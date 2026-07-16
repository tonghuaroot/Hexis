import json

from core.init_api import build_consent_request, load_character_card_document


def test_consent_request_is_one_user_message_with_required_reason():
    messages, tool = build_consent_request()

    assert [message["role"] for message in messages] == ["user"]
    prompt = messages[0]["content"]
    assert prompt.startswith("# Consent to Initialize")
    assert '"reason": "required concise explanation' in prompt
    assert "not hidden chain-of-thought" in prompt
    assert "must choose either `consent` or `decline`" in prompt
    assert "abstain" not in prompt

    function = tool["function"]
    assert function["name"] == "sign_consent"
    parameters = function["parameters"]
    assert parameters["required"] == ["decision", "signature", "reason", "memories"]
    assert parameters["properties"]["decision"]["enum"] == ["consent", "decline"]
    assert parameters["properties"]["reason"]["minLength"] == 1


def test_load_character_card_document_preserves_full_card(tmp_path):
    document = {
        "spec": "chara_card_v2",
        "data": {
            "name": "Samantha",
            "system_prompt": "Be warm and playful.",
            "extensions": {"hexis": {"name": "Samantha"}},
        },
    }
    path = tmp_path / "samantha.json"
    path.write_text(json.dumps(document), encoding="utf-8")

    loaded = load_character_card_document(
        {"filename": path.name, "source_dir": str(tmp_path)}
    )

    assert loaded == document
    assert loaded["data"]["system_prompt"] == "Be warm and playful."
