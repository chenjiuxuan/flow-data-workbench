"""把语义初稿编译成能通过 ProcessCreate 校验的格式稿。"""
import logging
from collections import defaultdict
from dataclasses import dataclass, field

from pydantic import ValidationError

from ..process_schemas import ProcessCreate, ProcessEdge, ProcessNode
from .assets import apply_preselect, dedupe_suggestions, name_matches
from .schema import GenDraft, GenStep

logger = logging.getLogger(__name__)

PENDING_PREFIX = "【待确认】"
NOTE_PREFIX = "AI 备注："
QUOTE_PREFIX = "原文依据："
# 留出补 start/end 和缺失分支的名额，保证节点数不超过 500。
MAX_MODEL_STEPS = 150
MIN_TASKS = 1

_KNOWN_KINDS = {
    "order", "missing_branch", "owner", "conflict",
    "missing_start", "missing_end", "unreachable", "other",
}

_KIND_WORDS = (
    ("conflict", ("矛盾", "冲突")),
    ("order", ("顺序", "先后", "次序")),
    ("owner", ("负责人", "岗位", "审批人")),
    ("missing_branch", ("分支",)),
    ("missing_start", ("开始",)),
    ("missing_end", ("结束",)),
)


class NotAProcess(Exception):
    def __init__(self, reason: str = ""):
        self.reason = reason
        super().__init__(reason or "未识别到流程")


@dataclass
class Note:
    kind: str
    reason: str
    source_quote: str = ""


@dataclass
class GNode:
    id: str
    type: str
    name: str
    owner: str = ""
    description: str = ""
    input: str = ""
    output: str = ""
    step: GenStep | None = None
    notes: list[Note] = field(default_factory=list)


@dataclass
class GEdge:
    id: str
    source: str
    target: str
    label: str = ""
    source_handle: str = "out"
    target_handle: str = "in"


@dataclass
class CompiledDraft:
    draft: ProcessCreate
    uncertainties: list[dict]
    asset_suggestions: list[dict]
    unmatched_assets: list[dict]


class _Ids:
    def __init__(self) -> None:
        self._node = 0
        self._edge = 0

    def node(self) -> str:
        self._node += 1
        return f"ai-n-{self._node}"

    def edge(self) -> str:
        self._edge += 1
        return f"ai-e-{self._edge}"


def compile_gen_draft(gen: GenDraft, *, source_text: str, requested_name: str | None,
                      assets: list[dict], ref_map: dict[str, dict],
                      match_assets: bool) -> CompiledDraft:
    if not gen.isProcess:
        raise NotAProcess(gen.notProcessReason)
    tasks = sum(1 for step in gen.steps if step.kind == "task")
    if tasks < MIN_TASKS:
        raise NotAProcess("识别到的处理步骤不足")

    ids = _Ids()
    steps = list(gen.steps[:MAX_MODEL_STEPS])
    truncated = len(gen.steps) > MAX_MODEL_STEPS
    nodes, key_to_id = _build_nodes(steps, ids)
    if truncated and nodes:
        nodes[-1].notes.append(Note("other", f"原文步骤较多，已保留前 {MAX_MODEL_STEPS} 步"))
    edges = _build_edges(nodes, key_to_id, ids)
    _fill_missing_branches(nodes, edges, ids)
    start_id = _ensure_terminals(nodes, ids)
    _link_unspecified_to_end(nodes, edges, ids)
    _connect_unreachable(nodes, edges, start_id, ids)
    edges = _sanitize_edges(nodes, edges, ids)
    _fill_missing_branches(nodes, edges, ids)
    _link_unspecified_to_end(nodes, edges, ids)
    _connect_unreachable(nodes, edges, start_id, ids)
    edges = _sanitize_edges(nodes, edges, ids)

    suggestions: list[dict] = []
    unmatched: list[dict] = []
    process_nodes: list[ProcessNode] = []
    for node in nodes:
        process_node, node_suggestions, node_unmatched = _finalize_node(
            node, source_text=source_text, assets=assets, ref_map=ref_map, match_assets=match_assets,
        )
        process_nodes.append(process_node)
        suggestions.extend(node_suggestions)
        unmatched.extend(node_unmatched)

    process_edges = [
        ProcessEdge(id=edge.id, source=edge.source, target=edge.target,
                    label=_clip(edge.label.strip(), 300),
                    sourceHandle=edge.source_handle, targetHandle="in")
        for edge in edges
    ]
    try:
        draft = ProcessCreate(
            name=_choose_name(requested_name, gen.suggestedName),
            description=_clip(gen.summary.strip(), 4000),
            nodes=process_nodes,
            edges=process_edges,
        )
    except ValidationError:
        logger.warning("ai draft compile failed validation nodes=%s edges=%s",
                       len(process_nodes), len(process_edges))
        raise
    return CompiledDraft(
        draft=draft,
        uncertainties=_uncertainties(nodes),
        asset_suggestions=apply_preselect(dedupe_suggestions(suggestions)),
        unmatched_assets=unmatched,
    )


def _build_nodes(steps: list[GenStep], ids: _Ids) -> tuple[list[GNode], dict[str, str]]:
    nodes: list[GNode] = []
    key_to_id: dict[str, str] = {}
    seen_start = False
    for index, step in enumerate(steps):
        key = step.key.strip()
        if not key or key in key_to_id:
            key = f"auto-{index + 1}"
            while key in key_to_id:
                key = f"{key}-x"
        kind = step.kind
        notes: list[Note] = []
        if kind == "start":
            if seen_start:
                kind = "task"
                notes.append(Note("other", "原文出现多个开始，已只保留第一个"))
            else:
                seen_start = True
        node = GNode(
            id=ids.node(),
            type=kind,
            name=_one_line(step.name) or "未命名步骤",
            owner=step.owner.strip(),
            description=step.description.strip(),
            input=step.input.strip(),
            output=step.output.strip(),
            step=step,
            notes=notes,
        )
        if _branch_target(step.yes) and _branch_target(step.no) and kind != "decision":
            node.type = "decision"
            notes.append(Note("other", "该步骤含有两个分支，已按判断节点处理"))
        if step.uncertain:
            node.notes.append(Note(
                _infer_kind(step.reason),
                step.reason.strip() or "该步骤需要人工确认",
                step.sourceQuote.strip(),
            ))
        if PENDING_PREFIX in node.name:
            node.notes.append(Note("other", "该步骤已被标为待确认"))
        nodes.append(node)
        key_to_id[key] = node.id
    return nodes, key_to_id


def _branch_target(branch) -> str:
    if branch is None:
        return ""
    return (branch.to or "").strip()


def _build_edges(nodes: list[GNode], key_to_id: dict[str, str], ids: _Ids) -> list[GEdge]:
    edges: list[GEdge] = []
    seen: set[tuple[str, str, str]] = set()
    for node in nodes:
        step = node.step
        if step is None:
            continue
        if node.type == "decision":
            for handle, branch, default_label in (("yes", step.yes, "是"), ("no", step.no, "否")):
                target_id = key_to_id.get(_branch_target(branch))
                if not target_id:
                    continue
                label = _one_line(branch.label if branch else "") or default_label
                _add_edge(edges, seen, ids, node.id, target_id, label, handle)
            continue
        target_key = (step.next or "").strip()
        if not target_key:
            yes_to = _branch_target(step.yes)
            no_to = _branch_target(step.no)
            if yes_to and not no_to:
                target_key = yes_to
            elif no_to and not yes_to:
                target_key = no_to
        target_id = key_to_id.get(target_key)
        if not target_id:
            continue
        _add_edge(edges, seen, ids, node.id, target_id, "", "out")
    return edges


def _add_edge(edges: list[GEdge], seen: set[tuple[str, str, str]], ids: _Ids,
              source: str, target: str, label: str, handle: str) -> None:
    marker = (source, target, handle)
    if marker in seen or source == target:
        return
    seen.add(marker)
    edges.append(GEdge(id=ids.edge(), source=source, target=target, label=label, source_handle=handle))


def _fill_missing_branches(nodes: list[GNode], edges: list[GEdge], ids: _Ids) -> None:
    present: dict[str, set[str]] = defaultdict(set)
    for edge in edges:
        if edge.source_handle in {"yes", "no"}:
            present[edge.source].add(edge.source_handle)
    existing_placeholder = next((node for node in nodes if node.name == "未说明的处理"), None)
    for node in list(nodes):
        if node.type != "decision":
            continue
        for handle, label in (("yes", "是"), ("no", "否")):
            if handle in present[node.id]:
                continue
            if len(nodes) >= 498 and existing_placeholder is not None:
                target = existing_placeholder
            else:
                target = GNode(id=ids.node(), type="task", name="未说明的处理")
                target.notes.append(Note("missing_branch", f"原文未说明「{label}」分支如何处理"))
                nodes.append(target)
                existing_placeholder = target
            if not any(edge.source == node.id and edge.target == target.id and edge.source_handle == handle
                       for edge in edges):
                edges.append(GEdge(id=ids.edge(), source=node.id, target=target.id,
                                   label=label, source_handle=handle))
            present[node.id].add(handle)


def _link_unspecified_to_end(nodes: list[GNode], edges: list[GEdge], ids: _Ids) -> None:
    """补上的「未说明的处理」不能断头，必须接到一个结束节点。"""
    pending = [node for node in nodes if node.step is None and any(
        note.kind == "missing_branch" for note in node.notes)]
    if not pending:
        return
    ends = [node for node in nodes if node.type == "end"]
    if not ends:
        end = GNode(id=ids.node(), type="end", name="结束")
        end.notes.append(Note("missing_end", "原文未写明结束"))
        nodes.append(end)
        ends = [end]
    end = ends[0]
    for node in pending:
        if node.id == end.id:
            continue
        if any(edge.source == node.id and edge.target == end.id for edge in edges):
            continue
        edges.append(GEdge(id=ids.edge(), source=node.id, target=end.id, source_handle="out"))


def _ensure_terminals(nodes: list[GNode], ids: _Ids) -> str:
    starts = [node for node in nodes if node.type == "start"]
    if not starts:
        start = GNode(id=ids.node(), type="start", name="开始")
        start.notes.append(Note("missing_start", "原文未写明开始"))
        nodes.insert(0, start)
    else:
        start = starts[0]
        for extra in starts[1:]:
            extra.type = "task"
            extra.notes.append(Note("other", "原文出现多个开始，已只保留第一个"))
    if not any(node.type == "end" for node in nodes):
        end = GNode(id=ids.node(), type="end", name="结束")
        end.notes.append(Note("missing_end", "原文未写明结束"))
        nodes.append(end)
    return start.id


def _connect_unreachable(nodes: list[GNode], edges: list[GEdge], start_id: str, ids: _Ids) -> None:
    guard = 0
    while guard <= len(nodes):
        seen = _reachable(start_id, edges)
        missing = [node for node in nodes if node.id not in seen]
        if not missing:
            return
        target = missing[0]
        if target.id == start_id:
            return
        edges.append(GEdge(id=ids.edge(), source=start_id, target=target.id, source_handle="out"))
        target.notes.append(Note("unreachable", "该步骤与主流程的衔接不明确"))
        guard += 1


def _reachable(start_id: str, edges: list[GEdge]) -> set[str]:
    adjacent: dict[str, list[str]] = defaultdict(list)
    for edge in edges:
        adjacent[edge.source].append(edge.target)
    seen: set[str] = set()
    stack = [start_id]
    while stack:
        current = stack.pop()
        if current in seen:
            continue
        seen.add(current)
        stack.extend(adjacent.get(current, []))
    return seen


def _sanitize_edges(nodes: list[GNode], edges: list[GEdge], ids: _Ids) -> list[GEdge]:
    types = {node.id: node.type for node in nodes}
    cleaned: list[GEdge] = []
    used_ids: set[str] = set()
    seen: set[tuple[str, str, str]] = set()
    for edge in edges:
        source_type = types.get(edge.source)
        target_type = types.get(edge.target)
        if source_type is None or target_type is None:
            continue
        if edge.source == edge.target or source_type == "end" or target_type == "start":
            continue
        handle = edge.source_handle
        if source_type == "decision":
            if handle not in {"yes", "no"}:
                continue
            if not edge.label.strip():
                edge.label = "是" if handle == "yes" else "否"
        else:
            handle = "out"
            edge.source_handle = "out"
        marker = (edge.source, edge.target, handle)
        if marker in seen:
            continue
        seen.add(marker)
        if edge.id in used_ids:
            edge.id = ids.edge()
        used_ids.add(edge.id)
        edge.target_handle = "in"
        cleaned.append(edge)
    return cleaned


def _finalize_node(node: GNode, *, source_text: str, assets: list[dict],
                   ref_map: dict[str, dict], match_assets: bool):
    owner, owner_note = _resolve_owner(node, source_text)
    if owner_note is not None:
        node.notes.append(owner_note)
    quote = _clean_quotes(node, source_text)
    pending = bool(node.notes)
    reasons: list[str] = []
    for note in node.notes:
        reason = _clip(note.reason.strip(), 300)
        if reason and reason not in reasons:
            reasons.append(reason)
    description = _with_note(node.description, reasons, quote, pending=pending)
    suggestions, unmatched_mentions = _collect_assets(
        node, assets=assets, ref_map=ref_map, match_assets=match_assets,
    )
    input_text = node.input
    output_text = node.output
    if match_assets and unmatched_mentions:
        input_text = _with_suffix(input_text, "未匹配资产：" + "、".join(unmatched_mentions), 2000)
    process_node = ProcessNode(
        id=node.id,
        type=node.type,  # type: ignore[arg-type]
        name=_clip_name(node.name, pending=pending),
        x=0.0,
        y=0.0,
        owner=_clip(owner, 300),
        description=description,
        input=_clip(input_text, 2000),
        output=_clip(output_text, 2000),
        assetIds=[],
    )
    unmatched = [{"nodeId": node.id, "mention": mention} for mention in unmatched_mentions]
    return process_node, suggestions, unmatched


def _resolve_owner(node: GNode, source_text: str) -> tuple[str, Note | None]:
    step = node.step
    raw = node.owner.strip()
    candidates: list[str] = []
    pool = [raw]
    if step is not None:
        pool.extend(item.strip() for item in step.ownerCandidates)
    for item in pool:
        if item and item in source_text and item not in candidates:
            candidates.append(item)
    if len(candidates) > 1:
        shown = "、".join(candidates[:5])
        return "", Note("owner", f"负责人有多个候选：{shown}")
    if len(candidates) == 1:
        if raw and raw not in source_text:
            return candidates[0], Note("owner", "负责人与原文不一致，已改用原文中的表述")
        return candidates[0], None
    if raw:
        return "", Note("owner", "负责人未在原文中出现")
    return "", None


def _clean_quotes(node: GNode, source_text: str) -> str:
    accepted = ""
    for note in node.notes:
        quote = note.source_quote.strip()
        if quote and quote in source_text:
            note.source_quote = quote[:500]
            if not accepted:
                accepted = note.source_quote
        else:
            note.source_quote = ""
    return accepted


def _collect_assets(node: GNode, *, assets: list[dict], ref_map: dict[str, dict], match_assets: bool):
    step = node.step
    mentions = []
    if step is not None:
        for mention in step.unmatchedMentions:
            cleaned = mention.strip()
            if cleaned and cleaned not in mentions:
                mentions.append(cleaned)
    if not match_assets:
        return [], mentions
    suggestions = []
    if step is not None:
        for ref in step.assetRefs:
            mention = ref.mention.strip()
            asset = ref_map.get(ref.ref.strip())
            if asset is None or not asset.get("id"):
                if mention and mention not in mentions:
                    mentions.append(mention)
                continue
            suggestions.append({
                "nodeId": node.id,
                "assetId": str(asset["id"]),
                "assetName": str(asset.get("name") or asset["id"]),
                "mention": mention or str(asset.get("name") or asset["id"]),
                "confidence": ref.confidence,
                "preselect": False,
            })
    haystack = "\n".join([
        node.name, node.description, node.input, node.output,
        *[note.source_quote for note in node.notes],
        *mentions,
        *[item["mention"] for item in suggestions],
    ])
    known = {(item["assetId"], item["mention"]) for item in suggestions}
    for item in name_matches(node.id, haystack, assets):
        if (item["assetId"], item["mention"]) not in known:
            suggestions.append(item)
    covered = {item["mention"] for item in suggestions}
    covered.update(item["assetName"] for item in suggestions)
    unmatched = [mention for mention in mentions if mention not in covered]
    return suggestions, unmatched


def _uncertainties(nodes: list[GNode]) -> list[dict]:
    items = []
    seen: set[tuple[str, str, str]] = set()
    for node in nodes:
        for note in node.notes:
            reason = note.reason.strip() or "需要人工确认"
            key = (node.id, note.kind, reason)
            if key in seen:
                continue
            seen.add(key)
            items.append({
                "nodeId": node.id,
                "kind": note.kind if note.kind in _KNOWN_KINDS else "other",
                "reason": reason,
                "sourceQuote": note.source_quote or "",
            })
    return items


def _infer_kind(reason: str) -> str:
    for kind, words in _KIND_WORDS:
        if any(word in reason for word in words):
            return kind
    return "other"


def _choose_name(requested: str | None, suggested: str) -> str:
    for candidate in (requested, suggested, "未命名流程"):
        cleaned = _one_line(candidate or "")
        if cleaned:
            return _clip(cleaned, 200)
    return "未命名流程"


def _clip_name(name: str, *, pending: bool) -> str:
    name = _one_line(name) or "未命名步骤"
    if pending and PENDING_PREFIX not in name:
        name = PENDING_PREFIX + name
    if len(name) <= 200:
        return name
    if name.startswith(PENDING_PREFIX):
        room = 200 - len(PENDING_PREFIX)
        return PENDING_PREFIX + name[len(PENDING_PREFIX):len(PENDING_PREFIX) + room]
    return name[:200]


def _with_note(description: str, reasons: list[str], quote: str, *, pending: bool) -> str:
    description = description.strip()
    if not pending:
        return _clip(description, 4000)
    reason = "、".join(reasons) or "需要人工确认"
    note = f"{NOTE_PREFIX}{reason}；{QUOTE_PREFIX}{quote}"
    return _with_suffix(description, note, 4000)


def _with_suffix(body: str, suffix: str, limit: int) -> str:
    body = body.strip()
    suffix = suffix.strip()
    if not suffix:
        return _clip(body, limit)
    if len(suffix) >= limit:
        return suffix[:limit]
    if not body:
        return suffix
    room = limit - len(suffix) - 1
    if room <= 0:
        return suffix[:limit]
    return body[:room] + "\n" + suffix


def _clip(value: str, limit: int) -> str:
    value = value.strip()
    return value[:limit]


def _one_line(value: str) -> str:
    return " ".join((value or "").split())
