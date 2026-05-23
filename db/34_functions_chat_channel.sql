-- DB-owned chat and channel turn lifecycle.
SET search_path = public, ag_catalog, "$user";

CREATE OR REPLACE FUNCTION _db_brain_try_uuid(p_value TEXT)
RETURNS UUID
LANGUAGE plpgsql
IMMUTABLE
AS $$
BEGIN
    IF p_value IS NULL OR p_value !~* '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$' THEN
        RETURN NULL;
    END IF;
    RETURN p_value::uuid;
EXCEPTION WHEN invalid_text_representation THEN
    RETURN NULL;
END;
$$;

CREATE OR REPLACE FUNCTION estimate_conversation_importance(
    p_user_text TEXT,
    p_assistant_text TEXT,
    p_baseline FLOAT DEFAULT 0.5
) RETURNS FLOAT
LANGUAGE plpgsql
IMMUTABLE
AS $$
DECLARE
    combined TEXT := lower(COALESCE(p_user_text, '') || E'\n' || COALESCE(p_assistant_text, ''));
    importance FLOAT := COALESCE(p_baseline, 0.5);
    signal TEXT;
    signals TEXT[] := ARRAY[
        'remember',
        'don''t forget',
        'important',
        'note that',
        'my name is',
        'i prefer',
        'i like',
        'i don''t like',
        'always',
        'never',
        'make sure',
        'keep in mind'
    ];
BEGIN
    IF length(COALESCE(p_user_text, '')) > 200 OR length(COALESCE(p_assistant_text, '')) > 500 THEN
        importance := GREATEST(importance, 0.7);
    END IF;

    FOREACH signal IN ARRAY signals LOOP
        IF position(signal IN combined) > 0 THEN
            importance := GREATEST(importance, 0.8);
            EXIT;
        END IF;
    END LOOP;

    RETURN LEAST(1.0, GREATEST(0.15, importance));
END;
$$;

CREATE OR REPLACE FUNCTION record_chat_turn_memory(
    p_user_text TEXT,
    p_assistant_text TEXT,
    p_session_id TEXT DEFAULT NULL,
    p_source_identity TEXT DEFAULT NULL,
    p_context JSONB DEFAULT '{}'::jsonb
) RETURNS JSONB
LANGUAGE plpgsql
AS $$
DECLARE
    started_at TIMESTAMPTZ := clock_timestamp();
    content TEXT;
    importance FLOAT;
    source_attribution JSONB;
    metadata JSONB;
    session_uuid UUID;
    raw JSONB;
    raw_unit_id UUID;
    promoted_memory_id UUID;
    promoted BOOLEAN := FALSE;
    duration_ms FLOAT;
BEGIN
    IF COALESCE(p_user_text, '') = '' AND COALESCE(p_assistant_text, '') = '' THEN
        RETURN jsonb_build_object('skipped', true, 'reason', 'empty_turn');
    END IF;

    content := format_recmem_turn(COALESCE(p_user_text, ''), COALESCE(p_assistant_text, ''));
    importance := COALESCE(
        NULLIF(p_context->>'importance', '')::FLOAT,
        estimate_conversation_importance(p_user_text, p_assistant_text)
    );
    metadata := COALESCE(p_context->'metadata', '{"type":"conversation"}'::jsonb);
    source_attribution := COALESCE(
        p_context->'source_attribution',
        jsonb_build_object(
            'kind', COALESCE(p_context #>> '{source_attribution_kind}', 'conversation'),
            'ref', COALESCE(p_source_identity, 'conversation_turn'),
            'label', COALESCE(p_context #>> '{source_attribution_label}', 'conversation turn'),
            'observed_at', CURRENT_TIMESTAMP,
            'trust', COALESCE(NULLIF(p_context #>> '{trust}', '')::FLOAT, 0.95)
        )
    );
    session_uuid := _db_brain_try_uuid(p_session_id);

    raw := recmem_ingest_turn(
        p_user_text,
        p_assistant_text,
        session_uuid,
        p_source_identity,
        CURRENT_TIMESTAMP,
        importance,
        source_attribution,
        metadata
    );
    raw_unit_id := _db_brain_try_uuid(raw->>'unit_id');

    IF importance >= 0.8 THEN
        promoted_memory_id := create_episodic_memory(
            content,
            NULL,
            jsonb_build_object('type', 'conversation', 'recmem', jsonb_build_object('direct_promoted', true)),
            NULL,
            0.0,
            CURRENT_TIMESTAMP,
            importance,
            source_attribution,
            0.95
        );
        promoted := TRUE;
        IF raw_unit_id IS NOT NULL THEN
            PERFORM link_memory_to_source_unit(promoted_memory_id, raw_unit_id, 'direct_promotion');
        END IF;
    END IF;

    duration_ms := EXTRACT(EPOCH FROM (clock_timestamp() - started_at)) * 1000.0;

    RETURN jsonb_build_object(
        'raw', COALESCE(raw, '{}'::jsonb),
        'raw_unit_id', raw_unit_id,
        'direct_promoted', promoted,
        'promoted_memory_id', promoted_memory_id,
        'importance', importance,
        'duration_ms', duration_ms
    );
END;
$$;

CREATE OR REPLACE FUNCTION prepare_channel_turn(
    p_message JSONB
) RETURNS JSONB
LANGUAGE plpgsql
AS $$
DECLARE
    v_channel_type TEXT := p_message->>'channel_type';
    v_channel_id TEXT := p_message->>'channel_id';
    v_sender_id TEXT := p_message->>'sender_id';
    v_sender_name TEXT := p_message->>'sender_name';
    v_content TEXT := COALESCE(p_message->>'content', '');
    v_platform_message_id TEXT := p_message->>'message_id';
    cost FLOAT;
    multiplier FLOAT;
    effective_cost FLOAT;
    rate_limit INT;
    recent_count INT;
    session_row channel_sessions%ROWTYPE;
    remaining_energy FLOAT;
BEGIN
    cost := COALESCE(NULLIF(get_config_text('channel.' || v_channel_type || '.energy_cost'), '')::FLOAT, 0.0);
    multiplier := COALESCE(NULLIF(get_config_text('channel.' || v_channel_type || '.energy_multiplier'), '')::FLOAT, 1.0);
    effective_cost := cost * multiplier;
    rate_limit := NULLIF(get_config_text('channel.' || v_channel_type || '.rate_limit.max_per_sender_per_hour'), '')::INT;

    IF rate_limit IS NOT NULL THEN
        SELECT COUNT(*)::INT INTO recent_count
        FROM channel_messages cm
        JOIN channel_sessions cs ON cm.session_id = cs.id
        WHERE cs.sender_id = v_sender_id
          AND cs.channel_type = v_channel_type
          AND cm.direction = 'inbound'
          AND cm.created_at > CURRENT_TIMESTAMP - INTERVAL '1 hour';
        IF recent_count >= rate_limit THEN
            RETURN jsonb_build_object('allowed', false, 'cost', effective_cost, 'rejection', 'Rate limit exceeded. Please try again later.');
        END IF;
    END IF;

    IF effective_cost > 0 THEN
        UPDATE heartbeat_state
        SET current_energy = current_energy - effective_cost
        WHERE current_energy >= effective_cost
        RETURNING current_energy INTO remaining_energy;
        IF remaining_energy IS NULL THEN
            RETURN jsonb_build_object('allowed', false, 'cost', effective_cost, 'rejection', 'I need to rest and recharge before I can respond. Please try again later.');
        END IF;
    END IF;

    SELECT * INTO session_row
    FROM channel_sessions cs
    WHERE cs.channel_type = v_channel_type
      AND cs.channel_id = v_channel_id
      AND cs.sender_id = v_sender_id
    LIMIT 1;

    IF NOT FOUND THEN
        INSERT INTO channel_sessions (channel_type, channel_id, sender_id, sender_name, history)
        VALUES (v_channel_type, v_channel_id, v_sender_id, v_sender_name, '[]'::jsonb)
        RETURNING * INTO session_row;
    END IF;

    INSERT INTO channel_messages (session_id, direction, content, platform_message_id, metadata)
    VALUES (
        session_row.id,
        'inbound',
        v_content,
        v_platform_message_id,
        jsonb_build_object('channel_type', v_channel_type, 'sender_name', v_sender_name)
    );

    RETURN jsonb_build_object(
        'allowed', true,
        'cost', effective_cost,
        'session_id', session_row.id,
        'history', COALESCE(session_row.history, '[]'::jsonb)
    );
END;
$$;

CREATE OR REPLACE FUNCTION flush_channel_history_to_memory(
    p_session_id UUID,
    p_trimmed_history JSONB
) RETURNS JSONB
LANGUAGE plpgsql
AS $$
DECLARE
    idx INT := 0;
    item JSONB;
    next_item JSONB;
    user_text TEXT;
    assistant_text TEXT;
    stored INT := 0;
    digest TEXT;
    source_identity TEXT;
    result JSONB;
BEGIN
    WHILE idx < jsonb_array_length(COALESCE(p_trimmed_history, '[]'::jsonb)) LOOP
        item := p_trimmed_history->idx;
        next_item := p_trimmed_history->(idx + 1);
        user_text := '';
        assistant_text := '';

        IF item->>'role' = 'user' THEN
            user_text := COALESCE(item->>'content', '');
            IF next_item->>'role' = 'assistant' THEN
                assistant_text := COALESCE(next_item->>'content', '');
                idx := idx + 2;
            ELSE
                idx := idx + 1;
            END IF;
        ELSIF item->>'role' = 'assistant' THEN
            assistant_text := COALESCE(item->>'content', '');
            idx := idx + 1;
        ELSE
            idx := idx + 1;
            CONTINUE;
        END IF;

        IF user_text <> '' OR assistant_text <> '' THEN
            IF estimate_conversation_importance(user_text, assistant_text, 0.3) < 0.4
               AND length(user_text) + length(assistant_text) < 100 THEN
                CONTINUE;
            END IF;
            digest := substring(encode(digest(user_text || E'\x1e' || assistant_text, 'sha256'), 'hex') from 1 for 16);
            source_identity := 'compaction:' || p_session_id::text || ':' || stored::text || ':' || digest;
            result := record_chat_turn_memory(
                user_text,
                assistant_text,
                p_session_id::text,
                source_identity,
                jsonb_build_object(
                    'importance', estimate_conversation_importance(user_text, assistant_text, 0.3),
                    'metadata', jsonb_build_object('type', 'conversation', 'source', 'compaction_flush'),
                    'source_attribution', jsonb_build_object(
                        'kind', 'compaction_flush',
                        'ref', p_session_id,
                        'label', 'pre-compaction memory flush',
                        'observed_at', CURRENT_TIMESTAMP,
                        'trust', 0.85
                    ),
                    'trust', 0.85
                )
            );
            stored := stored + 1;
        END IF;
    END LOOP;

    RETURN jsonb_build_object('stored', stored);
END;
$$;

CREATE OR REPLACE FUNCTION finalize_channel_turn(
    p_session_id UUID,
    p_user_text TEXT,
    p_assistant_text TEXT,
    p_result JSONB DEFAULT '{}'::jsonb
) RETURNS JSONB
LANGUAGE plpgsql
AS $$
DECLARE
    v_history JSONB := COALESCE(p_result->'history', '[]'::jsonb);
    trimmed JSONB := '[]'::jsonb;
    trim_to INT := 30;
    max_history INT := 40;
    flush_result JSONB := '{}'::jsonb;
    platform_message_id TEXT := p_result->>'platform_message_id';
    metadata JSONB := COALESCE(p_result->'metadata', '{}'::jsonb);
BEGIN
    IF jsonb_array_length(v_history) > max_history THEN
        SELECT COALESCE(jsonb_agg(value ORDER BY ord), '[]'::jsonb)
        INTO trimmed
        FROM jsonb_array_elements(v_history) WITH ORDINALITY AS t(value, ord)
        WHERE ord <= jsonb_array_length(v_history) - trim_to;

        SELECT COALESCE(jsonb_agg(value ORDER BY ord), '[]'::jsonb)
        INTO v_history
        FROM jsonb_array_elements(v_history) WITH ORDINALITY AS t(value, ord)
        WHERE ord > jsonb_array_length(v_history) - trim_to;

        flush_result := flush_channel_history_to_memory(p_session_id, trimmed);
    END IF;

    UPDATE channel_sessions
    SET history = v_history,
        last_active = CURRENT_TIMESTAMP
    WHERE id = p_session_id;

    INSERT INTO channel_messages (session_id, direction, content, platform_message_id, metadata)
    VALUES (p_session_id, 'outbound', COALESCE(p_assistant_text, ''), platform_message_id, metadata);

    RETURN jsonb_build_object('session_id', p_session_id, 'history_count', jsonb_array_length(v_history), 'flush', flush_result);
END;
$$;
