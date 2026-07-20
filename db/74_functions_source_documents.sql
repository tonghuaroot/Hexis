-- Durable source document storage and deliberate full-text retrieval.
SET search_path = public, ag_catalog, "$user";

INSERT INTO config_defaults (key, value, description) VALUES
    ('memory.document_search_default_limit', '10'::jsonb,
     'Default row budget for source-document search'),
    ('memory.document_search_max_limit', '50'::jsonb,
     'Ceiling on source-document search rows; open_document retrieves exact content on demand'),
    ('memory.source_document_desk_chunk_chars', '8000'::jsonb,
     'Default chunk size when loading source documents onto the RecMem desk')
ON CONFLICT (key) DO NOTHING;

CREATE OR REPLACE FUNCTION upsert_source_document(
    p_title TEXT,
    p_source_type TEXT,
    p_content_hash TEXT,
    p_path TEXT,
    p_file_type TEXT,
    p_content TEXT,
    p_word_count INT DEFAULT 0,
    p_source_attribution JSONB DEFAULT '{}'::jsonb,
    p_metadata JSONB DEFAULT '{}'::jsonb
) RETURNS JSONB
LANGUAGE plpgsql
AS $$
DECLARE
    doc_id UUID;
    row_doc source_documents%ROWTYPE;
BEGIN
    IF NULLIF(trim(COALESCE(p_content_hash, '')), '') IS NULL THEN
        RAISE EXCEPTION 'source document content_hash is required';
    END IF;
    IF p_content IS NULL THEN
        RAISE EXCEPTION 'source document content is required';
    END IF;

    INSERT INTO source_documents (
        title,
        source_type,
        content_hash,
        path,
        file_type,
        content,
        word_count,
        size_bytes,
        source_attribution,
        metadata
    )
    VALUES (
        COALESCE(NULLIF(trim(p_title), ''), COALESCE(NULLIF(trim(p_path), ''), p_content_hash)),
        COALESCE(NULLIF(trim(p_source_type), ''), 'document'),
        p_content_hash,
        NULLIF(trim(COALESCE(p_path, '')), ''),
        NULLIF(trim(COALESCE(p_file_type, '')), ''),
        p_content,
        GREATEST(COALESCE(p_word_count, 0), 0),
        octet_length(p_content),
        COALESCE(p_source_attribution, '{}'::jsonb),
        COALESCE(p_metadata, '{}'::jsonb)
    )
    ON CONFLICT (content_hash) DO UPDATE
    SET title = CASE WHEN source_documents.status = 'redacted' THEN source_documents.title ELSE EXCLUDED.title END,
        source_type = CASE WHEN source_documents.status = 'redacted' THEN source_documents.source_type ELSE EXCLUDED.source_type END,
        path = CASE WHEN source_documents.status = 'redacted' THEN source_documents.path ELSE COALESCE(EXCLUDED.path, source_documents.path) END,
        file_type = CASE WHEN source_documents.status = 'redacted' THEN source_documents.file_type ELSE COALESCE(EXCLUDED.file_type, source_documents.file_type) END,
        content = CASE WHEN source_documents.status = 'redacted' THEN source_documents.content ELSE EXCLUDED.content END,
        word_count = CASE WHEN source_documents.status = 'redacted' THEN source_documents.word_count ELSE EXCLUDED.word_count END,
        size_bytes = CASE WHEN source_documents.status = 'redacted' THEN source_documents.size_bytes ELSE EXCLUDED.size_bytes END,
        source_attribution = CASE
            WHEN source_documents.status = 'redacted' THEN source_documents.source_attribution
            ELSE source_documents.source_attribution || EXCLUDED.source_attribution
        END,
        metadata = CASE
            WHEN source_documents.status = 'redacted' THEN source_documents.metadata
            ELSE source_documents.metadata || EXCLUDED.metadata
        END,
        updated_at = CURRENT_TIMESTAMP,
        last_ingested_at = CURRENT_TIMESTAMP,
        status = CASE
            WHEN source_documents.status = 'redacted' THEN source_documents.status
            ELSE 'active'
        END
    RETURNING id INTO doc_id;

    SELECT * INTO row_doc FROM source_documents WHERE id = doc_id;
    RETURN jsonb_build_object(
        'document_id', row_doc.id::text,
        'content_hash', row_doc.content_hash,
        'title', row_doc.title,
        'source_type', row_doc.source_type,
        'path', row_doc.path,
        'file_type', row_doc.file_type,
        'word_count', row_doc.word_count,
        'size_bytes', row_doc.size_bytes,
        'status', row_doc.status,
        'updated_at', row_doc.updated_at
    );
END;
$$;

CREATE OR REPLACE FUNCTION search_source_documents(
    p_query TEXT DEFAULT NULL,
    p_limit INT DEFAULT NULL,
    p_source_path TEXT DEFAULT NULL,
    p_source_type TEXT DEFAULT NULL,
    p_created_after TIMESTAMPTZ DEFAULT NULL,
    p_created_before TIMESTAMPTZ DEFAULT NULL,
    p_include_content BOOLEAN DEFAULT FALSE,
    p_offset INT DEFAULT 0,
    p_snippet_chars INT DEFAULT 500,
    p_exclude_sensitive BOOLEAN DEFAULT FALSE
) RETURNS TABLE (
    document_id UUID,
    title TEXT,
    source_type TEXT,
    path TEXT,
    file_type TEXT,
    content_hash TEXT,
    word_count INT,
    size_bytes INT,
    created_at TIMESTAMPTZ,
    updated_at TIMESTAMPTZ,
    rank FLOAT,
    snippet TEXT,
    content TEXT
)
LANGUAGE plpgsql
STABLE
AS $$
DECLARE
    query_text TEXT := NULLIF(trim(COALESCE(p_query, '')), '');
    browse_mode BOOLEAN := NULLIF(trim(COALESCE(p_query, '')), '') IS NULL
        OR trim(COALESCE(p_query, '')) = '*';
    lim INT := LEAST(
        GREATEST(COALESCE(
            p_limit,
            get_config_int('memory.document_search_default_limit'),
            10
        ), 1),
        GREATEST(COALESCE(get_config_int('memory.document_search_max_limit'), 50), 1)
    );
    offs INT := GREATEST(COALESCE(p_offset, 0), 0);
    snippet_limit INT := GREATEST(COALESCE(p_snippet_chars, 500), 80);
BEGIN
    IF browse_mode
       AND NULLIF(trim(COALESCE(p_source_path, '')), '') IS NULL
       AND NULLIF(trim(COALESCE(p_source_type, '')), '') IS NULL
       AND p_created_after IS NULL
       AND p_created_before IS NULL THEN
        RETURN;
    END IF;

    RETURN QUERY
    WITH query_doc AS (
        SELECT CASE WHEN browse_mode THEN NULL ELSE websearch_to_tsquery('english', query_text) END AS q
    ),
    candidates AS (
        SELECT
            d.id,
            d.title,
            d.source_type,
            d.path,
            d.file_type,
            d.content_hash,
            d.word_count,
            d.size_bytes,
            d.created_at,
            d.updated_at,
            CASE
                WHEN browse_mode OR numnode(q.q) = 0 THEN 0.0
                ELSE ts_rank_cd(
                    to_tsvector('english', d.title || ' ' || COALESCE(d.path, '') || ' ' || d.content),
                    q.q,
                    32
                )::FLOAT
            END AS rank,
            CASE
                WHEN p_include_content THEN d.content
                WHEN NOT browse_mode AND numnode(q.q) > 0 THEN left(
                    ts_headline(
                        'english',
                        d.content,
                        q.q,
                        'StartSel='''', StopSel='''', MaxWords=45, MinWords=12, ShortWord=3, MaxFragments=2, FragmentDelimiter=" ... "'
                    ),
                    snippet_limit
                )
                ELSE left(d.content, snippet_limit)
            END AS snippet,
            CASE WHEN p_include_content THEN d.content ELSE NULL::TEXT END AS content
        FROM source_documents d
        CROSS JOIN query_doc q
        WHERE d.status = 'active'
          AND (NOT COALESCE(p_exclude_sensitive, FALSE)
               OR COALESCE(d.source_attribution->>'sensitivity', '') <> 'private')
          AND (p_created_after IS NULL OR d.created_at >= p_created_after)
          AND (p_created_before IS NULL OR d.created_at < p_created_before)
          AND (NULLIF(trim(COALESCE(p_source_type, '')), '') IS NULL OR d.source_type = p_source_type)
          AND (
              NULLIF(trim(COALESCE(p_source_path, '')), '') IS NULL
              OR d.path ILIKE '%' || p_source_path || '%'
          )
          AND (
              browse_mode
              OR (
                  numnode(q.q) > 0
                  AND to_tsvector('english', d.title || ' ' || COALESCE(d.path, '') || ' ' || d.content) @@ q.q
              )
              OR d.title ILIKE '%' || query_text || '%'
              OR COALESCE(d.path, '') ILIKE '%' || query_text || '%'
          )
    )
    SELECT
        c.id,
        c.title,
        c.source_type,
        c.path,
        c.file_type,
        c.content_hash,
        c.word_count,
        c.size_bytes,
        c.created_at,
        c.updated_at,
        c.rank,
        c.snippet,
        c.content
    FROM candidates c
    ORDER BY c.rank DESC, c.updated_at DESC, c.id
    OFFSET offs
    LIMIT lim;
END;
$$;

CREATE OR REPLACE FUNCTION open_source_document(
    p_document_id UUID DEFAULT NULL,
    p_content_hash TEXT DEFAULT NULL,
    p_path TEXT DEFAULT NULL,
    p_offset INT DEFAULT 0,
    p_max_chars INT DEFAULT NULL,
    p_exclude_sensitive BOOLEAN DEFAULT FALSE
) RETURNS JSONB
LANGUAGE plpgsql
STABLE
AS $$
DECLARE
    doc source_documents%ROWTYPE;
    start_offset INT := GREATEST(COALESCE(p_offset, 0), 0);
    max_chars INT := p_max_chars;
    total_chars INT;
    body TEXT;
    truncated BOOLEAN;
BEGIN
    IF p_document_id IS NULL
       AND NULLIF(trim(COALESCE(p_content_hash, '')), '') IS NULL
       AND NULLIF(trim(COALESCE(p_path, '')), '') IS NULL THEN
        RETURN jsonb_build_object('error', 'missing_selector');
    END IF;

    SELECT *
    INTO doc
    FROM source_documents d
    WHERE d.status = 'active'
      AND (NOT COALESCE(p_exclude_sensitive, FALSE)
           OR COALESCE(d.source_attribution->>'sensitivity', '') <> 'private')
      AND (p_document_id IS NULL OR d.id = p_document_id)
      AND (NULLIF(trim(COALESCE(p_content_hash, '')), '') IS NULL OR d.content_hash = p_content_hash)
      AND (
          NULLIF(trim(COALESCE(p_path, '')), '') IS NULL
          OR d.path = p_path
          OR d.path ILIKE '%' || p_path || '%'
      )
    ORDER BY d.updated_at DESC, d.id
    LIMIT 1;

    IF NOT FOUND THEN
        RETURN jsonb_build_object('error', 'not_found');
    END IF;

    total_chars := length(doc.content);
    IF max_chars IS NULL OR max_chars <= 0 THEN
        body := substring(doc.content FROM start_offset + 1);
    ELSE
        body := substring(doc.content FROM start_offset + 1 FOR max_chars);
    END IF;
    truncated := start_offset + length(body) < total_chars;

    RETURN jsonb_build_object(
        'document_id', doc.id::text,
        'title', doc.title,
        'source_type', doc.source_type,
        'path', doc.path,
        'file_type', doc.file_type,
        'content_hash', doc.content_hash,
        'word_count', doc.word_count,
        'size_bytes', doc.size_bytes,
        'created_at', doc.created_at,
        'updated_at', doc.updated_at,
        'source_attribution', doc.source_attribution,
        'metadata', doc.metadata,
        'offset', start_offset,
        'max_chars', max_chars,
        'total_chars', total_chars,
        'returned_chars', length(body),
        'truncated', truncated,
        'next_offset', CASE WHEN truncated THEN start_offset + length(body) ELSE NULL END,
        'content', body
    );
END;
$$;

CREATE OR REPLACE FUNCTION open_source_documents(
    p_document_ids UUID[] DEFAULT NULL,
    p_content_hashes TEXT[] DEFAULT NULL,
    p_paths TEXT[] DEFAULT NULL,
    p_offset INT DEFAULT 0,
    p_max_chars INT DEFAULT NULL,
    p_limit INT DEFAULT NULL,
    p_exclude_sensitive BOOLEAN DEFAULT FALSE
) RETURNS JSONB
LANGUAGE plpgsql
STABLE
AS $$
DECLARE
    lim INT := LEAST(GREATEST(COALESCE(p_limit, 10), 1), 50);
    start_offset INT := GREATEST(COALESCE(p_offset, 0), 0);
    doc_ids UUID[] := ARRAY[]::UUID[];
    documents JSONB := '[]'::jsonb;
    total_matches INT := 0;
BEGIN
    IF COALESCE(array_length(p_document_ids, 1), 0) = 0
       AND COALESCE(array_length(p_content_hashes, 1), 0) = 0
       AND COALESCE(array_length(p_paths, 1), 0) = 0 THEN
        RETURN jsonb_build_object('error', 'missing_selector');
    END IF;

    WITH requested AS (
        SELECT ord::BIGINT AS ord, document_id, NULL::TEXT AS content_hash, NULL::TEXT AS path
        FROM unnest(COALESCE(p_document_ids, ARRAY[]::UUID[])) WITH ORDINALITY AS ids(document_id, ord)
        UNION ALL
        SELECT (100000 + ord)::BIGINT AS ord, NULL::UUID AS document_id, content_hash, NULL::TEXT AS path
        FROM unnest(COALESCE(p_content_hashes, ARRAY[]::TEXT[])) WITH ORDINALITY AS hashes(content_hash, ord)
        WHERE NULLIF(trim(COALESCE(content_hash, '')), '') IS NOT NULL
        UNION ALL
        SELECT (200000 + ord)::BIGINT AS ord, NULL::UUID AS document_id, NULL::TEXT AS content_hash, path
        FROM unnest(COALESCE(p_paths, ARRAY[]::TEXT[])) WITH ORDINALITY AS paths(path, ord)
        WHERE NULLIF(trim(COALESCE(path, '')), '') IS NOT NULL
    ),
    matched AS (
        SELECT
            d.id,
            MIN(r.ord) AS first_requested_at,
            MAX(d.updated_at) AS newest_updated_at,
            COUNT(*) OVER () AS total_count
        FROM requested r
        JOIN source_documents d ON d.status = 'active'
          AND (NOT COALESCE(p_exclude_sensitive, FALSE)
               OR COALESCE(d.source_attribution->>'sensitivity', '') <> 'private')
          AND (
              (r.document_id IS NOT NULL AND d.id = r.document_id)
              OR (NULLIF(trim(COALESCE(r.content_hash, '')), '') IS NOT NULL
                  AND d.content_hash = r.content_hash)
              OR (NULLIF(trim(COALESCE(r.path, '')), '') IS NOT NULL
                  AND (d.path = r.path OR d.path ILIKE '%' || r.path || '%'))
          )
        GROUP BY d.id
        ORDER BY first_requested_at, newest_updated_at DESC, d.id
        LIMIT lim
    )
    SELECT
        COALESCE(array_agg(id ORDER BY first_requested_at, newest_updated_at DESC, id), ARRAY[]::UUID[]),
        COALESCE(MAX(total_count), 0)
    INTO doc_ids, total_matches
    FROM matched;

    SELECT COALESCE(
        jsonb_agg(open_source_document(d.id, NULL, NULL, start_offset, p_max_chars, p_exclude_sensitive) ORDER BY d.ord),
        '[]'::jsonb
    )
    INTO documents
    FROM unnest(doc_ids) WITH ORDINALITY AS d(id, ord);

    RETURN jsonb_build_object(
        'documents', documents,
        'count', jsonb_array_length(documents),
        'total_matches', total_matches,
        'limit', lim,
        'offset', start_offset,
        'max_chars', p_max_chars
    );
END;
$$;

CREATE OR REPLACE FUNCTION load_source_documents_to_recmem(
    p_document_ids UUID[] DEFAULT NULL,
    p_content_hashes TEXT[] DEFAULT NULL,
    p_paths TEXT[] DEFAULT NULL,
    p_offset INT DEFAULT 0,
    p_max_chars INT DEFAULT NULL,
    p_chunk_chars INT DEFAULT NULL,
    p_limit INT DEFAULT NULL,
    p_exclude_sensitive BOOLEAN DEFAULT FALSE,
    p_reason TEXT DEFAULT NULL
) RETURNS JSONB
LANGUAGE plpgsql
AS $$
DECLARE
    lim INT := LEAST(GREATEST(COALESCE(p_limit, 10), 1), 50);
    start_offset INT := GREATEST(COALESCE(p_offset, 0), 0);
    chunk_chars INT := GREATEST(COALESCE(p_chunk_chars, get_config_int('memory.source_document_desk_chunk_chars'), 8000), 500);
    payload JSONB;
BEGIN
    IF COALESCE(array_length(p_document_ids, 1), 0) = 0
       AND COALESCE(array_length(p_content_hashes, 1), 0) = 0
       AND COALESCE(array_length(p_paths, 1), 0) = 0 THEN
        RETURN jsonb_build_object('error', 'missing_selector');
    END IF;

    WITH requested AS (
        SELECT ord::BIGINT AS ord, document_id, NULL::TEXT AS content_hash, NULL::TEXT AS path
        FROM unnest(COALESCE(p_document_ids, ARRAY[]::UUID[])) WITH ORDINALITY AS ids(document_id, ord)
        UNION ALL
        SELECT (100000 + ord)::BIGINT AS ord, NULL::UUID AS document_id, content_hash, NULL::TEXT AS path
        FROM unnest(COALESCE(p_content_hashes, ARRAY[]::TEXT[])) WITH ORDINALITY AS hashes(content_hash, ord)
        WHERE NULLIF(trim(COALESCE(content_hash, '')), '') IS NOT NULL
        UNION ALL
        SELECT (200000 + ord)::BIGINT AS ord, NULL::UUID AS document_id, NULL::TEXT AS content_hash, path
        FROM unnest(COALESCE(p_paths, ARRAY[]::TEXT[])) WITH ORDINALITY AS paths(path, ord)
        WHERE NULLIF(trim(COALESCE(path, '')), '') IS NOT NULL
    ),
    matched AS (
        SELECT
            d.*,
            MIN(r.ord) AS first_requested_at,
            COUNT(*) OVER () AS total_matches
        FROM requested r
        JOIN source_documents d ON d.status = 'active'
          AND (NOT COALESCE(p_exclude_sensitive, FALSE)
               OR COALESCE(d.source_attribution->>'sensitivity', '') <> 'private')
          AND (
              (r.document_id IS NOT NULL AND d.id = r.document_id)
              OR (NULLIF(trim(COALESCE(r.content_hash, '')), '') IS NOT NULL
                  AND d.content_hash = r.content_hash)
              OR (NULLIF(trim(COALESCE(r.path, '')), '') IS NOT NULL
                  AND (d.path = r.path OR d.path ILIKE '%' || r.path || '%'))
          )
        GROUP BY d.id
        ORDER BY first_requested_at, d.updated_at DESC, d.id
        LIMIT lim
    ),
    selected AS (
        SELECT
            m.*,
            substring(
                m.content FROM start_offset + 1
                FOR CASE WHEN p_max_chars IS NULL OR p_max_chars <= 0 THEN length(m.content)
                         ELSE p_max_chars END
            ) AS selected_content
        FROM matched m
    ),
    chunks AS (
        SELECT
            s.id AS document_id,
            s.title,
            s.source_type,
            s.path,
            s.file_type,
            s.content_hash,
            s.word_count,
            s.size_bytes,
            s.source_attribution AS document_source_attribution,
            s.total_matches,
            (chunk_start / chunk_chars)::INT AS chunk_index,
            start_offset + chunk_start AS chunk_offset,
            substring(s.selected_content FROM chunk_start + 1 FOR chunk_chars) AS chunk_content,
            length(s.content) AS total_chars
        FROM selected s
        CROSS JOIN LATERAL generate_series(
            0,
            GREATEST(length(s.selected_content) - 1, 0),
            chunk_chars
        ) AS g(chunk_start)
        WHERE length(s.selected_content) > 0
    ),
    upserted AS (
        INSERT INTO subconscious_units (
            source_identity,
            content,
            user_text,
            assistant_text,
            embedding_status,
            route_status,
            extraction_status,
            importance,
            source_attribution,
            metadata,
            idempotency_key,
            access_count,
            last_accessed
        )
        SELECT
            'source_document:' || c.document_id::text || ':' || c.chunk_offset::text,
            concat_ws(E'\n',
                '[Source Document: ' || c.title || ']',
                CASE WHEN c.path IS NOT NULL THEN '[Path: ' || c.path || ']' END,
                '[Document ID: ' || c.document_id::text || ']',
                '[Chunk: ' || c.chunk_index::text || ', chars '
                    || c.chunk_offset::text || '-'
                    || (c.chunk_offset + length(c.chunk_content))::text || ' of '
                    || c.total_chars::text || ']',
                '',
                c.chunk_content
            ),
            NULL,
            NULL,
            'failed',
            'raw_only',
            'skipped',
            0.2,
            jsonb_strip_nulls(jsonb_build_object(
                'kind', 'source_document_desk',
                'ref', c.content_hash,
                'label', c.title,
                'content_hash', c.content_hash,
                'path', c.path,
                'source_document_id', c.document_id::text,
                'document_id', c.document_id::text,
                'sensitivity', CASE WHEN c.document_source_attribution->>'sensitivity' = 'private' THEN 'private' END
            )),
            jsonb_build_object(
                'recmem', jsonb_strip_nulls(jsonb_build_object(
                    'kind', 'source_document_desk',
                    'loaded_at', CURRENT_TIMESTAMP,
                    'reason', NULLIF(trim(COALESCE(p_reason, '')), ''),
                    'document_id', c.document_id::text,
                    'title', c.title,
                    'path', c.path,
                    'content_hash', c.content_hash,
                    'chunk_index', c.chunk_index,
                    'offset', c.chunk_offset,
                    'end_offset', c.chunk_offset + length(c.chunk_content),
                    'chunk_chars', chunk_chars,
                    'total_matches', c.total_matches,
                    'embedding_skipped', true,
                    'routing_skipped', true,
                    'extraction_skipped', true
                ))
            ),
            'source_document_desk:' || c.document_id::text || ':' || c.chunk_offset::text || ':' || chunk_chars::text,
            1,
            CURRENT_TIMESTAMP
        FROM chunks c
        ON CONFLICT (idempotency_key) DO UPDATE
        SET status = 'active',
            access_count = subconscious_units.access_count + 1,
            last_accessed = CURRENT_TIMESTAMP,
            metadata = subconscious_units.metadata
                || jsonb_build_object(
                    'recmem',
                    COALESCE(subconscious_units.metadata->'recmem', '{}'::jsonb)
                    || COALESCE(EXCLUDED.metadata->'recmem', '{}'::jsonb)
                    || jsonb_build_object('last_loaded_at', CURRENT_TIMESTAMP)
                ),
            updated_at = CURRENT_TIMESTAMP
        RETURNING
            id,
            source_attribution,
            metadata,
            created_at,
            updated_at
    )
    SELECT jsonb_build_object(
        'loaded_units', COALESCE(jsonb_agg(jsonb_build_object(
            'unit_id', u.id::text,
            'document_id', u.source_attribution->>'source_document_id',
            'title', u.source_attribution->>'label',
            'path', u.source_attribution->>'path',
            'content_hash', u.source_attribution->>'content_hash',
            'chunk_index', NULLIF(u.metadata#>>'{recmem,chunk_index}', '')::INT,
            'offset', NULLIF(u.metadata#>>'{recmem,offset}', '')::INT,
            'end_offset', NULLIF(u.metadata#>>'{recmem,end_offset}', '')::INT
        ) ORDER BY u.source_attribution->>'label', NULLIF(u.metadata#>>'{recmem,offset}', '')::INT), '[]'::jsonb),
        'desk_unit_ids', COALESCE(jsonb_agg(u.id::text ORDER BY u.source_attribution->>'label', NULLIF(u.metadata#>>'{recmem,offset}', '')::INT), '[]'::jsonb),
        'count', COUNT(u.id),
        'limit', lim,
        'offset', start_offset,
        'chunk_chars', chunk_chars,
        'max_chars', p_max_chars,
        'total_matches', COALESCE(MAX((u.metadata#>>'{recmem,total_matches}')::INT), COUNT(u.id))
    )
    INTO payload
    FROM upserted u;

    RETURN COALESCE(payload, jsonb_build_object(
        'loaded_units', '[]'::jsonb,
        'desk_unit_ids', '[]'::jsonb,
        'count', 0,
        'limit', lim,
        'offset', start_offset,
        'chunk_chars', chunk_chars,
        'max_chars', p_max_chars
    ));
END;
$$;
