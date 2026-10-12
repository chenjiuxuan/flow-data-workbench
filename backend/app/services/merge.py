"""合并服务：把采集/导入产出的草稿并入图，同时保护人工修改。

规则：
- 新 id → 新建，origin=auto；
- 已存在 → 更新 attrs 中未被人工改过的键（provenance.manual_fields 记录人工改过的键），
  名称同理；位置永远以画布保存的为准；
- 完整快照中缺失的项只停用当前来源；所有来源都失活才 vanished=True；
- 部分快照只更新实际见到的项，不推断其他项已经消失；
- 人工新建（origin=manual）的项合并完全不碰。
"""
import json
from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy.orm import Session

from ..models import EdgeRecord, NodeRecord


@dataclass
class NodeDraft:
    id: str
    type: str
    name: str
    attrs: dict = field(default_factory=dict)
    x: float | None = None  # 仅新建时作为初始位置
    y: float | None = None


@dataclass
class EdgeDraft:
    id: str
    source_id: str
    target_id: str
    type: str = "flow"
    label: str = ""
    attrs: dict = field(default_factory=dict)


@dataclass
class MergeReport:
    source: str
    added_nodes: list[str] = field(default_factory=list)
    updated_nodes: list[str] = field(default_factory=list)
    added_edges: list[str] = field(default_factory=list)
    updated_edges: list[str] = field(default_factory=list)
    vanished_nodes: list[str] = field(default_factory=list)
    vanished_edges: list[str] = field(default_factory=list)
    protected_fields: list[str] = field(default_factory=list)  # 因人工修改被跳过的 "节点/边:字段"

    def to_dict(self) -> dict:
        return self.__dict__.copy()


def _source_states(provenance: dict, vanished: bool) -> dict:
    """兼容只有 sources 时间戳的旧备份，保留时间戳及原来源信息。

    旧的多来源 vanished 可能是单个来源错误触发，无法追溯各来源的状态。
    此时保守保留其他来源，等待它们各自的完整快照确认是否消失。
    """
    sources = provenance.setdefault("sources", {})
    states = provenance.setdefault("source_states", {})
    legacy_active = not vanished or len(sources) > 1
    for known_source, seen_at in sources.items():
        states.setdefault(known_source, {"active": legacy_active, "last_seen": seen_at})
    return states


def _touch(provenance: dict, source: str, vanished: bool, now_iso: str) -> None:
    states = _source_states(provenance, vanished)
    provenance["sources"][source] = now_iso
    states[source] = {"active": True, "last_seen": now_iso, "last_checked": now_iso}


def _mark_missing(session: Session, cls, source: str, draft_ids: set[str],
                  now_iso: str, vanished_ids: list[str], updated_ids: list[str]) -> None:
    for rec in session.query(cls).filter(cls.origin == "auto").all():
        prov = rec.provenance_dict
        if source not in prov.get("sources", {}) or rec.id in draft_ids:
            continue
        states = _source_states(prov, rec.vanished)
        states[source].update(active=False, last_checked=now_iso)
        vanished = not any(state.get("active", False) for state in states.values())
        if vanished != rec.vanished:
            (vanished_ids if vanished else updated_ids).append(rec.id)
        rec.vanished = vanished
        rec.provenance = json.dumps(prov, ensure_ascii=False)


def merge_drafts(session: Session, source: str, node_drafts: list[NodeDraft],
                 edge_drafts: list[EdgeDraft], *, complete: bool = True) -> MergeReport:
    """合并已确认的内容；complete=False 表示失败或部分采集，不做缺失判定。"""
    report = MergeReport(source=source)
    now_iso = datetime.now().isoformat(timespec="seconds")
    draft_node_ids = {d.id for d in node_drafts}
    draft_edge_ids = {d.id for d in edge_drafts}

    for draft in node_drafts:
        rec = session.get(NodeRecord, draft.id)
        if rec is None:
            prov = {}
            _touch(prov, source, False, now_iso)
            rec = NodeRecord(
                id=draft.id, type=draft.type, name=draft.name,
                attrs=json.dumps(draft.attrs, ensure_ascii=False),
                origin="auto", x=draft.x if draft.x is not None else 0.0,
                y=draft.y if draft.y is not None else 0.0,
                provenance=json.dumps(prov, ensure_ascii=False),
            )
            session.add(rec)
            report.added_nodes.append(draft.id)
            continue
        if rec.origin == "manual":
            continue

        prov = rec.provenance_dict
        manual_fields = set(prov.get("manual_fields", []))
        dirty = rec.vanished  # 重新出现要清除标记，也算一次有效更新
        protected = []

        for key, value in (("name", draft.name), ("type", draft.type)):
            if key in manual_fields:
                protected.append(key)
            elif getattr(rec, key) != value:
                setattr(rec, key, value)
                dirty = True
        attrs = rec.attrs_dict
        for key, value in draft.attrs.items():
            if key in manual_fields:
                protected.append(key)
                continue
            if key not in attrs or attrs[key] != value:
                attrs[key] = value
                dirty = True
        rec.attrs = json.dumps(attrs, ensure_ascii=False)
        _touch(prov, source, rec.vanished, now_iso)
        if rec.vanished:
            rec.vanished = False
        rec.provenance = json.dumps(prov, ensure_ascii=False)
        if protected:
            report.protected_fields.extend(f"{draft.id}:{k}" for k in protected)
        if dirty:
            report.updated_nodes.append(draft.id)

    for draft in edge_drafts:
        rec = session.get(EdgeRecord, draft.id)
        if rec is None:
            prov = {}
            _touch(prov, source, False, now_iso)
            rec = EdgeRecord(
                id=draft.id, source_id=draft.source_id, target_id=draft.target_id,
                type=draft.type, label=draft.label,
                attrs=json.dumps(draft.attrs, ensure_ascii=False), origin="auto",
                provenance=json.dumps(prov, ensure_ascii=False),
            )
            session.add(rec)
            report.added_edges.append(draft.id)
            continue
        if rec.origin == "manual":
            continue

        prov = rec.provenance_dict
        manual_fields = set(prov.get("manual_fields", []))
        dirty = rec.vanished
        protected = []
        if "label" not in manual_fields and draft.label and rec.label != draft.label:
            rec.label = draft.label
            dirty = True
        attrs = rec.attrs_dict
        for key, value in draft.attrs.items():
            if key in manual_fields:
                protected.append(key)
                continue
            if key not in attrs or attrs[key] != value:
                attrs[key] = value
                dirty = True
        rec.attrs = json.dumps(attrs, ensure_ascii=False)
        _touch(prov, source, rec.vanished, now_iso)
        if rec.vanished:
            rec.vanished = False
        rec.provenance = json.dumps(prov, ensure_ascii=False)
        if protected:
            report.protected_fields.extend(f"{draft.id}:{k}" for k in protected)
        if dirty:
            report.updated_edges.append(draft.id)

    if complete:
        _mark_missing(session, NodeRecord, source, draft_node_ids, now_iso,
                      report.vanished_nodes, report.updated_nodes)
        _mark_missing(session, EdgeRecord, source, draft_edge_ids, now_iso,
                      report.vanished_edges, report.updated_edges)

    session.commit()
    return report
