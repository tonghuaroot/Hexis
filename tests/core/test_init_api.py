from core.init_api import build_consent_request


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
