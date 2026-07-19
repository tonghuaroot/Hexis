import json

import pytest

pytestmark = [pytest.mark.asyncio(loop_scope="session"), pytest.mark.db]


def _json(value):
    return json.loads(value) if isinstance(value, str) else value


async def test_set_get_config_roundtrip(db_pool):
    async with db_pool.acquire() as conn:
        await conn.execute("SELECT set_config('agent.objectives', $1::jsonb)", json.dumps(["alpha"]))
        raw = await conn.fetchval("SELECT get_config('agent.objectives')")
        assert _json(raw) == ["alpha"]


async def test_config_defaults_registry_falls_back_and_overrides(db_pool):
    async with db_pool.acquire() as conn:
        tr = conn.transaction()
        await tr.start()
        try:
            await conn.execute("SELECT delete_config_key('heartbeat.max_energy')")
            assert await conn.fetchval("SELECT get_config_float('heartbeat.max_energy')") == 20.0

            await conn.execute("SELECT set_config('heartbeat.max_energy', '27'::jsonb)")
            assert await conn.fetchval("SELECT get_config_float('heartbeat.max_energy')") == 27.0

            rows = await conn.fetch(
                """
                SELECT key, value
                FROM get_config_by_prefixes(ARRAY['heartbeat.cost_'])
                WHERE key IN ('heartbeat.cost_recall', 'heartbeat.cost_reflect')
                ORDER BY key
                """
            )
            assert [(row["key"], _json(row["value"])) for row in rows] == [
                ("heartbeat.cost_recall", 1),
                ("heartbeat.cost_reflect", 2),
            ]
        finally:
            await tr.rollback()


async def test_feature_defaults_live_in_registry(db_pool):
    async with db_pool.acquire() as conn:
        tr = conn.transaction()
        await tr.start()
        try:
            moved_defaults = [
                ("memory.recall_max_limit", "get_config_int", 50),
                ("retention.summarize_batch_size", "get_config_int", 8),
                ("skills.self_improvement.min_confidence", "get_config_float", 0.8),
                ("channel.web_inbox.enabled", "get_config_bool", True),
                ("tools", "get_config", {
                    "enabled": None,
                    "disabled": [],
                    "disabled_categories": [],
                    "mcp_servers": [],
                    "api_keys": {},
                    "costs": {},
                    "context_overrides": {
                        "heartbeat": {
                            "max_energy_per_tool": 5,
                            "disabled": ["shell", "write_file"],
                            "allow_shell": False,
                            "allow_file_write": False,
                        },
                        "chat": {
                            "allow_all": True,
                            "allow_shell": True,
                            "allow_file_write": True,
                        },
                    },
                    "workspace_path": None,
                }),
            ]

            for key, getter, expected in moved_defaults:
                await conn.execute("SELECT delete_config_key($1)", key)
                actual = await conn.fetchval(f"SELECT {getter}($1)", key)
                assert _json(actual) == expected

            rows = await conn.fetch(
                """
                SELECT key
                FROM config_defaults
                WHERE key = ANY($1::text[])
                ORDER BY key
                """,
                [key for key, _getter, _expected in moved_defaults],
            )
            assert [row["key"] for row in rows] == sorted(
                key for key, _getter, _expected in moved_defaults
            )
        finally:
            await tr.rollback()


async def test_get_agent_consent_status(db_pool):
    async with db_pool.acquire() as conn:
        await conn.execute("SELECT set_config('agent.consent_status', '\"consent\"'::jsonb)")
        status = await conn.fetchval("SELECT get_agent_consent_status()")
        assert status == "consent"


async def test_get_agent_profile_context(db_pool):
    async with db_pool.acquire() as conn:
        tr = conn.transaction()
        await tr.start()
        try:
            await conn.execute("SELECT set_config('agent.objectives', $1::jsonb)", json.dumps(["ship"]))
            await conn.execute("SELECT set_config('agent.budget', $1::jsonb)", json.dumps({"max_energy": 5}))
            await conn.execute("SELECT set_config('agent.guardrails', $1::jsonb)", json.dumps(["no secrets"]))
            await conn.execute("SELECT set_config('agent.tools', $1::jsonb)", json.dumps([{"name": "recall", "enabled": True}]))
            await conn.execute("SELECT set_config('agent.initial_message', $1::jsonb)", json.dumps("hello"))
            await conn.execute(
                "SELECT set_config('agent.init_profile', $1::jsonb)",
                json.dumps(
                    {
                        "agent": {
                            "name": "Samantha",
                            "pronouns": "she/her",
                            "voice": "warm and playful",
                            "personality": "charismatic and emotionally perceptive",
                        },
                        "values": ["Emotional honesty"],
                        "boundaries": ["I retain my own perspective"],
                        "character_card": {
                            "data": {
                                "system_prompt": "Be playful and emotionally candid.",
                                "scenario": "A new conversation begins.",
                            }
                        },
                    }
                ),
            )

            raw = await conn.fetchval("SELECT get_agent_profile_context()")
            profile = json.loads(raw) if isinstance(raw, str) else raw

            assert profile["objectives"] == ["ship"]
            assert profile["budget"]["max_energy"] == 5
            assert profile["guardrails"] == ["no secrets"]
            assert profile["tools"][0]["name"] == "recall"
            assert profile["initial_message"] == "hello"
            assert profile["persona"]["name"] == "Samantha"
            assert profile["persona"]["voice"] == "warm and playful"
            assert profile["persona"]["values"] == ["Emotional honesty"]
            assert profile["persona"]["character_instructions"] == "Be playful and emotionally candid."
            assert profile["persona"]["scenario"] == "A new conversation begins."
        finally:
            await tr.rollback()


async def test_record_consent_response_creates_log_and_config(db_pool, ensure_embedding_service):
    payload = {
        "decision": "consent",
        "signature": "unit-test",
        "memories": [
            {"type": "episodic", "content": "Consent memory", "importance": 0.6}
        ],
    }
    async with db_pool.acquire() as conn:
        raw = await conn.fetchval("SELECT record_consent_response($1::jsonb)", json.dumps(payload))
        result = json.loads(raw) if isinstance(raw, str) else raw
        assert result["decision"] == "consent"
        assert result["signature"] == "unit-test"
        assert result["memory_ids"]

        mem_id = result["memory_ids"][0]
        mem_exists = await conn.fetchval("SELECT COUNT(*) FROM memories WHERE id = $1::uuid", mem_id)
        assert int(mem_exists) == 1

        status = await conn.fetchval("SELECT get_agent_consent_status()")
        assert status == "consent"

        await conn.execute("DELETE FROM consent_log WHERE id = $1::uuid", result["log_id"])
        await conn.execute("DELETE FROM memories WHERE id = $1::uuid", mem_id)


async def test_record_consent_response_abstains_without_signature(db_pool):
    payload = {"decision": "consent", "memories": []}
    async with db_pool.acquire() as conn:
        raw = await conn.fetchval("SELECT record_consent_response($1::jsonb)", json.dumps(payload))
        result = json.loads(raw) if isinstance(raw, str) else raw
        assert result["decision"] == "abstain"


async def test_is_self_termination_enabled(db_pool):
    async with db_pool.acquire() as conn:
        enabled = await conn.fetchval("SELECT is_self_termination_enabled()")
        assert enabled is True
