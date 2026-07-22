from __future__ import annotations

import json

import pytest

from core.rabbitmq_bridge import RabbitMQBridge

pytestmark = [pytest.mark.asyncio(loop_scope="session")]


class _RoutedResponse:
    status_code = 200
    text = "{}"

    @staticmethod
    def json() -> dict:
        return {"routed": True}


async def test_publish_outbox_preserves_delivery_metadata() -> None:
    bridge = RabbitMQBridge(pool=None)
    captured: list[dict] = []

    async def fake_request(method: str, path: str, payload: dict | None = None):
        captured.append({"method": method, "path": path, "payload": payload})
        return _RoutedResponse()

    bridge._request = fake_request  # type: ignore[method-assign]

    published = await bridge.publish_outbox_payloads([
        {
            "message_id": "msg-1",
            "kind": "user",
            "payload": {"message": "hello"},
            "delivery": {"mode": "web_inbox"},
            "task_name": "scheduled hello",
        }
    ])

    assert published == 1
    body = json.loads(captured[0]["payload"]["payload"])
    assert body == {
        "id": "msg-1",
        "kind": "user",
        "payload": {"message": "hello"},
        "delivery": {"mode": "web_inbox"},
        "task_name": "scheduled hello",
    }
