"""One OpenAI-shaped chat completion, for tests of the code that calls it."""

TEST_BASE_URL = "http://llm.test/v1"


def mock_llm_post(
    monkeypatch, *, finish_reason, content="answer", captured=None, usage=None,
):
    """Stub requests.post with one Chat Completions response.

    Also points ``ai_services.LLM_BASE_URL`` at a test host when none is set:
    ``_call_llm`` refuses to run unconfigured, and a test that stubs the HTTP
    call wants a configured backend. ``captured`` receives the payload, the
    URL and the headers of the request."""
    import requests

    import ai_services  # the flat module every test imports, not server.ai_services

    if not ai_services.LLM_BASE_URL:
        monkeypatch.setattr(ai_services, "LLM_BASE_URL", TEST_BASE_URL)

    usage = usage or {"prompt_tokens": 10, "completion_tokens": 400}

    class _Resp:
        status_code = 200

        def raise_for_status(self):
            pass

        def json(self):
            return {
                "choices": [{"message": {"content": content}, "finish_reason": finish_reason}],
                "usage": usage,
            }

    def _fake_post(url, headers=None, json=None, timeout=None):
        if captured is not None:
            captured["payload"] = json
            captured["url"] = url
            captured["headers"] = headers
        return _Resp()

    monkeypatch.setattr(requests, "post", _fake_post)
