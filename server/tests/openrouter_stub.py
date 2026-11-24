"""One OpenRouter-shaped chat completion, for tests of the code that calls it."""


def mock_openrouter_post(
    monkeypatch, *, finish_reason, content="answer", captured=None, usage=None,
):
    """Stub requests.post with one OpenRouter-shaped chat completion."""
    import requests

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
        return _Resp()

    monkeypatch.setattr(requests, "post", _fake_post)
