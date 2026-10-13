"""POST /api/process/ai/draft：生成流程初稿，不写数据库。"""
import asyncio
import json
import logging
import os
import time
from typing import Awaitable, Callable

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from pydantic import ValidationError
from sqlalchemy.orm import Session

from ..db import get_session
from .assets import catalog_rows, load_available_assets, prefilter_assets
from .compile import NotAProcess, compile_gen_draft
from .errors import (
    EMPTY_TEXT,
    INVALID_BOTH,
    INVALID_KIND,
    INVALID_MATCH,
    INVALID_NEITHER,
    LLM_FAILED,
    LLM_NOT_CONFIGURED,
    LLM_TIMEOUT,
    NOT_A_PROCESS,
    UNSUPPORTED_TYPE,
    DraftError,
)
from .extract import ALLOWED_EXTENSIONS, extension_of, extract_bytes, prepare_text, read_limited
from .llm import InvalidModelOutput, LlmFailed, LlmNotConfigured, LlmTimeout, build_client
from .prompt import build_messages, repair_messages
from .schema import GEN_DRAFT_JSON_SCHEMA, AiDraftResponseModel, GenDraft, InputKind

router = APIRouter(prefix="/api/process/ai")
logger = logging.getLogger(__name__)
BUDGET_SECONDS = 55.0  # XILIU_LLM_TIMEOUT_S 未设置时的总预算
_DISCONNECT_POLL_SECONDS = 0.5
_INPUT_KINDS = {"description", "minutes", "sop"}


@router.post("/draft", response_model=AiDraftResponseModel)
async def create_ai_draft(
    request: Request,
    text: str | None = Form(default=None),
    file: UploadFile | None = File(default=None),
    name: str | None = Form(default=None),
    matchAssets: str | None = Form(default=None),
    inputKind: str | None = Form(default=None),
    session: Session = Depends(get_session),
):
    started = time.monotonic()
    try:
        source_text, requested_name, match_assets, input_kind = await _read_request(
            text=text, file=file, name=name, match_assets=matchAssets, input_kind=inputKind,
        )
        # 先结束只读事务，避免生成期间占着 SQLite 连接。
        assets = load_available_assets(session) if match_assets else []
        session.rollback()
        if await _disconnected(request):
            raise DraftError(499, "llm_failed", "客户端已断开，已取消生成")
        prompted = prefilter_assets(assets, source_text) if match_assets else []
        rows, ref_map = catalog_rows(prompted) if match_assets else ([], {})
        payload = await generate_draft(
            source_text=source_text,
            requested_name=requested_name,
            input_kind=input_kind,
            match_assets=match_assets,
            assets=assets,
            ref_map=ref_map,
            catalog=rows,
            budget_seconds=budget_seconds(),
            is_disconnected=lambda: _disconnected(request),
        )
        payload["stats"]["latencyMs"] = max(0, int((time.monotonic() - started) * 1000))
        response = AiDraftResponseModel.model_validate(payload)
    except DraftError as exc:
        if exc.status == 499:
            logger.info(
                "ai draft cancelled by client elapsed_ms=%s",
                max(0, int((time.monotonic() - started) * 1000)),
            )
        elif exc.reason:
            logger.info("ai draft rejected code=%s status=%s reason=%s",
                        exc.code, exc.status, exc.reason[:300])
        else:
            logger.info("ai draft rejected code=%s status=%s", exc.code, exc.status)
        raise HTTPException(status_code=exc.status, detail=_error_detail(exc)) from exc
    logger.info(
        "ai draft ok nodes=%s edges=%s llm_calls=%s repaired=%s latency_ms=%s",
        len(response.draft.nodes), len(response.draft.edges),
        response.stats.llmCalls, response.stats.repaired, response.stats.latencyMs,
    )
    return response


async def generate_draft(*, source_text: str, requested_name: str | None, input_kind: InputKind,
                         match_assets: bool, assets: list[dict], ref_map: dict[str, dict],
                         catalog: list[dict], budget_seconds: float = BUDGET_SECONDS,
                         is_disconnected: Callable[[], Awaitable[bool]] | None = None,
                         client=None) -> dict:
    try:
        llm = client or build_client()
    except LlmNotConfigured as exc:
        raise DraftError(503, "llm_not_configured", LLM_NOT_CONFIGURED) from exc
    messages = build_messages(
        source_text=source_text,
        requested_name=requested_name,
        input_kind=input_kind,
        catalog=catalog,
        match_assets=match_assets,
    )
    deadline = time.monotonic() + budget_seconds
    calls = 0
    repaired = False
    previous = ""
    for attempt in (1, 2):
        if is_disconnected is not None and await is_disconnected():
            raise DraftError(499, "llm_failed", "客户端已断开，已取消生成")
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise DraftError(504, "llm_timeout", LLM_TIMEOUT)
        calls += 1
        try:
            raw = await _await_llm(
                asyncio.wait_for(
                    llm.complete(messages=messages, schema=GEN_DRAFT_JSON_SCHEMA, timeout=remaining),
                    timeout=remaining,
                ),
                is_disconnected,
            )
        except (LlmTimeout, TimeoutError):
            raise DraftError(504, "llm_timeout", LLM_TIMEOUT) from None
        except LlmNotConfigured as exc:
            raise DraftError(503, "llm_not_configured", LLM_NOT_CONFIGURED) from exc
        except LlmFailed as exc:
            raise DraftError(502, "llm_failed", LLM_FAILED) from exc
        except InvalidModelOutput as exc:
            if attempt == 2:
                raise DraftError(502, "llm_failed", "模型返回无法修复") from exc
            repaired = True
            previous = exc.raw
            messages = repair_messages(messages, error=str(exc), previous=previous)
            continue
        previous = _dump(raw)
        try:
            gen = GenDraft.model_validate(raw)
        except ValidationError as exc:
            if attempt == 2:
                raise DraftError(502, "llm_failed", "模型返回无法修复") from exc
            repaired = True
            messages = repair_messages(messages, error=str(exc), previous=previous)
            continue
        try:
            compiled = compile_gen_draft(
                gen,
                source_text=source_text,
                requested_name=requested_name,
                assets=assets,
                ref_map=ref_map,
                match_assets=match_assets,
            )
        except NotAProcess as exc:
            reason = " ".join((exc.reason or "").split())
            raise DraftError(422, "not_a_process", NOT_A_PROCESS, reason=reason or None) from exc
        except ValidationError as exc:
            if attempt == 2:
                raise DraftError(502, "llm_failed", "模型返回无法修复") from exc
            repaired = True
            messages = repair_messages(messages, error=str(exc), previous=previous)
            continue
        if calls not in (1, 2):
            raise DraftError(502, "llm_failed", LLM_FAILED)
        return {
            "draft": compiled.draft.model_dump(),
            "uncertainties": compiled.uncertainties,
            "assetSuggestions": compiled.asset_suggestions,
            "unmatchedAssets": compiled.unmatched_assets,
            "sourceText": source_text,
            "stats": _stats(llm, calls=calls, repaired=repaired),
        }
    raise DraftError(502, "llm_failed", "模型返回无法修复")


def budget_seconds() -> float:
    raw = os.environ.get("XILIU_LLM_TIMEOUT_S", "").strip()
    if not raw:
        return BUDGET_SECONDS
    try:
        value = float(raw)
    except ValueError:
        logger.warning("ai draft invalid XILIU_LLM_TIMEOUT_S=%s", raw)
        return BUDGET_SECONDS
    if value <= 0:
        logger.warning("ai draft invalid XILIU_LLM_TIMEOUT_S=%s", raw)
        return BUDGET_SECONDS
    return value


def _stats(llm, *, calls: int, repaired: bool) -> dict:
    stats = {
        "model": llm.model,
        "latencyMs": 0,
        "llmCalls": calls,
        "repaired": repaired,
    }
    for key, attr in (
        ("promptTokens", "prompt_tokens"),
        ("completionTokens", "completion_tokens"),
        ("reasoningTokens", "reasoning_tokens"),
        ("firstTokenMs", "first_token_ms"),
        ("totalMs", "total_ms"),
        ("stalled", "stalled"),
        ("retried", "retried"),
        ("retrySucceeded", "retry_succeeded"),
        ("streamAttempts", "stream_attempts"),
        ("stallCount", "stall_count"),
        ("retryCount", "retry_count"),
        ("retrySuccessCount", "retry_success_count"),
    ):
        value = getattr(llm, attr, None)
        if isinstance(value, bool) or (isinstance(value, int) and value is not None):
            stats[key] = value
    return stats


async def _read_request(*, text: str | None, file: UploadFile | None, name: str | None,
                        match_assets: str | None, input_kind: str | None):
    has_text = text is not None and bool(text.strip())
    has_file = file is not None and bool((file.filename or "").strip())
    try:
        if has_text and has_file:
            raise DraftError(422, "invalid_input", INVALID_BOTH)
        if not has_text and not has_file:
            if text is not None and not text.strip():
                raise DraftError(422, "empty_text", EMPTY_TEXT)
            raise DraftError(422, "invalid_input", INVALID_NEITHER)
        kind = (input_kind or "description").strip().lower()
        if kind not in _INPUT_KINDS:
            raise DraftError(422, "invalid_input", INVALID_KIND)
        match = _parse_match_assets(match_assets)
        requested = " ".join(name.split()).strip() if name and name.strip() else None
        if has_file:
            extension = extension_of(file.filename)
            if extension not in ALLOWED_EXTENSIONS:
                raise DraftError(415, "unsupported_type", UNSUPPORTED_TYPE)
            data = await read_limited(file)
            logger.info("ai draft file ext=%s bytes=%s", extension, len(data))
            source = prepare_text(extract_bytes(file.filename, data))
        else:
            source = prepare_text(text or "")
        return source, requested, match, kind
    finally:
        await _close_upload(file)


def _parse_match_assets(value: str | None) -> bool:
    if value is None or not value.strip():
        return True
    token = value.strip().lower()
    if token in {"true", "1", "yes"}:
        return True
    if token in {"false", "0", "no"}:
        return False
    raise DraftError(422, "invalid_input", INVALID_MATCH)


async def _await_llm(awaitable, is_disconnected: Callable[[], Awaitable[bool]] | None):
    """跑模型调用，同时轮询断开。取消任务会关掉 httpx 请求，并且不会进入修复轮。"""
    task = asyncio.create_task(awaitable, name="ai-draft-llm")
    try:
        while not task.done():
            if is_disconnected is not None and await is_disconnected():
                raise DraftError(499, "llm_failed", "客户端已断开，已取消生成")
            await asyncio.wait({task}, timeout=_DISCONNECT_POLL_SECONDS)
        return task.result()
    finally:
        if not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass


async def _disconnected(request: Request) -> bool:
    try:
        return await request.is_disconnected()
    except Exception:
        return False


async def _close_upload(file: UploadFile | None) -> None:
    if file is None:
        return
    try:
        await file.close()
    except Exception:
        return


def _error_detail(exc: DraftError) -> dict:
    detail = {"code": exc.code, "message": exc.message}
    if exc.reason:
        detail["reason"] = exc.reason
    return detail


def _dump(payload: dict) -> str:
    try:
        return json.dumps(payload, ensure_ascii=False)[:4000]
    except (TypeError, ValueError):
        return ""
