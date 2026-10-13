"""OpenAI 兼容接口。密钥只从环境变量读取。"""
import json
import logging
import os
import re
from typing import Protocol
from urllib.parse import urlparse

import httpx

from .errors import LLM_NOT_CONFIGURED

logger = logging.getLogger(__name__)


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
    return OpenAICompatibleClient(base_url=base_url, api_key=api_key, model=model)


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
    def __init__(self, *, base_url: str, api_key: str, model: str):
        parsed = urlparse(base_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise LlmFailed()
        self._url = _chat_url(base_url)
        self._api_key = api_key
        self.model = model
        self._http_calls = 0

    def __repr__(self) -> str:
        return f"OpenAICompatibleClient(model={self.model})"

    async def complete(self, *, messages: list[dict], schema: dict, timeout: float) -> dict:
        if timeout <= 0:
            raise LlmTimeout()
        started = _now()
        response = await self._post(messages, _strict_format(schema), timeout)
        if _needs_json_mode(response):
            logger.info("ai draft llm json_schema unsupported model=%s", self.model)
            remaining = timeout - (_now() - started)
            if remaining <= 0:
                raise LlmTimeout()
            response = await self._post(messages, {"type": "json_object"}, remaining)
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

    async def _post(self, messages: list[dict], response_format: dict, timeout: float) -> httpx.Response:
        # 每个逻辑轮次最多一次 strict 和一次 json_object 降级；两轮合计不超过 4 次 HTTP。
        self._http_calls += 1
        if self._http_calls > 4:
            raise LlmFailed()
        request = {
            "model": self.model,
            "temperature": 0.2,
            "messages": messages,
            "response_format": response_format,
        }
        headers = {"Authorization": f"Bearer {self._api_key}", "Content-Type": "application/json"}
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(timeout)) as client:
                return await client.post(self._url, headers=headers, json=request)
        except httpx.TimeoutException as exc:
            raise LlmTimeout() from exc
        except httpx.HTTPError as exc:
            raise LlmFailed() from exc


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
