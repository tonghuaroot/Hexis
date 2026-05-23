-- DB-owned RecMem operations, rollout/eval, and subconscious post-processing.
SET search_path = public, ag_catalog, "$user";

CREATE OR REPLACE FUNCTION load_recmem_task_context(
    p_task_id UUID
) RETURNS JSONB
LANGUAGE plpgsql
STABLE
AS $$
DECLARE
    task_row recmem_consolidation_tasks%ROWTYPE;
    sources JSONB;
    target JSONB;
BEGIN
    SELECT * INTO task_row
    FROM recmem_consolidation_tasks
    WHERE id = p_task_id;

    IF NOT FOUND THEN
        RAISE EXCEPTION 'RecMem task not found: %', p_task_id;
    END IF;

    SELECT COALESCE(jsonb_agg(to_jsonb(s) ORDER BY s.turn_at, s.created_at), '[]'::jsonb)
    INTO sources
    FROM (
        SELECT id, content, user_text, assistant_text, turn_at, created_at
        FROM subconscious_units
        WHERE id = ANY(task_row.source_unit_ids)
    ) s;

    IF task_row.target_memory_id IS NOT NULL THEN
        SELECT to_jsonb(m)
        INTO target
        FROM (
            SELECT id, content, type::text, trust_level
            FROM memories
            WHERE id = task_row.target_memory_id
        ) m;
    END IF;

    RETURN jsonb_build_object(
        'task', to_jsonb(task_row),
        'sources', COALESCE(sources, '[]'::jsonb),
        'target_memory', target
    );
END;
$$;

CREATE OR REPLACE FUNCTION normalize_recmem_episode_output(
    p_output JSONB
) RETURNS JSONB
LANGUAGE plpgsql
IMMUTABLE
AS $$
DECLARE
    raw JSONB;
    item JSONB;
    episodes JSONB := '[]'::jsonb;
BEGIN
    raw := CASE
        WHEN jsonb_typeof(p_output) = 'object' THEN COALESCE(p_output->'episodes', '[]'::jsonb)
        ELSE COALESCE(p_output, '[]'::jsonb)
    END;
    IF jsonb_typeof(raw) <> 'array' THEN
        RETURN '[]'::jsonb;
    END IF;
    FOR item IN SELECT * FROM jsonb_array_elements(raw) LOOP
        IF jsonb_typeof(item) = 'string' THEN
            episodes := episodes || jsonb_build_array(jsonb_build_object('content', item #>> '{}'));
        ELSIF jsonb_typeof(item) = 'object' AND COALESCE(item->>'content', item->>'episode') IS NOT NULL THEN
            episodes := episodes || jsonb_build_array(item);
        END IF;
    END LOOP;
    RETURN episodes;
END;
$$;

CREATE OR REPLACE FUNCTION normalize_recmem_fact_output(
    p_output JSONB
) RETURNS JSONB
LANGUAGE plpgsql
IMMUTABLE
AS $$
DECLARE
    raw JSONB;
    item JSONB;
    facts JSONB := '[]'::jsonb;
BEGIN
    raw := CASE
        WHEN jsonb_typeof(p_output) = 'object' THEN COALESCE(p_output->'facts', '[]'::jsonb)
        ELSE COALESCE(p_output, '[]'::jsonb)
    END;
    IF jsonb_typeof(raw) <> 'array' THEN
        RETURN '[]'::jsonb;
    END IF;
    FOR item IN SELECT * FROM jsonb_array_elements(raw) LOOP
        IF jsonb_typeof(item) = 'string' THEN
            facts := facts || jsonb_build_array(jsonb_build_object('content', item #>> '{}'));
        ELSIF jsonb_typeof(item) = 'object' AND COALESCE(item->>'content', item->>'fact') IS NOT NULL THEN
            facts := facts || jsonb_build_array(item);
        END IF;
    END LOOP;
    RETURN facts;
END;
$$;

CREATE OR REPLACE FUNCTION run_recmem_eval_set(
    p_eval_set TEXT,
    p_label TEXT DEFAULT NULL,
    p_limit INT DEFAULT 10
) RETURNS JSONB
LANGUAGE plpgsql
AS $$
DECLARE
    eval_row recmem_eval_sets%ROWTYPE;
    run_id UUID;
    item recmem_eval_items%ROWTYPE;
    baseline_ids UUID[];
    recmem_ids UUID[];
    expected_ids UUID[];
    baseline_score FLOAT;
    recmem_score FLOAT;
    verdict TEXT;
    safe_limit INT := GREATEST(COALESCE(p_limit, 10), 1);
BEGIN
    BEGIN
        SELECT * INTO eval_row FROM recmem_eval_sets WHERE id = p_eval_set::uuid;
    EXCEPTION WHEN invalid_text_representation THEN
        SELECT * INTO eval_row FROM recmem_eval_sets WHERE name = p_eval_set;
    END;
    IF eval_row.id IS NULL THEN
        RAISE EXCEPTION 'RecMem eval set not found: %', p_eval_set;
    END IF;

    INSERT INTO recmem_eval_runs (eval_set_id, label, baseline_config, recmem_config, metadata)
    VALUES (
        eval_row.id,
        p_label,
        jsonb_build_object('retrieval', 'fast_recall', 'limit', safe_limit),
        jsonb_build_object('retrieval', 'recmem_recall_context', 'limit', safe_limit),
        '{}'::jsonb
    )
    RETURNING id INTO run_id;

    BEGIN
        FOR item IN
            SELECT * FROM recmem_eval_items WHERE eval_set_id = eval_row.id ORDER BY created_at, id
        LOOP
            SELECT COALESCE(array_agg(memory_id), ARRAY[]::uuid[])
            INTO baseline_ids
            FROM (
                SELECT memory_id FROM fast_recall(item.query_text, safe_limit)
            ) b;

            SELECT COALESCE(array_agg(item_id), ARRAY[]::uuid[])
            INTO recmem_ids
            FROM (
                SELECT item_id
                FROM recmem_recall_context(item.query_text, safe_limit, GREATEST(1, LEAST(safe_limit, 5)), safe_limit)
                WHERE tier IN ('episodic', 'semantic')
                LIMIT safe_limit
            ) r;

            SELECT COALESCE(array_agg(value::uuid), ARRAY[]::uuid[])
            INTO expected_ids
            FROM jsonb_array_elements_text(
                COALESCE(
                    NULLIF(item.metadata->'expected_memory_ids', 'null'::jsonb),
                    NULLIF(item.session_fixture->'expected_memory_ids', 'null'::jsonb),
                    '[]'::jsonb
                )
            ) ids(value)
            WHERE value ~* '^[0-9a-f-]{36}$';

            IF cardinality(expected_ids) = 0 THEN
                baseline_score := NULL;
                recmem_score := NULL;
                verdict := 'unjudged';
            ELSE
                SELECT COUNT(*)::float / cardinality(expected_ids)::float
                INTO baseline_score
                FROM unnest(expected_ids) expected(id)
                WHERE expected.id = ANY(baseline_ids);

                SELECT COUNT(*)::float / cardinality(expected_ids)::float
                INTO recmem_score
                FROM unnest(expected_ids) expected(id)
                WHERE expected.id = ANY(recmem_ids);

                IF recmem_score >= COALESCE(baseline_score, 0) THEN
                    verdict := 'pass';
                ELSIF baseline_score IS NULL THEN
                    verdict := CASE WHEN recmem_score > 0 THEN 'pass' ELSE 'miss' END;
                ELSE
                    verdict := 'regression';
                END IF;
            END IF;

            INSERT INTO recmem_eval_results (
                run_id, item_id, category, baseline_memory_ids, recmem_memory_ids, judge_score, verdict, metadata
            )
            VALUES (
                run_id,
                item.id,
                item.category,
                baseline_ids,
                recmem_ids,
                recmem_score,
                verdict,
                jsonb_build_object(
                    'expected_memory_ids', to_jsonb(expected_ids),
                    'baseline_hit_rate', baseline_score,
                    'recmem_hit_rate', recmem_score
                )
            );
        END LOOP;

        UPDATE recmem_eval_runs
        SET status = 'completed', completed_at = CURRENT_TIMESTAMP
        WHERE id = run_id;
    EXCEPTION WHEN OTHERS THEN
        UPDATE recmem_eval_runs
        SET status = 'failed',
            completed_at = CURRENT_TIMESTAMP,
            metadata = metadata || jsonb_build_object('error', SQLERRM)
        WHERE id = run_id;
        RAISE;
    END;

    RETURN get_recmem_eval_run_summary(run_id);
END;
$$;

CREATE OR REPLACE FUNCTION recmem_rollout_phase_config(
    p_phase INT
) RETURNS JSONB
LANGUAGE sql
IMMUTABLE
AS $$
    SELECT CASE p_phase
        WHEN 0 THEN '{"memory.recmem_rollout_phase":0,"memory.recmem_enabled":false,"chat.eager_memory_enabled":true,"chat.inline_subconscious_enabled":true,"memory.recmem_hydrate_enabled":false,"memory.recmem_dual_write_compare":false,"memory.recmem_rollout_metrics_enabled":false,"memory.recmem_worker_enabled":false}'::jsonb
        WHEN 1 THEN '{"memory.recmem_rollout_phase":1,"memory.recmem_enabled":false,"chat.eager_memory_enabled":true,"chat.inline_subconscious_enabled":true,"memory.recmem_hydrate_enabled":false,"memory.recmem_dual_write_compare":false,"memory.recmem_rollout_metrics_enabled":true,"memory.recmem_worker_enabled":false}'::jsonb
        WHEN 2 THEN '{"memory.recmem_rollout_phase":2,"memory.recmem_enabled":true,"chat.eager_memory_enabled":true,"chat.inline_subconscious_enabled":true,"memory.recmem_hydrate_enabled":false,"memory.recmem_dual_write_compare":true,"memory.recmem_rollout_metrics_enabled":true,"memory.recmem_worker_enabled":false}'::jsonb
        WHEN 3 THEN '{"memory.recmem_rollout_phase":3,"memory.recmem_enabled":true,"chat.eager_memory_enabled":false,"chat.inline_subconscious_enabled":true,"memory.recmem_hydrate_enabled":false,"memory.recmem_dual_write_compare":false,"memory.recmem_rollout_metrics_enabled":true,"memory.recmem_worker_enabled":false}'::jsonb
        WHEN 4 THEN '{"memory.recmem_rollout_phase":4,"memory.recmem_enabled":true,"chat.eager_memory_enabled":false,"chat.inline_subconscious_enabled":true,"memory.recmem_hydrate_enabled":false,"memory.recmem_dual_write_compare":false,"memory.recmem_rollout_metrics_enabled":true,"memory.recmem_worker_enabled":true}'::jsonb
        WHEN 5 THEN '{"memory.recmem_rollout_phase":5,"memory.recmem_enabled":true,"chat.eager_memory_enabled":false,"chat.inline_subconscious_enabled":true,"memory.recmem_hydrate_enabled":true,"memory.recmem_dual_write_compare":false,"memory.recmem_rollout_metrics_enabled":true,"memory.recmem_worker_enabled":true}'::jsonb
        WHEN 6 THEN '{"memory.recmem_rollout_phase":6,"memory.recmem_enabled":true,"chat.eager_memory_enabled":false,"chat.inline_subconscious_enabled":true,"memory.recmem_hydrate_enabled":true,"memory.recmem_dual_write_compare":false,"memory.recmem_rollout_metrics_enabled":true,"memory.recmem_worker_enabled":true}'::jsonb
        ELSE NULL
    END;
$$;

CREATE OR REPLACE FUNCTION infer_recmem_rollout_phase()
RETURNS INT
LANGUAGE plpgsql
STABLE
AS $$
DECLARE
    phase INT;
    cfg JSONB;
    key TEXT;
    value JSONB;
    matches BOOLEAN;
BEGIN
    FOR phase IN REVERSE 6..0 LOOP
        cfg := recmem_rollout_phase_config(phase);
        matches := TRUE;
        FOR key, value IN SELECT * FROM jsonb_each(cfg) LOOP
            IF get_config(key) IS DISTINCT FROM value THEN
                matches := FALSE;
                EXIT;
            END IF;
        END LOOP;
        IF matches THEN
            RETURN phase;
        END IF;
    END LOOP;
    RETURN NULL;
END;
$$;

CREATE OR REPLACE FUNCTION get_recmem_rollout_status(
    p_eval_run_id UUID DEFAULT NULL
) RETURNS JSONB
LANGUAGE plpgsql
STABLE
AS $$
DECLARE
    selected_run_id UUID := p_eval_run_id;
    configs JSONB := '{}'::jsonb;
    key TEXT;
BEGIN
    IF selected_run_id IS NULL THEN
        SELECT id INTO selected_run_id
        FROM recmem_eval_runs
        WHERE status = 'completed'
        ORDER BY completed_at DESC NULLS LAST, started_at DESC
        LIMIT 1;
    END IF;

    FOREACH key IN ARRAY ARRAY[
        'memory.recmem_rollout_phase',
        'memory.recmem_enabled',
        'chat.eager_memory_enabled',
        'chat.recmem_salience_direct_promote',
        'chat.inline_subconscious_enabled',
        'memory.recmem_hydrate_enabled',
        'memory.recmem_dual_write_compare',
        'memory.recmem_rollout_metrics_enabled',
        'memory.recmem_worker_enabled'
    ] LOOP
        configs := configs || jsonb_build_object(key, get_config(key));
    END LOOP;

    RETURN jsonb_build_object(
        'phase', infer_recmem_rollout_phase(),
        'configs', configs,
        'health', COALESCE((SELECT to_jsonb(h) FROM recmem_rollout_health h LIMIT 1), '{}'::jsonb),
        'metrics_7d', get_recmem_rollout_metrics(CURRENT_TIMESTAMP - INTERVAL '7 days'),
        'phase5_readiness', get_recmem_phase5_readiness(selected_run_id)
    );
END;
$$;

CREATE OR REPLACE FUNCTION apply_recmem_rollout_phase(
    p_phase INT,
    p_eval_run_id UUID DEFAULT NULL,
    p_force BOOLEAN DEFAULT FALSE
) RETURNS JSONB
LANGUAGE plpgsql
AS $$
DECLARE
    cfg JSONB;
    readiness JSONB;
    key TEXT;
    value JSONB;
BEGIN
    cfg := recmem_rollout_phase_config(p_phase);
    IF cfg IS NULL THEN
        RAISE EXCEPTION 'Unknown RecMem rollout phase: %', p_phase;
    END IF;

    IF p_phase >= 5 AND NOT COALESCE(p_force, false) THEN
        readiness := get_recmem_phase5_readiness(p_eval_run_id);
        IF COALESCE((readiness->>'ready')::boolean, false) IS DISTINCT FROM TRUE THEN
            RAISE EXCEPTION 'Phase % requires a passing readiness gate: %', p_phase, COALESCE(readiness->>'reason', 'readiness_unavailable');
        END IF;
    END IF;

    FOR key, value IN SELECT * FROM jsonb_each(cfg) LOOP
        PERFORM set_config(key, value);
    END LOOP;

    RETURN get_recmem_rollout_status(p_eval_run_id)
        || jsonb_build_object('applied_phase', p_phase, 'forced', COALESCE(p_force, false));
END;
$$;

CREATE OR REPLACE FUNCTION normalize_subconscious_observations(
    p_doc JSONB
) RETURNS JSONB
LANGUAGE sql
IMMUTABLE
AS $$
    SELECT jsonb_build_object(
        'narrative_observations', CASE WHEN jsonb_typeof(COALESCE(p_doc->'narrative_observations', '[]'::jsonb)) = 'array' THEN COALESCE(p_doc->'narrative_observations', '[]'::jsonb) ELSE '[]'::jsonb END,
        'relationship_observations', CASE WHEN jsonb_typeof(COALESCE(p_doc->'relationship_observations', '[]'::jsonb)) = 'array' THEN COALESCE(p_doc->'relationship_observations', '[]'::jsonb) ELSE '[]'::jsonb END,
        'contradiction_observations', CASE WHEN jsonb_typeof(COALESCE(p_doc->'contradiction_observations', '[]'::jsonb)) = 'array' THEN COALESCE(p_doc->'contradiction_observations', '[]'::jsonb) ELSE '[]'::jsonb END,
        'emotional_observations', CASE WHEN jsonb_typeof(COALESCE(p_doc->'emotional_observations', p_doc->'emotional_patterns', '[]'::jsonb)) = 'array' THEN COALESCE(p_doc->'emotional_observations', p_doc->'emotional_patterns', '[]'::jsonb) ELSE '[]'::jsonb END,
        'consolidation_observations', CASE WHEN jsonb_typeof(COALESCE(p_doc->'consolidation_observations', p_doc->'consolidation_suggestions', '[]'::jsonb)) = 'array' THEN COALESCE(p_doc->'consolidation_observations', p_doc->'consolidation_suggestions', '[]'::jsonb) ELSE '[]'::jsonb END
    );
$$;

CREATE OR REPLACE FUNCTION compute_dopamine_rpe(
    p_context JSONB DEFAULT '{}'::jsonb,
    p_doc JSONB DEFAULT '{}'::jsonb
) RETURNS JSONB
LANGUAGE plpgsql
AS $$
DECLARE
    da_state JSONB;
    affect JSONB;
    tonic FLOAT;
    current_valence FLOAT;
    current_arousal FLOAT;
    expected_valence FLOAT;
    rpe FLOAT;
    trigger_parts TEXT[] := ARRAY[]::TEXT[];
    obs JSONB;
    result JSONB;
    emotional_obs JSONB;
    relationship_obs JSONB;
BEGIN
    da_state := get_dopamine_state();
    affect := get_current_affective_state();
    BEGIN tonic := NULLIF(da_state->>'tonic', '')::float;
    EXCEPTION WHEN OTHERS THEN tonic := NULL; END;
    BEGIN current_valence := NULLIF(affect->>'valence', '')::float;
    EXCEPTION WHEN OTHERS THEN current_valence := NULL; END;
    BEGIN current_arousal := NULLIF(affect->>'arousal', '')::float;
    EXCEPTION WHEN OTHERS THEN current_arousal := NULL; END;
    tonic := COALESCE(tonic, 0.5);
    current_valence := COALESCE(current_valence, 0.0);
    current_arousal := COALESCE(current_arousal, 0.5);
    expected_valence := (tonic - 0.5) * 2.0;
    rpe := current_valence - expected_valence;
    rpe := rpe * (0.5 + current_arousal * 0.5);
    rpe := LEAST(1.0, GREATEST(-1.0, rpe));

    IF abs(rpe) < 0.15 THEN
        RETURN jsonb_build_object('fired', false, 'rpe', rpe, 'tonic', tonic);
    END IF;

    IF p_doc #>> '{emotional_state,primary_emotion}' IS NOT NULL THEN
        trigger_parts := trigger_parts || ('feeling ' || (p_doc #>> '{emotional_state,primary_emotion}'));
    END IF;
    emotional_obs := COALESCE(p_doc->'emotional_observations', p_doc->'emotional_patterns', '[]'::jsonb);
    IF jsonb_typeof(emotional_obs) <> 'array' THEN
        emotional_obs := '[]'::jsonb;
    END IF;
    FOR obs IN SELECT * FROM jsonb_array_elements(emotional_obs) LIMIT 2 LOOP
        IF COALESCE(obs->>'pattern', obs->>'summary', obs->>'theme') IS NOT NULL THEN
            trigger_parts := trigger_parts || left(COALESCE(obs->>'pattern', obs->>'summary', obs->>'theme'), 100);
        END IF;
    END LOOP;
    relationship_obs := COALESCE(p_doc->'relationship_observations', '[]'::jsonb);
    IF jsonb_typeof(relationship_obs) <> 'array' THEN
        relationship_obs := '[]'::jsonb;
    END IF;
    FOR obs IN SELECT * FROM jsonb_array_elements(relationship_obs) LIMIT 2 LOOP
        IF obs->>'entity' IS NOT NULL AND obs->>'change_type' IS NOT NULL THEN
            trigger_parts := trigger_parts || ((obs->>'change_type') || ' with ' || (obs->>'entity'));
        END IF;
    END LOOP;

    result := fire_dopamine_spike(
        rpe,
        COALESCE(array_to_string(trigger_parts, '; '), 'affective state shift')
    );
    RETURN jsonb_build_object('fired', true, 'rpe', rpe, 'result', result);
END;
$$;

CREATE OR REPLACE FUNCTION apply_subconscious_decider_result(
    p_doc JSONB,
    p_raw_response JSONB DEFAULT '{}'::jsonb
) RETURNS JSONB
LANGUAGE plpgsql
AS $$
DECLARE
    observations JSONB;
    applied JSONB;
    dopamine JSONB;
BEGIN
    observations := normalize_subconscious_observations(COALESCE(p_doc, '{}'::jsonb));
    applied := apply_subconscious_observations(observations);
    dopamine := compute_dopamine_rpe('{}'::jsonb, COALESCE(p_doc, '{}'::jsonb));
    RETURN jsonb_build_object('applied', applied, 'dopamine', dopamine, 'raw_response', COALESCE(p_raw_response, '{}'::jsonb));
END;
$$;
