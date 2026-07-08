-- RecMem: recurrence-based memory consolidation.

CREATE OR REPLACE FUNCTION format_recmem_turn(
    p_user_text TEXT,
    p_assistant_text TEXT
) RETURNS TEXT AS $$
BEGIN
    RETURN format(
        'User: %s%sAssistant: %s',
        COALESCE(p_user_text, ''),
        E'\n\n',
        COALESCE(p_assistant_text, '')
    );
END;
$$ LANGUAGE plpgsql IMMUTABLE;

CREATE OR REPLACE FUNCTION normalize_recmem_text(
    p_text TEXT
) RETURNS TEXT AS $$
    SELECT regexp_replace(
        regexp_replace(
            regexp_replace(
                replace(replace(COALESCE($1, ''), E'\r\n', E'\n'), E'\r', E'\n'),
                '[ \t]+$', '', 'gm'
            ),
            '^\n+', ''
        ),
        '\n+$', ''
    );
$$ LANGUAGE sql IMMUTABLE;

CREATE OR REPLACE FUNCTION compute_recmem_idempotency_key(
    p_user_text TEXT,
    p_assistant_text TEXT,
    p_session_id UUID DEFAULT NULL,
    p_source_identity TEXT DEFAULT NULL
) RETURNS TEXT AS $$
DECLARE
    normalized TEXT;
BEGIN
    IF NULLIF(trim(COALESCE(p_source_identity, '')), '') IS NOT NULL THEN
        RETURN 'src:' || trim(p_source_identity);
    END IF;

    normalized := normalize_recmem_text(p_user_text)
        || chr(30)
        || normalize_recmem_text(p_assistant_text)
        || chr(30)
        || COALESCE(p_session_id::text, '');

    RETURN 'hash:' || encode(digest(normalized, 'sha256'), 'hex');
END;
$$ LANGUAGE plpgsql IMMUTABLE;

CREATE OR REPLACE FUNCTION recmem_ingest_turn(
    p_user_text TEXT,
    p_assistant_text TEXT,
    p_session_id UUID DEFAULT NULL,
    p_source_identity TEXT DEFAULT NULL,
    p_turn_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    p_importance FLOAT DEFAULT 0.3,
    p_source_attribution JSONB DEFAULT NULL,
    p_metadata JSONB DEFAULT '{}'::jsonb
) RETURNS JSONB AS $$
DECLARE
    unit_content TEXT;
    idem TEXT;
    new_id UUID;
    existing_id UUID;
BEGIN
    IF COALESCE(p_user_text, '') = '' AND COALESCE(p_assistant_text, '') = '' THEN
        RETURN jsonb_build_object('status', 'empty');
    END IF;

    unit_content := format_recmem_turn(p_user_text, p_assistant_text);
    idem := compute_recmem_idempotency_key(p_user_text, p_assistant_text, p_session_id, p_source_identity);

    INSERT INTO subconscious_units (
        session_id,
        source_identity,
        turn_at,
        content,
        user_text,
        assistant_text,
        importance,
        source_attribution,
        metadata,
        idempotency_key
    )
    VALUES (
        p_session_id,
        NULLIF(trim(COALESCE(p_source_identity, '')), ''),
        COALESCE(p_turn_at, CURRENT_TIMESTAMP),
        unit_content,
        COALESCE(p_user_text, ''),
        COALESCE(p_assistant_text, ''),
        LEAST(1.0, GREATEST(0.0, COALESCE(p_importance, 0.3))),
        COALESCE(p_source_attribution, '{}'::jsonb),
        COALESCE(p_metadata, '{}'::jsonb),
        idem
    )
    ON CONFLICT (idempotency_key) DO NOTHING
    RETURNING id INTO new_id;

    IF new_id IS NOT NULL THEN
        RETURN jsonb_build_object('unit_id', new_id, 'status', 'stored');
    END IF;

    SELECT id INTO existing_id
    FROM subconscious_units
    WHERE idempotency_key = idem;

    RETURN jsonb_build_object('unit_id', existing_id, 'status', 'duplicate');
END;
$$ LANGUAGE plpgsql;

CREATE OR REPLACE FUNCTION claim_recmem_unembedded_batch(
    p_limit INT DEFAULT 32,
    p_claim_timeout_s INT DEFAULT NULL
) RETURNS JSONB AS $$
DECLARE
    timeout_s INT := COALESCE(p_claim_timeout_s, get_config_int('memory.recmem_embed_claim_timeout_s'), 120);
    payload JSONB;
BEGIN
    WITH candidate AS (
        SELECT id
        FROM subconscious_units
        WHERE status = 'active'
          AND (
              embedding_status = 'pending'
              OR (
                  embedding_status = 'in_progress'
                  AND embedding_claimed_at < CURRENT_TIMESTAMP - (timeout_s * INTERVAL '1 second')
              )
          )
        ORDER BY created_at
        FOR UPDATE SKIP LOCKED
        LIMIT GREATEST(COALESCE(p_limit, 32), 1)
    ),
    claimed AS (
        UPDATE subconscious_units u
        SET embedding_status = 'in_progress',
            embedding_claimed_at = CURRENT_TIMESTAMP,
            embedding_attempts = embedding_attempts + 1,
            updated_at = CURRENT_TIMESTAMP
        FROM candidate c
        WHERE u.id = c.id
        RETURNING u.id, u.content, u.embedding_attempts
    )
    SELECT COALESCE(jsonb_agg(jsonb_build_object(
        'unit_id', id,
        'content', content,
        'attempts', embedding_attempts
    )), '[]'::jsonb)
    INTO payload
    FROM claimed;

    RETURN payload;
END;
$$ LANGUAGE plpgsql;

CREATE OR REPLACE FUNCTION apply_recmem_embeddings(
    p_payload JSONB
) RETURNS JSONB AS $$
DECLARE
    item JSONB;
    updated_count INT := 0;
    row_count INT := 0;
    emb_arr FLOAT4[];
BEGIN
    FOR item IN SELECT value FROM jsonb_array_elements(COALESCE(p_payload, '[]'::jsonb))
    LOOP
        SELECT array_agg(value::float4 ORDER BY ord)
        INTO emb_arr
        FROM jsonb_array_elements_text(item->'embedding') WITH ORDINALITY AS e(value, ord);

        IF emb_arr IS NULL OR array_length(emb_arr, 1) IS NULL THEN
            CONTINUE;
        END IF;

        UPDATE subconscious_units
        SET embedding = emb_arr::vector,
            embedded_at = CURRENT_TIMESTAMP,
            embedding_status = 'embedded',
            embedding_claimed_at = NULL,
            updated_at = CURRENT_TIMESTAMP
        WHERE id = (item->>'unit_id')::uuid
          AND embedding_status = 'in_progress';

        GET DIAGNOSTICS row_count = ROW_COUNT;
        updated_count := updated_count + row_count;
    END LOOP;

    RETURN jsonb_build_object('updated', updated_count);
END;
$$ LANGUAGE plpgsql;

CREATE OR REPLACE FUNCTION fail_recmem_embedding(
    p_unit_id UUID,
    p_error TEXT DEFAULT NULL
) RETURNS JSONB AS $$
DECLARE
    max_attempts INT := COALESCE(get_config_int('memory.recmem_embed_max_attempts'), 3);
    final_status TEXT;
BEGIN
    UPDATE subconscious_units
    SET embedding_status = CASE WHEN embedding_attempts >= max_attempts THEN 'failed' ELSE 'pending' END,
        embedding_claimed_at = NULL,
        metadata = COALESCE(metadata, '{}'::jsonb)
            || jsonb_build_object(
                'recmem',
                COALESCE(metadata->'recmem', '{}'::jsonb)
                    || jsonb_build_object(
                        'embedding_error',
                        jsonb_build_object('error', p_error, 'at', CURRENT_TIMESTAMP)
                    )
            ),
        updated_at = CURRENT_TIMESTAMP
    WHERE id = p_unit_id
    RETURNING embedding_status INTO final_status;

    RETURN jsonb_build_object('unit_id', p_unit_id, 'embedding_status', final_status);
END;
$$ LANGUAGE plpgsql;

CREATE OR REPLACE FUNCTION claim_recmem_unrouted_batch(
    p_limit INT DEFAULT 32,
    p_claim_timeout_s INT DEFAULT NULL
) RETURNS JSONB AS $$
DECLARE
    timeout_s INT := COALESCE(p_claim_timeout_s, get_config_int('memory.recmem_route_claim_timeout_s'), 60);
    payload JSONB;
BEGIN
    WITH candidate AS (
        SELECT id
        FROM subconscious_units
        WHERE status = 'active'
          AND embedding_status = 'embedded'
          AND (
              route_status = 'unrouted'
              OR (
                  route_status = 'routing'
                  AND last_routed_at < CURRENT_TIMESTAMP - (timeout_s * INTERVAL '1 second')
              )
          )
        ORDER BY last_routed_at NULLS FIRST, created_at
        FOR UPDATE SKIP LOCKED
        LIMIT GREATEST(COALESCE(p_limit, 32), 1)
    ),
    claimed AS (
        UPDATE subconscious_units u
        SET route_status = 'routing',
            route_attempts = route_attempts + 1,
            last_routed_at = CURRENT_TIMESTAMP,
            updated_at = CURRENT_TIMESTAMP
        FROM candidate c
        WHERE u.id = c.id
        RETURNING u.id, u.content, u.route_attempts
    )
    SELECT COALESCE(jsonb_agg(jsonb_build_object(
        'unit_id', id,
        'content', content,
        'attempts', route_attempts
    )), '[]'::jsonb)
    INTO payload
    FROM claimed;

    RETURN payload;
END;
$$ LANGUAGE plpgsql;

CREATE OR REPLACE FUNCTION _recmem_pending_queue_depth()
RETURNS INT AS $$
    SELECT COUNT(*)::int
    FROM recmem_consolidation_tasks
    WHERE status IN ('pending','in_progress');
$$ LANGUAGE sql STABLE;

CREATE OR REPLACE FUNCTION recmem_route_unit(
    p_unit_id UUID
) RETURNS JSONB AS $$
DECLARE
    unit_row subconscious_units%ROWTYPE;
    theta_sim FLOAT := COALESCE(get_config_float('memory.recmem_theta_sim'), 0.7);
    theta_merge FLOAT := COALESCE(get_config_float('memory.recmem_theta_sim_merge'), 0.78);
    theta_count INT := COALESCE(get_config_int('memory.recmem_theta_count'), 5);
    top_k INT := COALESCE(get_config_int('memory.recmem_top_k'), 20);
    queue_max INT := COALESCE(get_config_int('memory.recmem_queue_max'), 5000);
    nearest_memory_id UUID;
    nearest_similarity FLOAT;
    source_ids UUID[];
    recurrence_count INT;
    max_neighbor_similarity FLOAT;
    task_id UUID;
    overlaps_open_create BOOLEAN;
BEGIN
    SELECT * INTO unit_row
    FROM subconscious_units
    WHERE id = p_unit_id
      AND status = 'active'
      AND embedding_status = 'embedded';

    IF NOT FOUND THEN
        RETURN jsonb_build_object('unit_id', p_unit_id, 'status', 'skipped', 'reason', 'not_embedded_or_inactive');
    END IF;

    SELECT m.id, 1 - (m.embedding <=> unit_row.embedding)
    INTO nearest_memory_id, nearest_similarity
    FROM memories m
    WHERE m.status = 'active'
      AND (m.valid_until IS NULL OR m.valid_until > CURRENT_TIMESTAMP)
      AND m.type = 'episodic'
      AND m.embedding IS NOT NULL
    ORDER BY m.embedding <=> unit_row.embedding
    LIMIT 1;

    IF nearest_memory_id IS NOT NULL
       AND nearest_similarity >= theta_merge
       AND COALESCE(unit_row.route_result->>'merge_rejected_target_memory_id', '') <> nearest_memory_id::text THEN
        IF _recmem_pending_queue_depth() >= queue_max THEN
            UPDATE subconscious_units
            SET route_status = 'raw_only',
                route_result = route_result || jsonb_build_object(
                    'decision', 'raw_only',
                    'reason', 'queue_full',
                    'nearest_memory_id', nearest_memory_id,
                    'similarity', nearest_similarity
                ),
                updated_at = CURRENT_TIMESTAMP
            WHERE id = p_unit_id;
            RETURN jsonb_build_object('unit_id', p_unit_id, 'status', 'raw_only', 'reason', 'queue_full');
        END IF;

        SELECT id INTO task_id
        FROM recmem_consolidation_tasks
        WHERE task_type = 'episode_merge'
          AND status = 'pending'
          AND target_memory_id = nearest_memory_id
        ORDER BY created_at
        LIMIT 1
        FOR UPDATE;

        IF task_id IS NOT NULL THEN
            UPDATE recmem_consolidation_tasks
            SET source_unit_ids = (
                    SELECT array_agg(DISTINCT source_id)
                    FROM unnest(array_append(source_unit_ids, p_unit_id)) AS source_id
                ),
                task_payload = task_payload || jsonb_build_object('coalesced_at', CURRENT_TIMESTAMP),
                updated_at = CURRENT_TIMESTAMP
            WHERE id = task_id;
        ELSE
            INSERT INTO recmem_consolidation_tasks (
                task_type,
                trigger_unit_id,
                target_memory_id,
                source_unit_ids,
                max_similarity,
                task_payload
            )
            VALUES (
                'episode_merge',
                p_unit_id,
                nearest_memory_id,
                ARRAY[p_unit_id],
                nearest_similarity,
                jsonb_build_object(
                    'unit_content', unit_row.content,
                    'target_memory_id', nearest_memory_id,
                    'similarity', nearest_similarity
                )
            )
            RETURNING id INTO task_id;
        END IF;

        UPDATE subconscious_units
        SET route_status = 'merge_queued',
            route_result = route_result || jsonb_build_object(
                'decision', 'merge_queued',
                'task_id', task_id,
                'target_memory_id', nearest_memory_id,
                'similarity', nearest_similarity
            ),
            updated_at = CURRENT_TIMESTAMP
        WHERE id = p_unit_id;

        RETURN jsonb_build_object('unit_id', p_unit_id, 'status', 'merge_queued', 'task_id', task_id);
    END IF;

    WITH neighbors AS (
        SELECT s.id, 1 - (s.embedding <=> unit_row.embedding) AS similarity
        FROM subconscious_units s
        WHERE s.status = 'active'
          AND s.embedding_status = 'embedded'
          AND s.embedding IS NOT NULL
        ORDER BY s.embedding <=> unit_row.embedding
        LIMIT GREATEST(top_k, theta_count)
    ),
    recurrent AS (
        SELECT id, similarity
        FROM neighbors
        WHERE similarity >= theta_sim
    )
    SELECT array_agg(id ORDER BY id), COUNT(*)::int, MAX(similarity)
    INTO source_ids, recurrence_count, max_neighbor_similarity
    FROM recurrent;

    source_ids := COALESCE(source_ids, ARRAY[p_unit_id]);
    IF NOT p_unit_id = ANY(source_ids) THEN
        source_ids := source_ids || p_unit_id;
        recurrence_count := COALESCE(recurrence_count, 0) + 1;
    END IF;

    IF COALESCE(recurrence_count, 0) < theta_count THEN
        UPDATE subconscious_units
        SET route_status = 'raw_only',
            route_result = route_result || jsonb_build_object(
                'decision', 'raw_only',
                'recurrence_count', COALESCE(recurrence_count, 0),
                'max_similarity', max_neighbor_similarity
            ),
            updated_at = CURRENT_TIMESTAMP
        WHERE id = p_unit_id;
        RETURN jsonb_build_object('unit_id', p_unit_id, 'status', 'raw_only', 'recurrence_count', COALESCE(recurrence_count, 0));
    END IF;

    SELECT EXISTS (
        SELECT 1
        FROM recmem_consolidation_tasks t
        WHERE t.task_type = 'episode_create'
          AND t.status IN ('pending','in_progress')
          AND t.source_unit_ids && source_ids
    ) INTO overlaps_open_create;

    IF overlaps_open_create THEN
        UPDATE subconscious_units
        SET route_status = 'raw_only',
            route_result = route_result || jsonb_build_object(
                'decision', 'raw_only',
                'reason', 'open_create_overlap',
                'recurrence_count', recurrence_count
            ),
            updated_at = CURRENT_TIMESTAMP
        WHERE id = p_unit_id;
        RETURN jsonb_build_object('unit_id', p_unit_id, 'status', 'raw_only', 'reason', 'open_create_overlap');
    END IF;

    IF _recmem_pending_queue_depth() >= queue_max THEN
        UPDATE subconscious_units
        SET route_status = 'raw_only',
            route_result = route_result || jsonb_build_object(
                'decision', 'raw_only',
                'reason', 'queue_full_create_paused',
                'recurrence_count', recurrence_count
            ),
            updated_at = CURRENT_TIMESTAMP
        WHERE id = p_unit_id;
        RETURN jsonb_build_object('unit_id', p_unit_id, 'status', 'raw_only', 'reason', 'queue_full_create_paused');
    END IF;

    INSERT INTO recmem_consolidation_tasks (
        task_type,
        trigger_unit_id,
        source_unit_ids,
        recurrence_count,
        max_similarity,
        task_payload
    )
    VALUES (
        'episode_create',
        p_unit_id,
        source_ids,
        recurrence_count,
        max_neighbor_similarity,
        jsonb_build_object('source_unit_ids', source_ids, 'recurrence_count', recurrence_count)
    )
    RETURNING id INTO task_id;

    UPDATE subconscious_units
    SET route_status = 'create_queued',
        route_result = route_result || jsonb_build_object(
            'decision', 'create_queued',
            'task_id', task_id,
            'recurrence_count', recurrence_count,
            'max_similarity', max_neighbor_similarity
        ),
        updated_at = CURRENT_TIMESTAMP
    WHERE id = ANY(source_ids)
      AND route_status IN ('routing','raw_only','unrouted');

    RETURN jsonb_build_object('unit_id', p_unit_id, 'status', 'create_queued', 'task_id', task_id, 'recurrence_count', recurrence_count);
END;
$$ LANGUAGE plpgsql;

CREATE OR REPLACE FUNCTION fail_recmem_routing(
    p_unit_id UUID,
    p_error TEXT DEFAULT NULL
) RETURNS JSONB AS $$
DECLARE
    max_attempts INT := COALESCE(get_config_int('memory.recmem_route_max_attempts'), 3);
    final_status TEXT;
BEGIN
    UPDATE subconscious_units
    SET route_status = CASE WHEN route_attempts >= max_attempts THEN 'route_failed' ELSE 'unrouted' END,
        route_result = jsonb_build_object('error', p_error, 'at', CURRENT_TIMESTAMP),
        updated_at = CURRENT_TIMESTAMP
    WHERE id = p_unit_id
    RETURNING route_status INTO final_status;

    RETURN jsonb_build_object('unit_id', p_unit_id, 'route_status', final_status);
END;
$$ LANGUAGE plpgsql;

CREATE OR REPLACE FUNCTION claim_recmem_consolidation_task(
    p_claim_timeout_s INT DEFAULT NULL
) RETURNS JSONB AS $$
DECLARE
    timeout_s INT := COALESCE(p_claim_timeout_s, get_config_int('memory.recmem_task_claim_timeout_s'), 600);
    task JSONB;
BEGIN
    WITH candidate AS (
        SELECT id
        FROM recmem_consolidation_tasks
        WHERE (status = 'pending' AND next_attempt_at <= CURRENT_TIMESTAMP)
           OR (status = 'in_progress' AND started_at < CURRENT_TIMESTAMP - (timeout_s * INTERVAL '1 second'))
        ORDER BY next_attempt_at, created_at
        FOR UPDATE SKIP LOCKED
        LIMIT 1
    ),
    claimed AS (
        UPDATE recmem_consolidation_tasks t
        SET status = 'in_progress',
            started_at = CURRENT_TIMESTAMP,
            attempts = attempts + 1,
            updated_at = CURRENT_TIMESTAMP
        FROM candidate c
        WHERE t.id = c.id
        RETURNING t.*
    )
    SELECT to_jsonb(claimed)
    INTO task
    FROM claimed;

    RETURN task;
END;
$$ LANGUAGE plpgsql;

CREATE OR REPLACE FUNCTION fail_recmem_consolidation_task(
    p_task_id UUID,
    p_error TEXT DEFAULT NULL
) RETURNS JSONB AS $$
DECLARE
    max_attempts INT := COALESCE(get_config_int('memory.recmem_task_max_attempts'), 3);
    backoff_base INT := COALESCE(get_config_int('memory.recmem_task_backoff_base_s'), 30);
    task recmem_consolidation_tasks%ROWTYPE;
BEGIN
    SELECT * INTO task
    FROM recmem_consolidation_tasks
    WHERE id = p_task_id
    FOR UPDATE;

    IF NOT FOUND THEN
        RETURN jsonb_build_object('task_id', p_task_id, 'status', 'missing');
    END IF;

    IF task.attempts >= max_attempts THEN
        UPDATE recmem_consolidation_tasks
        SET status = 'failed',
            error = p_error,
            completed_at = CURRENT_TIMESTAMP,
            updated_at = CURRENT_TIMESTAMP
        WHERE id = p_task_id;
        RETURN jsonb_build_object('task_id', p_task_id, 'status', 'failed');
    END IF;

    UPDATE recmem_consolidation_tasks
    SET status = 'pending',
        started_at = NULL,
        next_attempt_at = CURRENT_TIMESTAMP + (backoff_base * power(2, GREATEST(attempts - 1, 0))) * INTERVAL '1 second',
        error = p_error,
        updated_at = CURRENT_TIMESTAMP
    WHERE id = p_task_id;

    RETURN jsonb_build_object('task_id', p_task_id, 'status', 'pending');
END;
$$ LANGUAGE plpgsql;

CREATE OR REPLACE FUNCTION link_memory_to_source_unit(
    p_memory_id UUID,
    p_unit_id UUID,
    p_role TEXT DEFAULT 'source'
) RETURNS BOOLEAN AS $$
BEGIN
    INSERT INTO memory_source_units (memory_id, subconscious_unit_id, role)
    VALUES (p_memory_id, p_unit_id, COALESCE(p_role, 'source'))
    ON CONFLICT (memory_id, subconscious_unit_id) DO UPDATE
    SET role = EXCLUDED.role;

    RETURN TRUE;
END;
$$ LANGUAGE plpgsql;

CREATE OR REPLACE FUNCTION apply_recmem_episode_merge(
    p_task_id UUID,
    p_merged_content TEXT DEFAULT NULL,
    p_should_merge BOOLEAN DEFAULT TRUE
) RETURNS JSONB AS $$
DECLARE
    task recmem_consolidation_tasks%ROWTYPE;
    old_content TEXT;
    new_embedding vector;
    unit_id UUID;
    queue_max INT := COALESCE(get_config_int('memory.recmem_queue_max'), 5000);
BEGIN
    SELECT * INTO task
    FROM recmem_consolidation_tasks
    WHERE id = p_task_id
    FOR UPDATE;

    IF NOT FOUND THEN
        RETURN jsonb_build_object('task_id', p_task_id, 'status', 'missing');
    END IF;

    IF NOT COALESCE(p_should_merge, TRUE) THEN
        UPDATE subconscious_units
        SET route_status = 'routing',
            route_result = route_result || jsonb_build_object(
                'merge_rejected', true,
                'merge_rejected_target_memory_id', task.target_memory_id,
                'at', CURRENT_TIMESTAMP
            ),
            updated_at = CURRENT_TIMESTAMP
        WHERE id = ANY(task.source_unit_ids);

        FOREACH unit_id IN ARRAY task.source_unit_ids LOOP
            PERFORM recmem_route_unit(unit_id);
        END LOOP;

        UPDATE recmem_consolidation_tasks
        SET status = 'completed',
            completed_at = CURRENT_TIMESTAMP,
            result = jsonb_build_object('merged', false),
            updated_at = CURRENT_TIMESTAMP
        WHERE id = p_task_id;

        RETURN jsonb_build_object('task_id', p_task_id, 'status', 'completed', 'merged', false);
    END IF;

    SELECT content INTO old_content
    FROM memories
    WHERE id = task.target_memory_id;

    IF task.target_memory_id IS NULL OR old_content IS NULL THEN
        PERFORM fail_recmem_consolidation_task(p_task_id, 'target memory missing');
        RETURN jsonb_build_object('task_id', p_task_id, 'status', 'failed', 'reason', 'target_missing');
    END IF;

    new_embedding := (get_embedding(ARRAY[COALESCE(NULLIF(p_merged_content, ''), old_content)]))[1];

    UPDATE memories
    SET content = COALESCE(NULLIF(p_merged_content, ''), old_content),
        embedding = new_embedding,
        metadata = COALESCE(metadata, '{}'::jsonb)
            || jsonb_build_object(
                'recmem',
                COALESCE(metadata->'recmem', '{}'::jsonb)
                    || jsonb_build_object(
                        'merge_history',
                        COALESCE(metadata#>'{recmem,merge_history}', '[]'::jsonb)
                            || jsonb_build_array(jsonb_build_object('content', old_content, 'merged_at', CURRENT_TIMESTAMP))
                    )
            ),
        updated_at = CURRENT_TIMESTAMP
    WHERE id = task.target_memory_id;

    FOREACH unit_id IN ARRAY task.source_unit_ids LOOP
        PERFORM link_memory_to_source_unit(task.target_memory_id, unit_id, 'merge_addition');
    END LOOP;

    UPDATE subconscious_units
    SET consolidated_at = CURRENT_TIMESTAMP,
        route_status = 'merged',
        updated_at = CURRENT_TIMESTAMP
    WHERE id = ANY(task.source_unit_ids);

    IF _recmem_pending_queue_depth() >= queue_max THEN
        INSERT INTO recmem_consolidation_tasks (
            task_type,
            trigger_unit_id,
            target_memory_id,
            source_unit_ids,
            status,
            completed_at,
            dropped_reason,
            task_payload
        )
        VALUES (
            'semantic_refine',
            task.trigger_unit_id,
            task.target_memory_id,
            task.source_unit_ids,
            'dropped',
            CURRENT_TIMESTAMP,
            'queue_full_semantic_refine_dropped',
            jsonb_build_object('reason', 'episode_merge')
        );
    ELSE
        INSERT INTO recmem_consolidation_tasks (task_type, trigger_unit_id, target_memory_id, source_unit_ids, task_payload)
        VALUES ('semantic_refine', task.trigger_unit_id, task.target_memory_id, task.source_unit_ids, jsonb_build_object('reason', 'episode_merge'));
    END IF;

    UPDATE recmem_consolidation_tasks
    SET status = 'completed',
        completed_at = CURRENT_TIMESTAMP,
        result = jsonb_build_object('merged', true, 'target_memory_id', task.target_memory_id),
        updated_at = CURRENT_TIMESTAMP
    WHERE id = p_task_id;

    RETURN jsonb_build_object('task_id', p_task_id, 'status', 'completed', 'merged', true, 'target_memory_id', task.target_memory_id);
END;
$$ LANGUAGE plpgsql;

CREATE OR REPLACE FUNCTION apply_recmem_episode_create(
    p_task_id UUID,
    p_episodes JSONB
) RETURNS JSONB AS $$
DECLARE
    task recmem_consolidation_tasks%ROWTYPE;
    item JSONB;
    episode_content TEXT;
    new_embedding vector;
    memory_id UUID;
    created_ids UUID[] := ARRAY[]::UUID[];
    unit_id UUID;
    source_attr JSONB;
    queue_max INT := COALESCE(get_config_int('memory.recmem_queue_max'), 5000);
BEGIN
    SELECT * INTO task
    FROM recmem_consolidation_tasks
    WHERE id = p_task_id
    FOR UPDATE;

    IF NOT FOUND THEN
        RETURN jsonb_build_object('task_id', p_task_id, 'status', 'missing');
    END IF;

    source_attr := jsonb_build_object(
        'kind', 'recmem',
        'ref', task.id::text,
        'label', 'RecMem episodic consolidation',
        'observed_at', CURRENT_TIMESTAMP,
        'trust', 0.9
    );

    FOR item IN SELECT value FROM jsonb_array_elements(COALESCE(p_episodes, '[]'::jsonb))
    LOOP
        episode_content := COALESCE(item->>'content', item->>'episode', item#>>'{}');
        IF NULLIF(trim(COALESCE(episode_content, '')), '') IS NULL THEN
            CONTINUE;
        END IF;

        new_embedding := (get_embedding(ARRAY[episode_content]))[1];
        memory_id := create_memory_with_embedding(
            'episodic',
            episode_content,
            new_embedding,
            COALESCE(NULLIF(item->>'importance', '')::float, 0.6),
            source_attr,
            0.9,
            jsonb_build_object('recmem', jsonb_build_object('task_id', task.id, 'source_unit_ids', task.source_unit_ids))
        );
        created_ids := created_ids || memory_id;

        FOREACH unit_id IN ARRAY task.source_unit_ids LOOP
            PERFORM link_memory_to_source_unit(memory_id, unit_id, 'source');
        END LOOP;

        IF _recmem_pending_queue_depth() >= queue_max THEN
            INSERT INTO recmem_consolidation_tasks (
                task_type,
                trigger_unit_id,
                target_memory_id,
                source_unit_ids,
                status,
                completed_at,
                dropped_reason,
                task_payload
            )
            VALUES (
                'semantic_refine',
                task.trigger_unit_id,
                memory_id,
                task.source_unit_ids,
                'dropped',
                CURRENT_TIMESTAMP,
                'queue_full_semantic_refine_dropped',
                jsonb_build_object('reason', 'episode_create')
            );
        ELSE
            INSERT INTO recmem_consolidation_tasks (task_type, trigger_unit_id, target_memory_id, source_unit_ids, task_payload)
            VALUES ('semantic_refine', task.trigger_unit_id, memory_id, task.source_unit_ids, jsonb_build_object('reason', 'episode_create'));
        END IF;
    END LOOP;

    IF cardinality(created_ids) = 0 THEN
        UPDATE subconscious_units
        SET route_status = 'raw_only',
            route_result = route_result || jsonb_build_object(
                'episode_create_empty', true,
                'task_id', p_task_id,
                'at', CURRENT_TIMESTAMP
            ),
            updated_at = CURRENT_TIMESTAMP
        WHERE id = ANY(task.source_unit_ids);

        UPDATE recmem_consolidation_tasks
        SET status = 'completed',
            completed_at = CURRENT_TIMESTAMP,
            result = jsonb_build_object('memory_ids', created_ids, 'empty', true),
            updated_at = CURRENT_TIMESTAMP
        WHERE id = p_task_id;

        RETURN jsonb_build_object('task_id', p_task_id, 'status', 'completed', 'memory_ids', created_ids, 'empty', true);
    END IF;

    UPDATE subconscious_units
    SET consolidated_at = CURRENT_TIMESTAMP,
        route_status = 'episode_created',
        updated_at = CURRENT_TIMESTAMP
    WHERE id = ANY(task.source_unit_ids);

    UPDATE recmem_consolidation_tasks
    SET status = 'completed',
        completed_at = CURRENT_TIMESTAMP,
        result = jsonb_build_object('memory_ids', created_ids),
        updated_at = CURRENT_TIMESTAMP
    WHERE id = p_task_id;

    RETURN jsonb_build_object('task_id', p_task_id, 'status', 'completed', 'memory_ids', created_ids);
END;
$$ LANGUAGE plpgsql;

CREATE OR REPLACE FUNCTION apply_recmem_semantic_facts(
    p_task_id UUID,
    p_facts JSONB
) RETURNS JSONB AS $$
DECLARE
    task recmem_consolidation_tasks%ROWTYPE;
    item JSONB;
    fact_content TEXT;
    fact_embedding vector;
    duplicate_id UUID;
    memory_id UUID;
    created_ids UUID[] := ARRAY[]::UUID[];
    unit_id UUID;
BEGIN
    SELECT * INTO task
    FROM recmem_consolidation_tasks
    WHERE id = p_task_id
    FOR UPDATE;

    IF NOT FOUND THEN
        RETURN jsonb_build_object('task_id', p_task_id, 'status', 'missing');
    END IF;

    FOR item IN SELECT value FROM jsonb_array_elements(COALESCE(p_facts, '[]'::jsonb))
    LOOP
        fact_content := COALESCE(item->>'content', item->>'fact', item#>>'{}');
        IF NULLIF(trim(COALESCE(fact_content, '')), '') IS NULL THEN
            CONTINUE;
        END IF;

        fact_embedding := (get_embedding(ARRAY[fact_content]))[1];

        SELECT m.id INTO duplicate_id
        FROM memories m
        WHERE m.status = 'active'
          AND (m.valid_until IS NULL OR m.valid_until > CURRENT_TIMESTAMP)
          AND m.type = 'semantic'
          AND 1 - (m.embedding <=> fact_embedding) >= 0.92
        ORDER BY m.embedding <=> fact_embedding
        LIMIT 1;

        IF duplicate_id IS NOT NULL THEN
            CONTINUE;
        END IF;

        memory_id := create_memory_with_embedding(
            'semantic',
            fact_content,
            fact_embedding,
            COALESCE(NULLIF(item->>'importance', '')::float, 0.55),
            jsonb_build_object(
                'kind', 'recmem',
                'ref', task.id::text,
                'label', 'RecMem semantic refinement',
                'observed_at', CURRENT_TIMESTAMP,
                'trust', 0.85
            ),
            0.85,
            jsonb_build_object('recmem', jsonb_build_object('task_id', task.id, 'episode_id', task.target_memory_id, 'source_unit_ids', task.source_unit_ids))
        );
        created_ids := created_ids || memory_id;

        FOREACH unit_id IN ARRAY task.source_unit_ids LOOP
            PERFORM link_memory_to_source_unit(memory_id, unit_id, 'source');
        END LOOP;

        IF task.target_memory_id IS NOT NULL THEN
            PERFORM create_memory_relationship(memory_id, task.target_memory_id, 'DERIVED_FROM', '{}'::jsonb);
        END IF;
    END LOOP;

    UPDATE recmem_consolidation_tasks
    SET status = 'completed',
        completed_at = CURRENT_TIMESTAMP,
        result = jsonb_build_object('memory_ids', created_ids),
        updated_at = CURRENT_TIMESTAMP
    WHERE id = p_task_id;

    RETURN jsonb_build_object('task_id', p_task_id, 'status', 'completed', 'memory_ids', created_ids);
END;
$$ LANGUAGE plpgsql;

CREATE OR REPLACE FUNCTION recmem_recall_context(
    p_query TEXT,
    p_k_sub INT DEFAULT 10,
    p_k_epi INT DEFAULT 5,
    p_k_sem INT DEFAULT 10,
    p_session_id UUID DEFAULT NULL
) RETURNS TABLE (
    tier TEXT,
    item_id UUID,
    content TEXT,
    memory_type TEXT,
    score FLOAT,
    source_unit_ids UUID[],
    source_attribution JSONB,
    created_at TIMESTAMPTZ,
    trust_level FLOAT,
    fidelity FLOAT,
    strength FLOAT
) AS $$
DECLARE
    query_embedding vector;
    strength_weight FLOAT;
BEGIN
    query_embedding := (get_embedding(ARRAY[ensure_embedding_prefix(p_query, 'search_query')]))[1];
    -- How much computed memory strength (recency/reinforcement/decay) reshapes
    -- the pure-cosine recall score: 0 = pure similarity (old behavior),
    -- 0.5 = gentle default, 1 = score fully scaled by strength.
    strength_weight := LEAST(1.0, GREATEST(0.0, COALESCE(get_config_float('memory.recall_strength_weight'), 0.5)));

    RETURN QUERY
    WITH raw_hits AS (
        SELECT
            'subconscious'::text AS tier,
            s.id AS item_id,
            s.content,
            NULL::text AS memory_type,
            (1 - (s.embedding <=> query_embedding))::float AS score,
            ARRAY[s.id]::uuid[] AS source_unit_ids,
            s.source_attribution,
            s.created_at,
            s.trust_level,
            1.0::float AS fidelity,
            1.0::float AS strength
        FROM subconscious_units s
        WHERE s.status = 'active'
          AND s.embedding_status = 'embedded'
          AND s.embedding IS NOT NULL
        ORDER BY s.embedding <=> query_embedding
        LIMIT GREATEST(COALESCE(p_k_sub, 10), 0)
    ),
    recent_unembedded AS (
        SELECT
            'subconscious'::text AS tier,
            s.id AS item_id,
            s.content,
            NULL::text AS memory_type,
            0.2::float AS score,
            ARRAY[s.id]::uuid[] AS source_unit_ids,
            s.source_attribution,
            s.created_at,
            s.trust_level,
            1.0::float AS fidelity,
            1.0::float AS strength
        FROM subconscious_units s
        WHERE p_session_id IS NOT NULL
          AND s.session_id = p_session_id
          AND s.status = 'active'
          AND s.embedding_status <> 'embedded'
        ORDER BY s.created_at DESC
        LIMIT 3
    ),
    epi_hits AS (
        SELECT
            'episodic'::text AS tier,
            m.id AS item_id,
            m.content,
            m.type::text AS memory_type,
            ((1 - (m.embedding <=> query_embedding))
             * (1.0 - strength_weight + strength_weight
                * calculate_strength(m.importance, m.decay_rate, m.created_at, m.last_reinforced)))::float AS score,
            COALESCE(array_agg(msu.subconscious_unit_id) FILTER (WHERE msu.subconscious_unit_id IS NOT NULL), '{}'::uuid[]) AS source_unit_ids,
            m.source_attribution,
            m.created_at,
            m.trust_level,
            m.fidelity,
            calculate_strength(m.importance, m.decay_rate, m.created_at, m.last_reinforced)::float AS strength
        FROM memories m
        LEFT JOIN memory_source_units msu ON msu.memory_id = m.id
        WHERE m.status = 'active'
          AND (m.valid_until IS NULL OR m.valid_until > CURRENT_TIMESTAMP)
          AND m.type = 'episodic'
        GROUP BY m.id
        ORDER BY m.embedding <=> query_embedding
        LIMIT GREATEST(COALESCE(p_k_epi, 5), 0)
    ),
    sem_hits AS (
        SELECT
            'semantic'::text AS tier,
            m.id AS item_id,
            m.content,
            m.type::text AS memory_type,
            ((1 - (m.embedding <=> query_embedding))
             * (1.0 - strength_weight + strength_weight
                * calculate_strength(m.importance, m.decay_rate, m.created_at, m.last_reinforced)))::float AS score,
            COALESCE(array_agg(msu.subconscious_unit_id) FILTER (WHERE msu.subconscious_unit_id IS NOT NULL), '{}'::uuid[]) AS source_unit_ids,
            m.source_attribution,
            m.created_at,
            m.trust_level,
            m.fidelity,
            calculate_strength(m.importance, m.decay_rate, m.created_at, m.last_reinforced)::float AS strength
        FROM memories m
        LEFT JOIN memory_source_units msu ON msu.memory_id = m.id
        WHERE m.status = 'active'
          AND (m.valid_until IS NULL OR m.valid_until > CURRENT_TIMESTAMP)
          AND m.type = 'semantic'
        GROUP BY m.id
        ORDER BY m.embedding <=> query_embedding
        LIMIT GREATEST(COALESCE(p_k_sem, 10), 0)
    )
    SELECT * FROM raw_hits
    UNION ALL
    SELECT * FROM recent_unembedded
    UNION ALL
    SELECT * FROM epi_hits
    UNION ALL
    SELECT * FROM sem_hits
    ORDER BY tier, score DESC, created_at DESC;
END;
$$ LANGUAGE plpgsql;

CREATE OR REPLACE FUNCTION recmem_periodic_sweep(
    p_limit INT DEFAULT NULL
) RETURNS JSONB AS $$
DECLARE
    sweep_limit INT := COALESCE(p_limit, get_config_int('memory.recmem_sweep_batch_size'), 100);
    min_age_days INT := COALESCE(get_config_int('memory.recmem_sweep_min_rerouting_age_days'), 7);
    unit_id UUID;
    processed INT := 0;
BEGIN
    FOR unit_id IN
        SELECT id
        FROM subconscious_units
        WHERE status = 'active'
          AND embedding_status = 'embedded'
          AND route_status = 'raw_only'
          AND consolidated_at IS NULL
          AND (last_routed_at IS NULL OR last_routed_at < CURRENT_TIMESTAMP - (min_age_days * INTERVAL '1 day'))
        ORDER BY created_at
        LIMIT sweep_limit
    LOOP
        UPDATE subconscious_units
        SET route_status = 'routing',
            last_routed_at = CURRENT_TIMESTAMP,
            route_attempts = route_attempts + 1,
            updated_at = CURRENT_TIMESTAMP
        WHERE id = unit_id;
        PERFORM recmem_route_unit(unit_id);
        processed := processed + 1;
    END LOOP;

    RETURN jsonb_build_object('processed', processed);
END;
$$ LANGUAGE plpgsql;

CREATE OR REPLACE FUNCTION should_run_recmem_sweep()
RETURNS BOOLEAN AS $$
DECLARE
    state_doc JSONB := COALESCE(get_state('recmem_state'), '{}'::jsonb);
    last_run TIMESTAMPTZ := NULLIF(state_doc->>'last_sweep_at', '')::timestamptz;
    interval_seconds FLOAT := COALESCE(get_config_float('memory.recmem_sweep_interval_seconds'), 86400);
BEGIN
    IF last_run IS NULL THEN
        RETURN TRUE;
    END IF;

    RETURN CURRENT_TIMESTAMP >= last_run + (interval_seconds || ' seconds')::interval;
END;
$$ LANGUAGE plpgsql STABLE;

CREATE OR REPLACE FUNCTION mark_recmem_sweep_run(
    p_result JSONB DEFAULT '{}'::jsonb
) RETURNS JSONB AS $$
DECLARE
    merged JSONB;
BEGIN
    merged := COALESCE(get_state('recmem_state'), '{}'::jsonb)
        || jsonb_build_object(
            'last_sweep_at', CURRENT_TIMESTAMP,
            'last_sweep_result', COALESCE(p_result, '{}'::jsonb)
        );

    PERFORM set_state('recmem_state', merged);
    RETURN merged;
END;
$$ LANGUAGE plpgsql;

CREATE OR REPLACE FUNCTION recmem_unhealthy_items()
RETURNS TABLE (
    kind TEXT,
    item_id UUID,
    item_status TEXT,
    attempts INT,
    last_seen TIMESTAMPTZ,
    error TEXT,
    extra JSONB
) AS $$
    SELECT
        'embedding'::text,
        id,
        embedding_status,
        embedding_attempts,
        COALESCE(embedding_claimed_at, updated_at, created_at),
        metadata#>>'{recmem,embedding_error,error}',
        metadata
    FROM subconscious_units
    WHERE embedding_status = 'failed'
    UNION ALL
    SELECT
        'routing'::text,
        id,
        route_status,
        route_attempts,
        COALESCE(last_routed_at, updated_at, created_at),
        route_result->>'error',
        route_result
    FROM subconscious_units
    WHERE route_status = 'route_failed'
    UNION ALL
    SELECT
        'task'::text,
        id,
        status,
        attempts,
        COALESCE(completed_at, started_at, updated_at, created_at),
        error,
        task_payload
    FROM recmem_consolidation_tasks
    WHERE status = 'failed'
    UNION ALL
    SELECT
        'task'::text,
        id,
        status,
        attempts,
        COALESCE(completed_at, started_at, updated_at, created_at),
        dropped_reason,
        task_payload
    FROM recmem_consolidation_tasks
    WHERE status = 'dropped';
$$ LANGUAGE sql STABLE;

CREATE OR REPLACE FUNCTION recmem_redact_unit(
    p_unit_id UUID,
    p_reason TEXT DEFAULT NULL,
    p_cascade_invalidate BOOLEAN DEFAULT TRUE
) RETURNS JSONB AS $$
DECLARE
    invalidated_ids UUID[] := ARRAY[]::UUID[];
BEGIN
    UPDATE subconscious_units
    SET status = 'redacted',
        metadata = jsonb_set(
            COALESCE(metadata, '{}'::jsonb),
            '{redaction}',
            jsonb_build_object('reason', p_reason, 'at', CURRENT_TIMESTAMP),
            true
        ),
        updated_at = CURRENT_TIMESTAMP
    WHERE id = p_unit_id;

    IF COALESCE(p_cascade_invalidate, TRUE) THEN
        WITH linked AS (
            SELECT DISTINCT memory_id
            FROM memory_source_units
            WHERE subconscious_unit_id = p_unit_id
        ),
        updated AS (
            UPDATE memories m
            SET valid_until = CURRENT_TIMESTAMP,
                metadata = COALESCE(m.metadata, '{}'::jsonb)
                    || jsonb_build_object(
                        'recmem',
                        COALESCE(m.metadata->'recmem', '{}'::jsonb)
                            || jsonb_build_object(
                                'invalidation',
                                jsonb_build_object(
                                    'reason', 'source_redacted',
                                    'source_unit_id', p_unit_id,
                                    'detail', p_reason,
                                    'at', CURRENT_TIMESTAMP
                                )
                            )
                    ),
                updated_at = CURRENT_TIMESTAMP
            FROM linked l
            WHERE m.id = l.memory_id
            RETURNING m.id
        )
        SELECT COALESCE(array_agg(id), '{}'::uuid[])
        INTO invalidated_ids
        FROM updated;
    END IF;

    RETURN jsonb_build_object('redacted_unit_id', p_unit_id, 'invalidated_memory_ids', invalidated_ids);
END;
$$ LANGUAGE plpgsql;


CREATE OR REPLACE FUNCTION has_pending_recmem_consolidation()
RETURNS BOOLEAN AS $$
    SELECT EXISTS (
        SELECT 1
        FROM recmem_consolidation_tasks
        WHERE status = 'pending'
          AND next_attempt_at <= CURRENT_TIMESTAMP
    );
$$ LANGUAGE sql STABLE;
