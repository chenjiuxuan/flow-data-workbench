"""模型客户端：环境变量、fixture，以及 strict JSON Schema 降级。不访问真实网络。"""
import asyncio
import json
import logging

import httpx
import pytest

from app.process_ai.llm import (
    FixtureClient,
    LlmFailed,
    LlmNotConfigured,
    LlmTimeout,
    OpenAICompatibleClient,
    _chat_url,
    build_client,
    reset_reasoning_block,
    stream_counters,
)
from app.process_ai.schema import GEN_DRAFT_JSON_SCHEMA

_LLM_ENV = (
    "XILIU_LLM_BASE_URL", "XILIU_LLM_API_KEY", "XILIU_LLM_MODEL", "XILIU_LLM_FIXTURE",
    "XILIU_LLM_MAX_TOKENS", "XILIU_LLM_REASONING_EFFORT", "XILIU_LLM_TIMEOUT_S",
    "XILIU_LLM_FIRST_TOKEN_TIMEOUT_S", "XILIU_LLM_STALL_TIMEOUT_S",
)


@pytest.fixture(autouse=True)
def _reset_llm_state(monkeypatch):
    reset_reasoning_block()
    for key in _LLM_ENV:
        monkeypatch.delenv(key, raising=False)
    yield
    reset_reasoning_block()


def test_chat_url_joins_openai_compatible_base():
    assert _chat_url("https://example.com/v1") == "https://example.com/v1/chat/completions"
    assert _chat_url("https://example.com/v1/") == "https://example.com/v1/chat/completions"
    assert _chat_url("https://example.com/v1/chat/completions") == "https://example.com/v1/chat/completions"


def test_missing_env_does_not_build_a_network_client():
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
            self.text = text or (json.dumps(self._payload) if self._payload else "")
            self.headers = {"content-type": "application/json"}

        async def aread(self):
            return self.text.encode()

        def json(self):
            return self._payload

    class FakeClient:
        def __init__(self, *args, **kwargs):
            del args, kwargs

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        def stream(self, method, url, headers=None, json=None):
            del method
            return _FakeStream(self, url, headers, json)

        async def post(self, url, headers, body):
            posts.append((url, headers, body))
            if body["response_format"]["type"] == "json_schema":
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
    assert "max_tokens" not in posts[0][2]
    assert posts[0][2]["reasoning_effort"] == "none"
    assert posts[0][2]["stream"] is True
    assert "secret-key" not in repr(client)


class _FakeStream:
    def __init__(self, client, url, headers, body):
        self.client = client
        self.url = url
        self.headers = headers
        self.body = body

    async def __aenter__(self):
        return await self.client.post(self.url, self.headers, self.body)

    async def __aexit__(self, *args):
        return False


def _choice(payload=None, usage=None):
    body = {"choices": [{"message": {"content": json.dumps(payload or {"isProcess": False, "notProcessReason": "不是流程"})}}]}
    if usage is not None:
        body["usage"] = usage
    return body


def _client(transport, **kwargs):
    return OpenAICompatibleClient(
        base_url="https://llm.example/v1",
        api_key="secret-key",
        model="demo-model",
        transport=transport,
        **kwargs,
    )


def test_optional_params_are_omitted_when_unset():
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        return httpx.Response(200, json=_choice())

    asyncio.run(_client(httpx.MockTransport(handler)).complete(
        messages=[{"role": "user", "content": "hi"}], schema=GEN_DRAFT_JSON_SCHEMA, timeout=5,
    ))
    assert len(seen) == 1
    assert "max_tokens" not in seen[0]
    assert seen[0]["reasoning_effort"] == "none"
    assert seen[0]["stream"] is True
    assert seen[0]["temperature"] == 0.2


def test_optional_params_are_sent_as_given(monkeypatch):
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        return httpx.Response(200, json=_choice())

    asyncio.run(_client(
        httpx.MockTransport(handler), max_tokens=800, reasoning_effort="xhigh",
    ).complete(messages=[{"role": "user", "content": "hi"}], schema=GEN_DRAFT_JSON_SCHEMA, timeout=5))
    assert seen[0]["max_tokens"] == 800
    assert seen[0]["reasoning_effort"] == "xhigh"

    monkeypatch.setenv("XILIU_LLM_BASE_URL", "https://llm.example/v1")
    monkeypatch.setenv("XILIU_LLM_API_KEY", "secret-key")
    monkeypatch.setenv("XILIU_LLM_MODEL", "demo-model")
    assert build_client()._reasoning_effort == "none"
    monkeypatch.setenv("XILIU_LLM_MAX_TOKENS", "1500")
    monkeypatch.setenv("XILIU_LLM_REASONING_EFFORT", "minimal")
    built = build_client()
    assert built._max_tokens == 1500
    assert built._reasoning_effort == "minimal"
    monkeypatch.setenv("XILIU_LLM_MAX_TOKENS", "nope")
    assert build_client()._max_tokens is None


def test_reasoning_effort_is_dropped_after_rejection_and_skipped_next_time(monkeypatch, caplog):
    caplog.set_level(logging.INFO, logger="app.process_ai.llm")
    clock = {"now": 0.0}
    timeouts = []
    monkeypatch.setattr("app.process_ai.llm._now", lambda: clock["now"])

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        seen.append(body)
        clock["now"] += 1.5
        if "reasoning_effort" in body:
            return httpx.Response(400, json={
                "error": {"message": "unsupported reasoning_effort: minimal. supported: none, low, medium, high, xhigh, max"},
                "usage": {"prompt_tokens": 3},
            })
        return httpx.Response(200, json=_choice(usage={
            "prompt_tokens": 10,
            "completion_tokens": 20,
            "completion_tokens_details": {"reasoning_tokens": 4},
        }))

    seen = []
    original = httpx.AsyncClient

    class Recording(original):
        def __init__(self, *args, **kwargs):
            timeout = kwargs.get("timeout")
            timeouts.append(timeout.read if isinstance(timeout, httpx.Timeout) else timeout)
            super().__init__(*args, **kwargs)

    monkeypatch.setattr("app.process_ai.llm.httpx.AsyncClient", Recording)
    first = _client(httpx.MockTransport(handler), max_tokens=400, reasoning_effort="minimal")
    payload = asyncio.run(first.complete(
        messages=[{"role": "user", "content": "hi"}], schema=GEN_DRAFT_JSON_SCHEMA, timeout=5,
    ))
    assert payload["notProcessReason"] == "不是流程"
    assert seen[0]["reasoning_effort"] == "minimal"
    assert seen[0]["max_tokens"] == 400
    assert "reasoning_effort" not in seen[1]
    assert seen[1]["max_tokens"] == 400
    assert timeouts[0] == 5
    assert timeouts[1] == 3.5
    assert first.prompt_tokens == 13
    assert first.completion_tokens == 20
    assert first.reasoning_tokens == 4
    warning = next(record for record in caplog.records if record.levelno == logging.WARNING)
    assert "minimal" in warning.message
    assert any(
        record.levelno == logging.INFO and "latency_ms=" in record.message
        and "prompt_tokens=10" in record.message and "completion_tokens=20" in record.message
        and "reasoning_tokens=4" in record.message
        for record in caplog.records
    )

    seen.clear()
    clock["now"] = 0.0
    second = _client(httpx.MockTransport(handler), max_tokens=400, reasoning_effort="minimal")
    asyncio.run(second.complete(
        messages=[{"role": "user", "content": "hi"}], schema=GEN_DRAFT_JSON_SCHEMA, timeout=5,
    ))
    assert len(seen) == 1
    assert "reasoning_effort" not in seen[0]
    assert seen[0]["max_tokens"] == 400


def test_reasoning_retry_does_not_start_when_the_budget_is_spent(monkeypatch):
    clock = {"now": 0.0}
    monkeypatch.setattr("app.process_ai.llm._now", lambda: clock["now"])
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        clock["now"] += 5
        return httpx.Response(422, text="reasoning is not supported")

    with pytest.raises(LlmTimeout):
        asyncio.run(_client(httpx.MockTransport(handler), reasoning_effort="minimal").complete(
            messages=[{"role": "user", "content": "hi"}], schema={}, timeout=5,
        ))
    assert len(seen) == 1
    assert seen[0]["reasoning_effort"] == "minimal"


def _sse(content: str, usage: dict | None = None) -> bytes:
    payload: dict = {"choices": [{"delta": {"content": content}}]}
    if usage:
        payload["usage"] = usage
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\ndata: [DONE]\n\n".encode()


def _model_text() -> str:
    return json.dumps({"isProcess": False, "notProcessReason": "不是流程"}, ensure_ascii=False)


class _ScriptedStream(httpx.AsyncByteStream):
    def __init__(self, steps, events, name):
        self.steps = steps
        self.events = events
        self.name = name

    async def __aiter__(self):
        self.events.append((self.name, "open"))
        for kind, value in self.steps:
            if kind == "sleep":
                await asyncio.sleep(value)
            else:
                yield value
        self.events.append((self.name, "end"))

    async def aclose(self):
        self.events.append((self.name, "close"))


class _ScriptedTransport(httpx.AsyncBaseTransport):
    def __init__(self, responses):
        self._responses = list(responses)
        self.requests = []
        self.events = []

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.events.append(("http", len(self.requests)))
        self.requests.append(json.loads(request.content))
        return self._responses.pop(0)(self)


def _event_stream(steps, name):
    def build(transport: _ScriptedTransport):
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            stream=_ScriptedStream(steps, transport.events, name),
        )
    return build


def _assert_sequential(events):
    inflight = 0
    for event in events:
        if event[0] == "http":
            assert inflight == 0
            inflight = 1
        elif len(event) > 1 and event[1] == "close":
            inflight -= 1
            assert inflight == 0
    assert inflight == 0


def test_first_token_timeout_retries_once_and_succeeds(caplog):
    caplog.set_level(logging.INFO, logger="app.process_ai.llm")
    transport = _ScriptedTransport([
        _event_stream([("sleep", 1)], "slow"),
        _event_stream([("data", _sse(_model_text()))], "ok"),
    ])
    client = _client(transport, reasoning_effort="none", first_token_timeout=0.05, stall_timeout=0.05)
    payload = asyncio.run(client.complete(messages=[{"role": "user", "content": "hi"}], schema={}, timeout=2))
    assert payload["notProcessReason"] == "不是流程"
    assert len(transport.requests) == 2
    assert all(body["stream"] is True for body in transport.requests)
    _assert_sequential(transport.events)
    assert client.stalled is True
    assert client.retried is True
    assert client.retry_succeeded is True
    assert client.stream_attempts == 2
    assert client.stall_count == 1
    assert client.retry_count == 1
    assert client.retry_success_count == 1
    assert stream_counters()["stallCount"] == 1
    assert stream_counters()["streamAttempts"] == 2
    assert any("stalled=true" in record.message and "first_token_ms" not in record.message for record in caplog.records)
    assert any(
        "retried=true" in record.message and "retry_succeeded=true" in record.message and "first_token_ms=" in record.message
        and "total_ms=" in record.message
        for record in caplog.records
    )


def test_mid_stream_stall_retries_once():
    partial = f"data: {json.dumps({'choices': [{'delta': {'content': '{{'}}]})}\n\n".encode()
    transport = _ScriptedTransport([
        _event_stream([("data", partial), ("sleep", 1)], "partial"),
        _event_stream([("data", _sse(_model_text()))], "ok"),
    ])
    client = _client(transport, reasoning_effort="none", first_token_timeout=1, stall_timeout=0.05)
    payload = asyncio.run(client.complete(messages=[{"role": "user", "content": "hi"}], schema={}, timeout=2))
    assert payload["notProcessReason"] == "不是流程"
    assert len(transport.requests) == 2
    _assert_sequential(transport.events)
    assert client.stalled is True and client.retried is True and client.retry_succeeded is True
    assert client.stall_count == 1


def test_retry_stall_becomes_llm_timeout():
    transport = _ScriptedTransport([
        _event_stream([("sleep", 1)], "slow-1"),
        _event_stream([("sleep", 1)], "slow-2"),
    ])
    client = _client(transport, reasoning_effort="none", first_token_timeout=0.05, stall_timeout=0.05)
    with pytest.raises(LlmTimeout):
        asyncio.run(client.complete(messages=[{"role": "user", "content": "hi"}], schema={}, timeout=2))
    assert len(transport.requests) == 2
    _assert_sequential(transport.events)
    assert client.stalled is True
    assert client.retried is True
    assert client.retry_succeeded is False
    assert client.retry_success_count == 0
    assert client.stall_count == 2


def test_non_sse_and_stream_error_fall_back_and_are_cached():
    def unsupported(_transport):
        return httpx.Response(400, json={"error": {"message": "stream is not supported"}})

    def ok(_transport):
        return httpx.Response(200, json=_choice())

    transport = _ScriptedTransport([unsupported, ok, ok])
    client = _client(transport, reasoning_effort="none")
    first = asyncio.run(client.complete(messages=[{"role": "user", "content": "hi"}], schema={}, timeout=2))
    assert first["notProcessReason"] == "不是流程"
    assert transport.requests[0]["stream"] is True
    assert "stream" not in transport.requests[1]

    again = _client(transport, reasoning_effort="none")
    asyncio.run(again.complete(messages=[{"role": "user", "content": "hi"}], schema={}, timeout=2))
    assert "stream" not in transport.requests[2]


def test_non_sse_json_is_used_without_a_second_call():
    transport = _ScriptedTransport([
        lambda _transport: httpx.Response(200, json=_choice()),
        lambda _transport: httpx.Response(200, json=_choice()),
    ])
    client = _client(transport, reasoning_effort="none")
    payload = asyncio.run(client.complete(messages=[{"role": "user", "content": "hi"}], schema={}, timeout=2))
    assert payload["notProcessReason"] == "不是流程"
    assert len(transport.requests) == 1
    assert transport.requests[0]["stream"] is True
    nxt = _client(transport, reasoning_effort="none")
    asyncio.run(nxt.complete(messages=[{"role": "user", "content": "hi"}], schema={}, timeout=2))
    assert "stream" not in transport.requests[1]
