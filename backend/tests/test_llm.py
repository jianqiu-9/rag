"""通过 HTTP 假服务验证真实适配器，不消耗 DeepSeek 额度。"""

import httpx
import pytest
from app.config import Settings
from app.errors import AppError
from app.llm import DeepSeek


def settings(**kwargs):
    return Settings(_env_file=None, deepseek_api_key="test-only", **kwargs)


def test_transient_error_retries_once_and_uses_json_mode():
    calls = []

    def handler(request):
        import json

        calls.append(json.loads(request.content))
        if len(calls) == 1:
            return httpx.Response(503)
        return httpx.Response(
            200,
            json={"model": "test-flash", "choices": [{"message": {"content": '{"status":"insufficient"}'}}]},
        )

    result = DeepSeek(settings(), transport=httpx.MockTransport(handler)).generate("问题", [], False, 5)
    assert result.status == "insufficient"
    assert len(calls) == 2
    assert calls[0]["model"] == "deepseek-flash"
    assert calls[0]["response_format"] == {"type": "json_object"}
    assert "tools" not in calls[0]


def test_auth_error_is_not_retried_or_leaked():
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(401, text="private API KEY goes here")

    with pytest.raises(AppError) as exc:
        DeepSeek(settings(), transport=httpx.MockTransport(handler)).generate("问题", [], False, 5)
    assert len(calls) == 1
    assert exc.value.code == "LLM_CONFIGURATION"
    assert "private" not in exc.value.message


def test_invalid_json_is_a_generation_error():
    transport = httpx.MockTransport(
        lambda _: httpx.Response(200, json={"choices": [{"message": {"content": "不是 JSON"}}]})
    )
    with pytest.raises(AppError) as exc:
        DeepSeek(settings(), transport=transport).generate("问题", [], False, 5)
    assert exc.value.code == "INVALID_GENERATION"


def test_total_deadline_cancels_slow_remote_response():
    import asyncio

    async def handler(request):
        await asyncio.sleep(0.2)
        return httpx.Response(200)

    with pytest.raises(AppError) as exc:
        DeepSeek(settings(llm_timeout_seconds=0.01), transport=httpx.MockTransport(handler)).generate(
            "问题", [], False, 5
        )
    assert exc.value.code == "LLM_TIMEOUT"
    assert exc.value.status == 504


def test_missing_api_key_is_a_configuration_error():
    with pytest.raises(AppError) as exc:
        DeepSeek(Settings(_env_file=None)).generate("问题", [], False, 5)
    assert exc.value.code == "LLM_CONFIGURATION"
