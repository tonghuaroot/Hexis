-- DB-native tool execution helpers for tools that do not need Python side effects.
SET search_path = public, ag_catalog, "$user";

CREATE OR REPLACE FUNCTION tool_success(
    p_output JSONB,
    p_display_output TEXT DEFAULT NULL
) RETURNS JSONB
LANGUAGE sql
IMMUTABLE
AS $$
    SELECT jsonb_build_object('success', true, 'output', COALESCE(p_output, '{}'::jsonb), 'display_output', p_display_output);
$$;

CREATE OR REPLACE FUNCTION tool_error(
    p_error TEXT,
    p_error_type TEXT DEFAULT 'execution_failed'
) RETURNS JSONB
LANGUAGE sql
IMMUTABLE
AS $$
    SELECT jsonb_build_object('success', false, 'error', COALESCE(p_error, 'Tool failed'), 'error_type', COALESCE(p_error_type, 'execution_failed'));
$$;

CREATE OR REPLACE FUNCTION execute_goals_tool(
    p_args JSONB
) RETURNS JSONB
LANGUAGE plpgsql
AS $$
DECLARE
    action TEXT := COALESCE(p_args->>'action', '');
    title TEXT;
    goal_id UUID;
    priority TEXT;
    source_value TEXT;
    snapshot JSONB;
    rows_json JSONB;
BEGIN
    IF action NOT IN ('create', 'update_priority', 'add_progress', 'list') THEN
        RETURN tool_error(format('Invalid action %L', action), 'invalid_params');
    END IF;
    IF action = 'create' THEN
        title := NULLIF(btrim(COALESCE(p_args->>'title', '')), '');
        IF title IS NULL THEN
            RETURN tool_error('Title is required for create', 'invalid_params');
        END IF;
        priority := COALESCE(NULLIF(p_args->>'priority', ''), 'queued');
        IF priority NOT IN ('active', 'queued', 'backburner', 'completed', 'abandoned') THEN
            priority := 'queued';
        END IF;
        source_value := COALESCE(NULLIF(p_args->>'source', ''), 'curiosity');
        IF source_value NOT IN ('curiosity', 'user_request', 'identity', 'derived', 'external') THEN
            source_value := 'curiosity';
        END IF;
        goal_id := create_goal(title, p_args->>'description', source_value::goal_source, priority::goal_priority);
        RETURN tool_success(
            jsonb_build_object('goal_id', goal_id::text, 'title', title, 'priority', priority),
            format('Created goal: %s (%s)', title, priority)
        );
    ELSIF action = 'update_priority' THEN
        goal_id := NULLIF(p_args->>'goal_id', '')::uuid;
        priority := COALESCE(p_args->>'priority', '');
        IF goal_id IS NULL THEN
            RETURN tool_error('goal_id is required for update_priority', 'invalid_params');
        END IF;
        IF priority NOT IN ('active', 'queued', 'backburner', 'completed', 'abandoned') THEN
            RETURN tool_error(format('Invalid priority %L', priority), 'invalid_params');
        END IF;
        PERFORM change_goal_priority(goal_id, priority::goal_priority, COALESCE(p_args->>'reason', ''));
        RETURN tool_success(jsonb_build_object('goal_id', goal_id::text, 'new_priority', priority, 'reason', COALESCE(p_args->>'reason', '')), format('Updated goal %s... to %s', left(goal_id::text, 8), priority));
    ELSIF action = 'add_progress' THEN
        goal_id := NULLIF(p_args->>'goal_id', '')::uuid;
        IF goal_id IS NULL THEN
            RETURN tool_error('goal_id is required for add_progress', 'invalid_params');
        END IF;
        IF NULLIF(btrim(COALESCE(p_args->>'note', '')), '') IS NULL THEN
            RETURN tool_error('note is required for add_progress', 'invalid_params');
        END IF;
        PERFORM add_goal_progress(goal_id, p_args->>'note');
        RETURN tool_success(jsonb_build_object('goal_id', goal_id::text, 'note', p_args->>'note'), format('Added progress to goal %s...', left(goal_id::text, 8)));
    ELSE
        priority := NULLIF(p_args->>'priority', '');
        IF priority IS NOT NULL AND priority IN ('active', 'queued', 'backburner', 'completed', 'abandoned') THEN
            SELECT COALESCE(jsonb_agg(to_jsonb(g)), '[]'::jsonb) INTO rows_json
            FROM get_goals_by_priority(priority::goal_priority) g;
            RETURN tool_success(jsonb_build_object('goals', rows_json, 'count', jsonb_array_length(rows_json)));
        END IF;
        snapshot := get_goals_snapshot();
        RETURN tool_success(COALESCE(snapshot, '{}'::jsonb));
    END IF;
EXCEPTION WHEN OTHERS THEN
    RETURN tool_error(SQLERRM);
END;
$$;

CREATE OR REPLACE FUNCTION record_backlog_user_change(
    p_action TEXT,
    p_title TEXT,
    p_item_id UUID
) RETURNS VOID
LANGUAGE plpgsql
AS $$
BEGIN
    INSERT INTO memories (type, content, embedding, importance, trust_level, metadata)
    VALUES (
        'episodic',
        format('User %s backlog item: %s', p_action, p_title),
        array_fill(0.1, ARRAY[embedding_dimension()])::vector,
        0.6,
        1.0,
        jsonb_build_object('backlog_item_id', p_item_id::text, 'action', p_action, 'source', 'user_backlog_change')
    );
EXCEPTION WHEN OTHERS THEN
    NULL;
END;
$$;

CREATE OR REPLACE FUNCTION execute_backlog_tool(
    p_args JSONB,
    p_context JSONB DEFAULT '{}'::jsonb
) RETURNS JSONB
LANGUAGE plpgsql
AS $$
DECLARE
    action TEXT := COALESCE(p_args->>'action', '');
    item_id UUID;
    row_data backlog%ROWTYPE;
    rows_json JSONB;
    fields JSONB := '{}'::jsonb;
    is_user BOOLEAN := COALESCE(p_context->>'tool_context', 'chat') IN ('chat', 'mcp');
BEGIN
    IF action NOT IN ('create', 'update', 'delete', 'list', 'get', 'set_status', 'set_checkpoint') THEN
        RETURN tool_error(format('Invalid action %L', action), 'invalid_params');
    END IF;
    IF action = 'create' THEN
        IF NULLIF(btrim(COALESCE(p_args->>'title', '')), '') IS NULL THEN
            RETURN tool_error('Title is required for create', 'invalid_params');
        END IF;
        SELECT * INTO row_data
        FROM create_backlog_item(
            p_args->>'title',
            COALESCE(p_args->>'description', ''),
            CASE WHEN p_args->>'priority' IN ('urgent', 'high', 'normal', 'low') THEN p_args->>'priority' ELSE 'normal' END,
            CASE WHEN p_args->>'owner' IN ('agent', 'user', 'shared') THEN p_args->>'owner' ELSE 'agent' END,
            CASE WHEN is_user THEN 'user' ELSE 'agent' END,
            COALESCE(ARRAY(SELECT jsonb_array_elements_text(COALESCE(p_args->'tags', '[]'::jsonb))), ARRAY[]::TEXT[]),
            NULLIF(p_args->>'parent_id', '')::uuid
        );
        IF is_user THEN
            PERFORM record_backlog_user_change('created', row_data.title, row_data.id);
        END IF;
        RETURN tool_success(jsonb_build_object('item_id', row_data.id::text, 'title', row_data.title, 'priority', row_data.priority, 'owner', row_data.owner, 'created_by', row_data.created_by), format('Created backlog item: %s (%s)', row_data.title, row_data.priority));
    ELSIF action = 'list' THEN
        SELECT COALESCE(jsonb_agg(to_jsonb(b)), '[]'::jsonb)
        INTO rows_json
        FROM list_backlog(
            CASE WHEN p_args->>'status_filter' IN ('todo', 'in_progress', 'done', 'blocked', 'cancelled') THEN p_args->>'status_filter' ELSE NULL END,
            CASE WHEN p_args->>'priority_filter' IN ('urgent', 'high', 'normal', 'low') THEN p_args->>'priority_filter' ELSE NULL END,
            CASE WHEN p_args->>'owner_filter' IN ('agent', 'user', 'shared') THEN p_args->>'owner_filter' ELSE NULL END
        ) b;
        RETURN tool_success(jsonb_build_object('items', rows_json, 'count', jsonb_array_length(rows_json)));
    END IF;

    item_id := NULLIF(p_args->>'item_id', '')::uuid;
    IF item_id IS NULL THEN
        RETURN tool_error(format('item_id is required for %s', action), 'invalid_params');
    END IF;

    IF action = 'get' THEN
        SELECT * INTO row_data FROM get_backlog_item(item_id);
        IF row_data.id IS NULL THEN
            RETURN tool_error(format('Backlog item %s not found', item_id), 'execution_failed');
        END IF;
        RETURN tool_success(to_jsonb(row_data));
    ELSIF action = 'delete' THEN
        SELECT * INTO row_data FROM get_backlog_item(item_id);
        IF NOT delete_backlog_item(item_id) THEN
            RETURN tool_error(format('Backlog item %s not found', item_id), 'execution_failed');
        END IF;
        IF is_user AND row_data.title IS NOT NULL THEN
            PERFORM record_backlog_user_change('deleted', row_data.title, item_id);
        END IF;
        RETURN tool_success(jsonb_build_object('item_id', item_id::text, 'deleted', true), format('Deleted backlog item %s...', left(item_id::text, 8)));
    ELSIF action = 'set_status' THEN
        IF p_args->>'status' NOT IN ('todo', 'in_progress', 'done', 'blocked', 'cancelled') THEN
            RETURN tool_error(format('Invalid status %L', p_args->>'status'), 'invalid_params');
        END IF;
        fields := jsonb_build_object('status', p_args->>'status');
    ELSIF action = 'set_checkpoint' THEN
        IF NOT p_args ? 'checkpoint' THEN
            RETURN tool_error('checkpoint data is required for set_checkpoint', 'invalid_params');
        END IF;
        fields := jsonb_build_object('checkpoint', p_args->'checkpoint');
    ELSE
        FOR fields IN SELECT jsonb_object_agg(key, value)
        FROM jsonb_each(p_args)
        WHERE key IN ('title', 'description', 'priority', 'owner', 'status', 'tags')
        LOOP
            fields := COALESCE(fields, '{}'::jsonb);
        END LOOP;
        IF fields = '{}'::jsonb THEN
            RETURN tool_error('No fields to update. Provide at least one of: title, description, priority, owner, status, tags.', 'invalid_params');
        END IF;
    END IF;

    SELECT * INTO row_data FROM update_backlog_item(item_id, fields);
    IF row_data.id IS NULL THEN
        RETURN tool_error(format('Backlog item %s not found', item_id), 'execution_failed');
    END IF;
    IF is_user THEN
        PERFORM record_backlog_user_change(CASE WHEN action = 'set_status' THEN format('changed status to %L on', p_args->>'status') ELSE 'updated' END, row_data.title, item_id);
    END IF;
    RETURN tool_success(
        CASE action
            WHEN 'set_status' THEN jsonb_build_object('item_id', row_data.id::text, 'title', row_data.title, 'new_status', row_data.status)
            WHEN 'set_checkpoint' THEN jsonb_build_object('item_id', row_data.id::text, 'title', row_data.title, 'checkpoint_saved', true)
            ELSE jsonb_build_object('item_id', row_data.id::text, 'title', row_data.title, 'status', row_data.status, 'priority', row_data.priority, 'owner', row_data.owner)
        END,
        CASE action
            WHEN 'set_status' THEN format('Set %s to %s', row_data.title, row_data.status)
            WHEN 'set_checkpoint' THEN format('Saved checkpoint for %s', row_data.title)
            ELSE format('Updated backlog item %s...', left(item_id::text, 8))
        END
    );
EXCEPTION WHEN OTHERS THEN
    RETURN tool_error(SQLERRM);
END;
$$;

CREATE OR REPLACE FUNCTION contact_row_json(c contacts)
RETURNS JSONB
LANGUAGE sql
IMMUTABLE
AS $$
    SELECT jsonb_build_object(
        'id', c.id,
        'name', c.name,
        'email', c.email,
        'company', c.company,
        'role', c.role,
        'phone', c.phone,
        'notes', c.notes,
        'tags', to_jsonb(c.tags),
        'source', c.source,
        'first_seen', c.first_seen,
        'last_touch', c.last_touch
    );
$$;

CREATE OR REPLACE FUNCTION execute_contact_tool(
    p_tool_name TEXT,
    p_args JSONB
) RETURNS JSONB
LANGUAGE plpgsql
AS $$
DECLARE
    row_data contacts%ROWTYPE;
    rows_json JSONB;
    contact_id BIGINT;
    keep_id BIGINT;
    remove_id BIGINT;
    query TEXT;
    limit_value INT;
    updated BOOLEAN;
BEGIN
    limit_value := LEAST(GREATEST(COALESCE(NULLIF(p_args->>'limit', '')::int, 20), 1), 100);
    IF p_tool_name = 'search_contacts' THEN
        query := NULLIF(btrim(COALESCE(p_args->>'query', '')), '');
        IF query IS NULL THEN
            SELECT COALESCE(jsonb_agg(contact_row_json(c)), '[]'::jsonb) INTO rows_json FROM recent_contacts(limit_value) c;
        ELSE
            SELECT COALESCE(jsonb_agg(contact_row_json(c)), '[]'::jsonb) INTO rows_json FROM search_contacts(query, limit_value) c;
        END IF;
        RETURN tool_success(jsonb_build_object('count', jsonb_array_length(rows_json), 'contacts', rows_json), format('Found %s contact(s)', jsonb_array_length(rows_json)));
    ELSIF p_tool_name = 'get_contact' THEN
        IF p_args ? 'id' THEN
            SELECT * INTO row_data FROM contacts WHERE id = (p_args->>'id')::bigint;
        ELSIF NULLIF(p_args->>'email', '') IS NOT NULL THEN
            SELECT * INTO row_data FROM get_contact_by_email(p_args->>'email');
        ELSE
            RETURN tool_error('Provide either id or email.', 'invalid_params');
        END IF;
        IF row_data.id IS NULL THEN
            RETURN tool_success('{"found": false}'::jsonb);
        END IF;
        RETURN tool_success(jsonb_build_object('found', true, 'contact', contact_row_json(row_data)));
    ELSIF p_tool_name = 'create_contact' THEN
        IF NULLIF(btrim(COALESCE(p_args->>'name', '')), '') IS NULL THEN
            RETURN tool_error('Name is required.', 'invalid_params');
        END IF;
        contact_id := create_contact(
            p_args->>'name',
            p_args->>'email',
            p_args->>'company',
            p_args->>'role',
            p_args->>'phone',
            p_args->>'notes',
            COALESCE(ARRAY(SELECT jsonb_array_elements_text(COALESCE(p_args->'tags', '[]'::jsonb))), ARRAY[]::TEXT[]),
            COALESCE(NULLIF(p_args->>'source', ''), 'manual')
        );
        RETURN tool_success(jsonb_build_object('id', contact_id, 'name', p_args->>'name'), format('Created contact #%s: %s', contact_id, p_args->>'name'));
    ELSIF p_tool_name = 'update_contact' THEN
        contact_id := NULLIF(p_args->>'id', '')::bigint;
        IF contact_id IS NULL THEN
            RETURN tool_error('Contact ID is required.', 'invalid_params');
        END IF;
        updated := update_contact(
            contact_id,
            p_args->>'name',
            p_args->>'email',
            p_args->>'company',
            p_args->>'role',
            p_args->>'phone',
            p_args->>'notes',
            CASE WHEN p_args ? 'tags' THEN ARRAY(SELECT jsonb_array_elements_text(COALESCE(p_args->'tags', '[]'::jsonb))) ELSE NULL END
        );
        IF NOT updated THEN
            RETURN tool_error(format('Contact #%s not found.', contact_id), 'execution_failed');
        END IF;
        PERFORM touch_contact(contact_id);
        RETURN tool_success(jsonb_build_object('id', contact_id, 'updated', true), format('Updated contact #%s', contact_id));
    ELSIF p_tool_name = 'merge_contacts' THEN
        keep_id := NULLIF(p_args->>'keep_id', '')::bigint;
        remove_id := NULLIF(p_args->>'remove_id', '')::bigint;
        IF keep_id IS NULL OR remove_id IS NULL THEN
            RETURN tool_error('Both keep_id and remove_id are required.', 'invalid_params');
        END IF;
        IF keep_id = remove_id THEN
            RETURN tool_error('Cannot merge a contact with itself.', 'invalid_params');
        END IF;
        IF NOT merge_contacts(keep_id, remove_id) THEN
            RETURN tool_error(format('Contact #%s not found.', remove_id), 'execution_failed');
        END IF;
        RETURN tool_success(jsonb_build_object('keep_id', keep_id, 'removed_id', remove_id, 'merged', true), format('Merged contact #%s into #%s', remove_id, keep_id));
    END IF;
    RETURN tool_error(format('Unsupported contact tool: %s', p_tool_name), 'invalid_params');
EXCEPTION WHEN OTHERS THEN
    RETURN tool_error(SQLERRM);
END;
$$;

CREATE OR REPLACE FUNCTION execute_memory_tool(
    p_tool_name TEXT,
    p_args JSONB
) RETURNS JSONB
LANGUAGE plpgsql
AS $$
DECLARE
    content TEXT;
    memory_type_value TEXT;
    importance_value FLOAT;
    memory_id UUID;
    query TEXT;
    limit_value INT;
    rows_json JSONB;
    type_filter memory_type[];
BEGIN
    IF p_tool_name = 'remember' THEN
        content := NULLIF(btrim(COALESCE(p_args->>'content', '')), '');
        IF content IS NULL THEN
            RETURN tool_error('content is required', 'invalid_params');
        END IF;
        memory_type_value := COALESCE(NULLIF(p_args->>'type', ''), 'episodic');
        IF memory_type_value NOT IN ('episodic', 'semantic', 'procedural', 'strategic') THEN
            RETURN tool_error(format('Invalid memory type: %s', memory_type_value), 'invalid_params');
        END IF;
        importance_value := LEAST(1.0, GREATEST(0.0, COALESCE(NULLIF(p_args->>'importance', '')::float, 0.5)));
        memory_id := create_memory(memory_type_value::memory_type, content, importance_value);
        IF jsonb_typeof(COALESCE(p_args->'concepts', '[]'::jsonb)) = 'array' THEN
            PERFORM link_memory_to_concept(memory_id, value)
            FROM jsonb_array_elements_text(p_args->'concepts') c(value);
        END IF;
        RETURN tool_success(jsonb_build_object('memory_id', memory_id::text, 'content', left(content, 100)), format('Stored memory: %s...', left(content, 50)));
    ELSIF p_tool_name = 'sense_memory_availability' THEN
        query := NULLIF(btrim(COALESCE(p_args->>'query', '')), '');
        IF query IS NULL THEN
            RETURN tool_error('query is required', 'invalid_params');
        END IF;
        SELECT to_jsonb(s) INTO rows_json FROM sense_memory_availability(query) s;
        RETURN tool_success(COALESCE(rows_json, '{"has_memories": false, "activation_strength": 0.0}'::jsonb), format('Memory availability: %s', COALESCE(rows_json->>'activation_strength', '0.0')));
    ELSIF p_tool_name = 'recall' THEN
        query := NULLIF(p_args->>'query', '');
        limit_value := LEAST(GREATEST(COALESCE(NULLIF(p_args->>'limit', '')::int, 5), 1), 50);
        IF p_args ? 'memory_types' THEN
            SELECT ARRAY(SELECT value::memory_type FROM jsonb_array_elements_text(p_args->'memory_types') t(value)) INTO type_filter;
        END IF;
        IF query IS NULL AND NOT (p_args ? 'memory_types' OR p_args ? 'source_path' OR p_args ? 'source_kind' OR p_args ? 'created_after' OR p_args ? 'created_before' OR p_args ? 'concept') THEN
            RETURN tool_error('Provide at least a query or one filter (memory_types, source_path, source_kind, created_after, created_before, concept).', 'invalid_params');
        END IF;
        SELECT COALESCE(jsonb_agg(jsonb_build_object(
            'memory_id', r.memory_id::text,
            'content', r.content,
            'type', r.memory_type::text,
            'score', r.score,
            'importance', r.importance,
            'source_attribution', r.source_attribution
        )), '[]'::jsonb)
        INTO rows_json
        FROM recall_memories_structured(
            query,
            limit_value,
            type_filter,
            COALESCE(NULLIF(p_args->>'min_importance', '')::float, 0.0),
            p_args->>'source_path',
            p_args->>'source_kind',
            NULLIF(p_args->>'created_after', '')::timestamptz,
            NULLIF(p_args->>'created_before', '')::timestamptz,
            p_args->>'concept',
            NULL
        ) r;
        PERFORM touch_memories(ARRAY(SELECT (value->>'memory_id')::uuid FROM jsonb_array_elements(rows_json) value));
        RETURN tool_success(jsonb_build_object('memories', rows_json, 'count', jsonb_array_length(rows_json), 'query', COALESCE(query, '(filters only)')), format('Found %s memories for %L', jsonb_array_length(rows_json), COALESCE(query, '(filters only)')));
    END IF;
    RETURN tool_error(format('Unsupported memory tool: %s', p_tool_name), 'invalid_params');
EXCEPTION WHEN OTHERS THEN
    RETURN tool_error(SQLERRM);
END;
$$;
