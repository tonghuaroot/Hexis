from __future__ import annotations

import pytest
from fastapi import HTTPException

from apps.hexis_api import IntegrationActionRequest, _integration_action_arguments


def test_integration_action_arguments_add_web_source_context() -> None:
    args = _integration_action_arguments(
        "start_setup",
        {"connector_id": "Telegram"},
        "web-connections",
    )

    assert args["connector_id"] == "telegram"
    assert args["source_channel"] == "web"
    assert args["source_session_id"] == "web-connections"


def test_integration_action_arguments_accept_channel_configure() -> None:
    args = _integration_action_arguments(
        "configure_channel",
        {
            "connector_id": "Slack",
            "settings": {
                "bot_token": "SLACK_BOT_TOKEN",
                "app_token": "SLACK_APP_TOKEN",
            },
        },
        "web-connections",
    )

    assert args == {
        "connector_id": "slack",
        "settings": {
            "bot_token": "SLACK_BOT_TOKEN",
            "app_token": "SLACK_APP_TOKEN",
        },
    }


def test_integration_action_request_accepts_legacy_flat_payload() -> None:
    request = IntegrationActionRequest(
        action="start_setup",
        connector_id="signal",
        source_session_id="web-connections",
    )

    assert request.model_extra == {"connector_id": "signal"}


def test_integration_action_arguments_reject_gmail_manual_setup() -> None:
    with pytest.raises(HTTPException) as excinfo:
        _integration_action_arguments(
            "start_setup",
            {"connector_id": "gmail"},
            "web-connections",
        )

    assert excinfo.value.status_code == 422
    assert "connect_gmail" in str(excinfo.value.detail)
