"""模型客户端：环境变量、fixture，以及 strict JSON Schema 降级。不访问真实网络。"""
import asyncio
import json

import pytest

from app.process_ai.llm import (
    FixtureClient,
    LlmFailed,
    LlmNotConfigured,
    OpenAICompatibleClient,
    _chat_url,
    build_client,
)
from app.process_ai.schema import GEN_DRAFT_JSON_SCHEMA


def test_chat_url_joins_openai_compatible_base():
    assert _chat_url("https://example.com/v1") == "https://example.com/v1/chat/completions"
    assert _chat_url("https://example.com/v1/") == "https://example.com/v1/chat/completions"
    assert _chat_url("https://example.com/v1/chat/completions") == "https://example.com/v1/chat/completions"


def test_missing_env_does_not_build_a_network_client(monkeypatch):
    for key in ("XILIU_LLM_BASE_URL", "XILIU_LLM_API_KEY", "XILIU_LLM_MODEL", "XILIU_LLM_FIXTURE"):
        monkeypatch.delenv(key, raising=False)
    with pytest.raises(LlmNotConfigured):
        build_client()
    with pytest.raises(LlmFailed):
        OpenAICompatibleClient(base_url="ftp://keys.example/secret", api_key="secret-key", model="demo")


def test_fixture_file_is_returned_without_http(tmp_path):
    path = tmp_path / "draft.json"
    path.write_text(json.dumps({"rounds": [
        {"isProcess": False, "notProcessReason": "第一轮"},
        {"isProcess": True, "steps": []},
    ]}), encoding="utf-8")
    client = FixtureClient(str(path))
    first = asyncio.run(client.complete(messages=[], schema={}, timeout=1))
    second = asyncio.run(client.complete(messages=[], schema={}, timeout=1))
    assert first["notProcessReason"] == "第一轮"
    assert second["isProcess"] is True
    assert client.model == "fixture"


def test_strict_schema_falls_back_to_json_mode(monkeypatch):
    posts = []

    class FakeResponse:
        def __init__(self, status, payload=None, text=""):
            self.status_code = status
            self._payload = payload or {}
            self.text = text

        def json(self):
            return self._payload

    class FakeClient:
        def __init__(self, *args, **kwargs):
            del args, kwargs

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def post(self, url, headers, json):
            posts.append((url, headers, json))
            if json["response_format"]["type"] == "json_schema":
                return FakeResponse(400, text="response_format json_schema is unsupported")
            fenced = "```json\n" + __import__("json").dumps({
                "isProcess": False, "notProcessReason": "不是流程", "suggestedName": "", "summary": "", "steps": [],
            }) + "\n```"
            return FakeResponse(200, {"choices": [{"message": {"content": fenced}}]})

    monkeypatch.setattr("app.process_ai.llm.httpx.AsyncClient", FakeClient)
    client = OpenAICompatibleClient(base_url="https://llm.example/v1", api_key="secret-key", model="demo-model")
    payload = asyncio.run(client.complete(messages=[{"role": "user", "content": "hi"}],
                                          schema=GEN_DRAFT_JSON_SCHEMA, timeout=5))
    assert payload["notProcessReason"] == "不是流程"
    assert len(posts) == 2
    assert posts[0][0] == "https://llm.example/v1/chat/completions"
    assert posts[0][1]["Authorization"] == "Bearer secret-key"
    assert "secret-key" not in posts[0][0]
    assert posts[0][2]["temperature"] == 0.2
    assert posts[0][2]["model"] == "demo-model"
    assert posts[0][2]["response_format"]["json_schema"]["strict"] is True
    assert posts[1][2]["response_format"] == {"type": "json_object"}
    assert "secret-key" not in repr(client)
