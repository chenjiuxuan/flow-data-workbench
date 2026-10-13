"""OpenAI 兼容接口。密钥和可选参数只从环境变量读取。

未设置时不发送 max_tokens。reasoning_effort 未设置时默认 none。
流式优先；服务商不支持时本进程改为非流式。
总预算 XILIU_LLM_TIMEOUT_S 在路由里读取，默认 55 秒。
首 token 超时 XILIU_LLM_FIRST_TOKEN_TIMEOUT_S 默认 10 秒，
块间隔 XILIU_LLM_STALL_TIMEOUT_S 默认 15 秒。
"""
import asyncio
import json
import logging
import os
import re
from dataclasses import dataclass, field
from typing import Protocol
from urllib.parse import urlparse

import httpx

from .errors import LLM_NOT_CONFIGURED

logger = logging.getLogger(__name__)

# 进程内记住服务商能力，避免后续请求再付一次往返。
_reasoning_blocked = False
_stream_blocked = False
_counters = {"attempts": 0, "stalls": 0, "retries": 0, "retry_successes": 0}
_DEFAULT_FIRST_TOKEN_TIMEOUT = 10.0
_DEFAULT_STALL_TIMEOUT = 15.0


class LlmTimeout(Exception):
    pass


class LlmFailed(Exception):
    pass


class LlmNotConfigured(LlmFailed):
    def __init__(self):
        super().__init__(LLM_NOT_CONFIGURED)


class InvalidModelOutput(Exception):
    def __init__(self, message: str, raw: str = ""):
        self.raw = raw
        super().__init__(message)


class LlmClient(Protocol):
    model: str

    async def complete(self, *, messages: list[dict], schema: dict, timeout: float) -> dict: ...


def build_client() -> LlmClient:
    fixture = os.environ.get("XILIU_LLM_FIXTURE", "").strip()
    if fixture:
        return FixtureClient(fixture)
    base_url = os.environ.get("XILIU_LLM_BASE_URL", "").strip()
    api_key = os.environ.get("XILIU_LLM_API_KEY", "").strip()
    model = os.environ.get("XILIU_LLM_MODEL", "").strip()
    if not base_url or not api_key or not model:
        raise LlmNotConfigured()
    return OpenAICompatibleClient(
        base_url=base_url,
        api_key=api_key,
        model=model,
        max_tokens=_max_tokens_from_env(),
        reasoning_effort=_reasoning_effort_from_env(),
        first_token_timeout=_env_seconds("XILIU_LLM_FIRST_TOKEN_TIMEOUT_S", _DEFAULT_FIRST_TOKEN_TIMEOUT),
        stall_timeout=_env_seconds("XILIU_LLM_STALL_TIMEOUT_S", _DEFAULT_STALL_TIMEOUT),
    )


def reset_reasoning_block() -> None:
    """测试用：清掉进程内服务商能力记忆和流式计数。"""
    global _reasoning_blocked, _stream_blocked
    _reasoning_blocked = False
    _stream_blocked = False
    for key in _counters:
        _counters[key] = 0


def stream_counters() -> dict[str, int]:
    """进程内计数，stallCount / streamAttempts 即卡顿比例。"""
    return {
        "streamAttempts": _counters["attempts"],
        "stallCount": _counters["stalls"],
        "retryCount": _counters["retries"],
        "retrySuccessCount": _counters["retry_successes"],
    }


def _max_tokens_from_env() -> int | None:
    raw = os.environ.get("XILIU_LLM_MAX_TOKENS", "").strip()
    if not raw:
        return None
    try:
        value = int(raw)
    except ValueError:
        logger.warning("ai draft llm invalid XILIU_LLM_MAX_TOKENS=%s", raw)
        return None
    if value <= 0:
        logger.warning("ai draft llm invalid XILIU_LLM_MAX_TOKENS=%s", raw)
        return None
    return value


def _reasoning_effort_from_env() -> str:
    raw = os.environ.get("XILIU_LLM_REASONING_EFFORT", "").strip()
    return raw or "none"


def _env_seconds(name: str, default: float) -> float:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError:
        logger.warning("ai draft llm invalid %s=%s", name, raw)
        return default
    if value <= 0:
        logger.warning("ai draft llm invalid %s=%s", name, raw)
        return default
    return value


class _Stall(Exception):
    def __init__(self, kind: str, first_token_ms: int | None = None):
        self.kind = kind
        self.first_token_ms = first_token_ms
        super().__init__(kind)


class _StreamUnsupported(Exception):
    pass


@dataclass
class _Outcome:
    status_code: int
    text: str = ""
    content: str | None = None
    payload: dict | None = None
    usage: dict[str, int] = field(default_factory=dict)
    first_token_ms: int | None = None
    total_ms: int = 0


class FixtureClient:
    """CI 和无密钥环境：直接返回 JSON 文件里的中间稿，不访问网络。"""

    model = "fixture"

    def __init__(self, path: str):
        self.path = path
        self._rounds: list[dict] | None = None

    async def complete(self, *, messages: list[dict], schema: dict, timeout: float) -> dict:
        del messages, schema
        if timeout <= 0:
            raise LlmTimeout()
        if self._rounds is None:
            try:
                with open(self.path, encoding="utf-8") as handle:
                    payload = json.load(handle)
            except (OSError, json.JSONDecodeError) as exc:
                raise LlmFailed() from exc
            if isinstance(payload, dict) and isinstance(payload.get("rounds"), list):
                self._rounds = [item for item in payload["rounds"] if isinstance(item, dict)]
            elif isinstance(payload, dict):
                self._rounds = [payload]
            else:
                raise InvalidModelOutput("fixture 不是 JSON 对象")
        if not self._rounds:
            raise LlmFailed()
        return self._rounds.pop(0)


class OpenAICompatibleClient:
    def __init__(self, *, base_url: str, api_key: str, model: str,
                 max_tokens: int | None = None, reasoning_effort: str | None = None,
                 transport: httpx.BaseTransport | None = None,
                 first_token_timeout: float = _DEFAULT_FIRST_TOKEN_TIMEOUT,
                 stall_timeout: float = _DEFAULT_STALL_TIMEOUT):
        parsed = urlparse(base_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise LlmFailed()
        self._url = _chat_url(base_url)
        self._api_key = api_key
        self.model = model
        self._max_tokens = max_tokens if max_tokens and max_tokens > 0 else None
        self._reasoning_effort = "none" if reasoning_effort is None else (reasoning_effort.strip() or "none")
        self._transport = transport
        self._first_token_timeout = first_token_timeout if first_token_timeout > 0 else _DEFAULT_FIRST_TOKEN_TIMEOUT
        self._stall_timeout = stall_timeout if stall_timeout > 0 else _DEFAULT_STALL_TIMEOUT
        self._http_calls = 0
        self._sent_reasoning = False
        self._stall_retry_used = False
        self.prompt_tokens: int | None = None
        self.completion_tokens: int | None = None
        self.reasoning_tokens: int | None = None
        self.first_token_ms: int | None = None
        self.total_ms: int = 0
        self.stalled: bool = False
        self.retried: bool = False
        self.retry_succeeded: bool = False
        self.stream_attempts: int = 0
        self.stall_count: int = 0
        self.retry_count: int = 0
        self.retry_success_count: int = 0

    def __repr__(self) -> str:
        return f"OpenAICompatibleClient(model={self.model})"

    async def complete(self, *, messages: list[dict], schema: dict, timeout: float) -> dict:
        if timeout <= 0:
            raise LlmTimeout()
        started = _now()
        deadline = started + timeout
        self._stall_retry_used = False
        strict = _strict_format(schema)
        outcome = await self._attempt(messages, strict, deadline)
        outcome = await self._retry_without_reasoning(outcome, messages, strict, deadline)
        if _needs_json_mode(outcome):
            logger.info("ai draft llm json_schema unsupported model=%s", self.model)
            if deadline - _now() <= 0:
                raise LlmTimeout()
            plain = {"type": "json_object"}
            outcome = await self._attempt(messages, plain, deadline)
            outcome = await self._retry_without_reasoning(outcome, messages, plain, deadline)
        return self._finish(outcome)

    async def _retry_without_reasoning(self, outcome: _Outcome, messages: list[dict],
                                       response_format: dict, deadline: float) -> _Outcome:
        if not self._sent_reasoning or not _rejects_reasoning(outcome):
            return outcome
        logger.warning("ai draft llm reasoning_effort rejected value=%s", self._reasoning_effort)
        _block_reasoning()
        if deadline - _now() <= 0:
            raise LlmTimeout()
        return await self._attempt(messages, response_format, deadline)

    async def _attempt(self, messages: list[dict], response_format: dict, deadline: float) -> _Outcome:
        try:
            return await self._exchange(messages, response_format, deadline, is_retry=False)
        except _StreamUnsupported:
            _block_stream()
            logger.info("ai draft llm stream unsupported model=%s", self.model)
            return await self._exchange(messages, response_format, deadline, is_retry=False, force_plain=True)
        except _Stall:
            if self._stall_retry_used:
                raise LlmTimeout()
            self._stall_retry_used = True
            self.retried = True
            self.retry_count += 1
            _counters["retries"] += 1
            if deadline - _now() <= 0:
                raise LlmTimeout()
            try:
                outcome = await self._exchange(messages, response_format, deadline, is_retry=True)
            except _Stall:
                raise LlmTimeout() from None
            except _StreamUnsupported:
                _block_stream()
                logger.info("ai draft llm stream unsupported model=%s", self.model)
                outcome = await self._exchange(
                    messages, response_format, deadline, is_retry=True, force_plain=True,
                )
            if outcome.status_code < 400 and outcome.content is not None:
                self.retry_succeeded = True
                self.retry_success_count += 1
                _counters["retry_successes"] += 1
            return outcome

    async def _exchange(self, messages: list[dict], response_format: dict, deadline: float,
                        *, is_retry: bool, force_plain: bool = False) -> _Outcome:
        # 两轮逻辑调用，每轮还可能去掉 reasoning、降级 json、流式卡顿重试一次、以及非流式回退。
        self._http_calls += 1
        if self._http_calls > 12:
            raise LlmFailed()
        remaining = deadline - _now()
        if remaining <= 0:
            raise LlmTimeout()
        request = {
            "model": self.model,
            "temperature": 0.2,
            "messages": messages,
            "response_format": response_format,
        }
        if self._max_tokens is not None:
            request["max_tokens"] = self._max_tokens
        send_reasoning = bool(self._reasoning_effort) and not _reasoning_blocked
        self._sent_reasoning = send_reasoning
        if send_reasoning:
            request["reasoning_effort"] = self._reasoning_effort
        send_stream = not force_plain and not _stream_blocked
        if send_stream:
            request["stream"] = True
        headers = {"Authorization": f"Bearer {self._api_key}", "Content-Type": "application/json"}
        client_kwargs: dict = {"timeout": httpx.Timeout(remaining)}
        if self._transport is not None:
            client_kwargs["transport"] = self._transport
        call_started = _now()
        try:
            async with httpx.AsyncClient(**client_kwargs) as client:
                async with client.stream("POST", self._url, headers=headers, json=request) as response:
                    try:
                        outcome = await self._consume(response, call_started, deadline, send_stream)
                    except _Stall as exc:
                        self._record_call(
                            sent_stream=send_stream, stalled=True, retried=is_retry, retry_succeeded=False,
                            first_token_ms=exc.first_token_ms, total_ms=_latency_ms(call_started), usage={},
                        )
                        raise
        except httpx.TimeoutException as exc:
            self._record_call(
                sent_stream=send_stream, stalled=False, retried=is_retry, retry_succeeded=False,
                first_token_ms=None, total_ms=_latency_ms(call_started), usage={},
            )
            raise LlmTimeout() from exc
        except httpx.HTTPError as exc:
            raise LlmFailed() from exc
        outcome.total_ms = _latency_ms(call_started)
        retry_succeeded = is_retry and outcome.status_code < 400
        self._record_call(
            sent_stream=send_stream, stalled=False, retried=is_retry, retry_succeeded=retry_succeeded,
            first_token_ms=outcome.first_token_ms, total_ms=outcome.total_ms, usage=outcome.usage,
        )
        if send_stream and _body_rejects_stream(outcome) and outcome.status_code in {400, 422}:
            raise _StreamUnsupported()
        return outcome

    async def _consume(self, response: httpx.Response, call_started: float, deadline: float,
                       send_stream: bool) -> _Outcome:
        content_type = response.headers.get("content-type", "")
        if response.status_code >= 400 or not send_stream or "event-stream" not in content_type.lower():
            raw = await response.aread()
            text = raw.decode("utf-8", errors="replace")
            payload = _parse_json_object(text)
            if response.status_code < 400 and send_stream and "event-stream" not in content_type.lower():
                _block_stream()
                logger.info("ai draft llm non-sse response model=%s; caching plain mode", self.model)
            outcome = _Outcome(status_code=response.status_code, text=text, payload=payload, usage=_usage_from_payload(payload))
            if response.status_code < 400 and payload is not None:
                try:
                    outcome.content = _message_text(payload)
                    outcome.first_token_ms = _latency_ms(call_started)
                except InvalidModelOutput:
                    outcome.content = None
            return outcome
        return await self._read_sse(response, call_started, deadline)

    async def _read_sse(self, response: httpx.Response, call_started: float, deadline: float) -> _Outcome:
        lines = response.aiter_lines()
        parts: list[str] = []
        usage: dict[str, int] = {}
        got_content = False
        first_token_ms: int | None = None
        while True:
            now = _now()
            budget_left = deadline - now
            if budget_left <= 0:
                raise LlmTimeout()
            if not got_content:
                gap_limit = self._first_token_timeout - (now - call_started)
                if gap_limit <= 0:
                    raise _Stall("first_token")
            else:
                gap_limit = self._stall_timeout
            timeout = min(gap_limit, budget_left)
            try:
                line = await _next_line(lines, timeout)
            except StopAsyncIteration:
                break
            except TimeoutError:
                if budget_left <= gap_limit:
                    raise LlmTimeout()
                raise _Stall("stall" if got_content else "first_token", first_token_ms)
            text, chunk_usage, done = _apply_sse_line(line)
            if chunk_usage:
                usage.update(chunk_usage)
            if text:
                if not got_content:
                    got_content = True
                    first_token_ms = _latency_ms(call_started)
                parts.append(text)
            if done:
                break
        if not parts:
            raise InvalidModelOutput("模型没有返回内容")
        return _Outcome(
            status_code=response.status_code,
            content="".join(parts),
            usage=usage,
            first_token_ms=first_token_ms,
        )

    def _finish(self, outcome: _Outcome) -> dict:
        if outcome.status_code in {408, 504}:
            raise LlmTimeout()
        if outcome.status_code >= 400:
            logger.warning("ai draft llm http_status=%s model=%s", outcome.status_code, self.model)
            raise LlmFailed()
        if outcome.content is None:
            raise InvalidModelOutput("模型没有返回内容", raw=outcome.text[:4000])
        return _parse_model_json(outcome.content)

    def _record_call(self, *, sent_stream: bool, stalled: bool, retried: bool, retry_succeeded: bool,
                     first_token_ms: int | None, total_ms: int, usage: dict[str, int]) -> None:
        if sent_stream:
            self.stream_attempts += 1
            _counters["attempts"] += 1
        if stalled:
            self.stalled = True
            self.stall_count += 1
            _counters["stalls"] += 1
        if retried:
            self.retried = True
        if retry_succeeded:
            self.retry_succeeded = True
        if first_token_ms is not None:
            self.first_token_ms = first_token_ms
        self.total_ms += total_ms
        self._add_usage(usage)
        parts = [
            f"model={self.model}",
            f"total_ms={total_ms}",
            f"latency_ms={total_ms}",
            f"stalled={str(stalled).lower()}",
            f"retried={str(retried).lower()}",
            f"retry_succeeded={str(retry_succeeded).lower()}",
        ]
        if first_token_ms is not None:
            parts.insert(1, f"first_token_ms={first_token_ms}")
        for key in ("prompt_tokens", "completion_tokens", "reasoning_tokens"):
            if key in usage:
                parts.append(f"{key}={usage[key]}")
        logger.info("ai draft llm call %s", " ".join(parts))

    def _add_usage(self, counts: dict[str, int]) -> None:
        for key, attr in (
            ("prompt_tokens", "prompt_tokens"),
            ("completion_tokens", "completion_tokens"),
            ("reasoning_tokens", "reasoning_tokens"),
        ):
            if key not in counts:
                continue
            current = getattr(self, attr)
            setattr(self, attr, counts[key] if current is None else current + counts[key])


def _chat_url(base_url: str) -> str:
    trimmed = base_url.rstrip("/")
    if trimmed.endswith("/chat/completions"):
        return trimmed
    return trimmed + "/chat/completions"


def _strict_format(schema: dict) -> dict:
    return {
        "type": "json_schema",
        "json_schema": {"name": "gen_draft", "strict": True, "schema": schema},
    }


def _needs_json_mode(outcome: _Outcome) -> bool:
    if outcome.status_code not in {400, 422}:
        return False
    text = outcome.text.lower()
    return "response_format" in text or "json_schema" in text


def _rejects_reasoning(outcome: _Outcome) -> bool:
    if outcome.status_code not in {400, 422}:
        return False
    text = outcome.text.lower()
    return "reasoning_effort" in text or "reasoning" in text


def _body_rejects_stream(outcome: _Outcome) -> bool:
    if outcome.status_code not in {400, 422}:
        return False
    return "stream" in outcome.text.lower()


async def _next_line(lines, timeout: float) -> str:
    return await asyncio.wait_for(anext(lines), timeout)


def _parse_json_object(text: str) -> dict | None:
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, dict) else None


def _apply_sse_line(line: str) -> tuple[str, dict[str, int], bool]:
    stripped = line.strip()
    if not stripped.startswith("data:"):
        return "", {}, False
    data = stripped[5:].lstrip()
    if data == "[DONE]":
        return "", {}, True
    payload = _parse_json_object(data)
    if payload is None:
        return "", {}, False
    return _delta_text(payload), _usage_from_payload(payload), False


def _delta_text(payload: dict) -> str:
    choices = payload.get("choices") if isinstance(payload, dict) else None
    if not choices:
        return ""
    choice = choices[0] if isinstance(choices[0], dict) else {}
    for key in ("delta", "message"):
        body = choice.get(key)
        if isinstance(body, dict):
            text = _content_to_str(body.get("content"))
            if text:
                return text
    return ""


def _content_to_str(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict) and isinstance(item.get("text"), str):
                parts.append(item["text"])
        return "".join(parts)
    return ""


def _usage_from_payload(payload: dict | None) -> dict[str, int]:
    if not isinstance(payload, dict):
        return {}
    usage = payload.get("usage")
    if not isinstance(usage, dict):
        return {}
    counts: dict[str, int] = {}
    for key in ("prompt_tokens", "completion_tokens"):
        value = usage.get(key)
        if isinstance(value, int) and not isinstance(value, bool):
            counts[key] = value
    details = usage.get("completion_tokens_details")
    if isinstance(details, dict):
        reasoning = details.get("reasoning_tokens")
        if isinstance(reasoning, int) and not isinstance(reasoning, bool):
            counts["reasoning_tokens"] = reasoning
    return counts


def _block_reasoning() -> None:
    global _reasoning_blocked
    _reasoning_blocked = True


def _block_stream() -> None:
    global _stream_blocked
    _stream_blocked = True


def _usage_counts(response: httpx.Response) -> dict[str, int]:
    try:
        payload = response.json()
    except (json.JSONDecodeError, ValueError, UnicodeDecodeError):
        return {}
    usage = payload.get("usage") if isinstance(payload, dict) else None
    if not isinstance(usage, dict):
        return {}
    counts: dict[str, int] = {}
    for key in ("prompt_tokens", "completion_tokens"):
        value = usage.get(key)
        if isinstance(value, int) and not isinstance(value, bool):
            counts[key] = value
    details = usage.get("completion_tokens_details")
    if isinstance(details, dict):
        reasoning = details.get("reasoning_tokens")
        if isinstance(reasoning, int) and not isinstance(reasoning, bool):
            counts["reasoning_tokens"] = reasoning
    return counts


def _latency_ms(started: float) -> int:
    return max(0, int((_now() - started) * 1000))


def _message_text(payload: dict) -> str:
    choices = payload.get("choices") if isinstance(payload, dict) else None
    if not choices:
        raise InvalidModelOutput("模型没有返回内容")
    message = choices[0].get("message") or {}
    content = message.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict) and isinstance(item.get("text"), str):
                parts.append(item["text"])
        if parts:
            return "".join(parts)
    raise InvalidModelOutput("模型返回的内容不是文本")


def _parse_model_json(content: str) -> dict:
    text = content.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text, count=1, flags=re.IGNORECASE)
        text = re.sub(r"\s*```$", "", text)
    try:
        value = json.loads(text)
    except json.JSONDecodeError as exc:
        raise InvalidModelOutput("模型返回的 JSON 无法解析", raw=content[:4000]) from exc
    if not isinstance(value, dict):
        raise InvalidModelOutput("模型返回的 JSON 不是对象", raw=content[:4000])
    return value


def _now() -> float:
    import time
    return time.monotonic()
