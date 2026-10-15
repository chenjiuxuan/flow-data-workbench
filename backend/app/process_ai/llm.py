"""OpenAI 兼容接口。密钥和可选参数只从环境变量读取。

未设置时不发送对应请求字段：
XILIU_LLM_MAX_TOKENS -> max_tokens，XILIU_LLM_REASONING_EFFORT -> reasoning_effort。
总预算 XILIU_LLM_TIMEOUT_S 在路由里读取，默认 55 秒。
"""
import json
import logging
import os
import re
from typing import Protocol
from urllib.parse import urlparse

import httpx

from .errors import LLM_NOT_CONFIGURED

logger = logging.getLogger(__name__)

# 进程内记住「该服务商拒绝 reasoning_effort」，避免后续请求再付一次往返。
_reasoning_blocked = False


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
    )


def reset_reasoning_block() -> None:
    """测试用：清掉进程内「不再发送 reasoning_effort」的记忆。"""
    global _reasoning_blocked
    _reasoning_blocked = False


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


def _reasoning_effort_from_env() -> str | None:
    raw = os.environ.get("XILIU_LLM_REASONING_EFFORT", "").strip()
    return raw or None


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
                 transport: httpx.BaseTransport | None = None):
        parsed = urlparse(base_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise LlmFailed()
        self._url = _chat_url(base_url)
        self._api_key = api_key
        self.model = model
        self._max_tokens = max_tokens if max_tokens and max_tokens > 0 else None
        self._reasoning_effort = (reasoning_effort or "").strip() or None
        self._transport = transport
        self._http_calls = 0
        self._sent_reasoning = False
        self.prompt_tokens: int | None = None
        self.completion_tokens: int | None = None
        self.reasoning_tokens: int | None = None

    def __repr__(self) -> str:
        return f"OpenAICompatibleClient(model={self.model})"

    async def complete(self, *, messages: list[dict], schema: dict, timeout: float) -> dict:
        if timeout <= 0:
            raise LlmTimeout()
        started = _now()
        strict = _strict_format(schema)
        response = await self._post(messages, strict, timeout)
        response = await self._retry_without_reasoning(response, messages, strict, timeout, started)
        if _needs_json_mode(response):
            logger.info("ai draft llm json_schema unsupported model=%s", self.model)
            remaining = timeout - (_now() - started)
            if remaining <= 0:
                raise LlmTimeout()
            plain = {"type": "json_object"}
            response = await self._post(messages, plain, remaining)
            response = await self._retry_without_reasoning(response, messages, plain, timeout, started)
        if response.status_code in {408, 504}:
            raise LlmTimeout()
        if response.status_code >= 400:
            logger.warning("ai draft llm http_status=%s model=%s", response.status_code, self.model)
            raise LlmFailed()
        try:
            payload = response.json()
        except json.JSONDecodeError as exc:
            raise InvalidModelOutput("模型返回的 JSON 无法解析") from exc
        content = _message_text(payload)
        return _parse_model_json(content)

    async def _retry_without_reasoning(self, response: httpx.Response, messages: list[dict],
                                       response_format: dict, timeout: float, started: float) -> httpx.Response:
        if not self._sent_reasoning or not _rejects_reasoning(response):
            return response
        logger.warning("ai draft llm reasoning_effort rejected value=%s", self._reasoning_effort)
        _block_reasoning()
        remaining = timeout - (_now() - started)
        if remaining <= 0:
            raise LlmTimeout()
        return await self._post(messages, response_format, remaining)

    async def _post(self, messages: list[dict], response_format: dict, timeout: float) -> httpx.Response:
        # 每个逻辑轮次最多 strict、去掉 reasoning_effort、json_object 各一次；两轮合计不超过 6 次 HTTP。
        self._http_calls += 1
        if self._http_calls > 6:
            raise LlmFailed()
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
        headers = {"Authorization": f"Bearer {self._api_key}", "Content-Type": "application/json"}
        client_kwargs: dict = {"timeout": httpx.Timeout(timeout)}
        if self._transport is not None:
            client_kwargs["transport"] = self._transport
        call_started = _now()
        try:
            async with httpx.AsyncClient(**client_kwargs) as client:
                response = await client.post(self._url, headers=headers, json=request)
        except httpx.TimeoutException as exc:
            logger.info(
                "ai draft llm call model=%s latency_ms=%s timed_out=1",
                self.model, _latency_ms(call_started),
            )
            raise LlmTimeout() from exc
        except httpx.HTTPError as exc:
            raise LlmFailed() from exc
        self._log_call(response, call_started)
        return response

    def _log_call(self, response: httpx.Response, call_started: float) -> None:
        counts = _usage_counts(response)
        self._add_usage(counts)
        parts = [f"model={self.model}", f"latency_ms={_latency_ms(call_started)}"]
        for key in ("prompt_tokens", "completion_tokens", "reasoning_tokens"):
            if key in counts:
                parts.append(f"{key}={counts[key]}")
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


def _needs_json_mode(response: httpx.Response) -> bool:
    if response.status_code not in {400, 422}:
        return False
    text = response.text.lower()
    return "response_format" in text or "json_schema" in text


def _rejects_reasoning(response: httpx.Response) -> bool:
    if response.status_code not in {400, 422}:
        return False
    text = response.text.lower()
    return "reasoning_effort" in text or "reasoning" in text


def _block_reasoning() -> None:
    global _reasoning_blocked
    _reasoning_blocked = True


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
