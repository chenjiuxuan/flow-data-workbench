"""合并服务：把采集/导入产出的草稿并入图，同时保护人工修改。

规则：
- 新 id → 新建，origin=auto；
- 已存在 → 更新 attrs 中未被人工改过的键（provenance.manual_fields 记录人工改过的键），
  名称同理；位置永远以画布保存的为准；
- 本来源上一轮见过、这一轮没见到的自动项 → vanished=True（打标不删除）；
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


def _touch(provenance: dict, source: str) -> dict:
    sources = provenance.setdefault("sources", {})
    sources[source] = datetime.now().isoformat(timespec="seconds")
    return provenance


def merge_drafts(session: Session, source: str, node_drafts: list[NodeDraft],
                 edge_drafts: list[EdgeDraft]) -> MergeReport:
    report = MergeReport(source=source)
    now_iso = datetime.now().isoformat(timespec="seconds")
    draft_node_ids = {d.id for d in node_drafts}
    draft_edge_ids = {d.id for d in edge_drafts}

    for draft in node_drafts:
        rec = session.get(NodeRecord, draft.id)
        if rec is None:
            rec = NodeRecord(
                id=draft.id, type=draft.type, name=draft.name,
                attrs=json.dumps(draft.attrs, ensure_ascii=False),
                origin="auto", x=draft.x if draft.x is not None else 0.0,
                y=draft.y if draft.y is not None else 0.0,
                provenance=json.dumps({"sources": {source: now_iso}}, ensure_ascii=False),
            )
            session.add(rec)
            report.added_nodes.append(draft.id)
            continue

        prov = rec.provenance_dict
        manual_fields = set(prov.get("manual_fields", []))
        was_auto = rec.origin == "auto"
        dirty = rec.vanished  # 重新出现要清除标记，也算一次有效更新
        protected = []

        if was_auto:
            if "name" not in manual_fields and rec.name != draft.name:
                rec.name = draft.name
                dirty = True
            for key, value in draft.attrs.items():
                if key in manual_fields:
                    protected.append(key)
                    continue
                if rec.attrs_dict.get(key) != value:
                    attrs = rec.attrs_dict
                    attrs[key] = value
                    rec.attrs = json.dumps(attrs, ensure_ascii=False)
                    dirty = True
        if rec.vanished:
            rec.vanished = False
        _touch(prov, source)
        rec.provenance = json.dumps(prov, ensure_ascii=False)
        if protected:
            report.protected_fields.extend(f"{draft.id}:{k}" for k in protected)
        if dirty:
            report.updated_nodes.append(draft.id)

    for draft in edge_drafts:
        rec = session.get(EdgeRecord, draft.id)
        if rec is None:
            rec = EdgeRecord(
                id=draft.id, source_id=draft.source_id, target_id=draft.target_id,
                type=draft.type, label=draft.label,
                attrs=json.dumps(draft.attrs, ensure_ascii=False), origin="auto",
                provenance=json.dumps({"sources": {source: now_iso}}, ensure_ascii=False),
            )
            session.add(rec)
            report.added_edges.append(draft.id)
            continue

        prov = rec.provenance_dict
        manual_fields = set(prov.get("manual_fields", []))
        dirty = rec.vanished
        protected = []
        if rec.origin == "auto":
            if "label" not in manual_fields and draft.label and rec.label != draft.label:
                rec.label = draft.label
                dirty = True
            for key, value in draft.attrs.items():
                if key in manual_fields:
                    protected.append(key)
                    continue
                if rec.attrs_dict.get(key) != value:
                    attrs = rec.attrs_dict
                    attrs[key] = value
                    rec.attrs = json.dumps(attrs, ensure_ascii=False)
                    dirty = True
        if rec.vanished:
            rec.vanished = False
        _touch(prov, source)
        rec.provenance = json.dumps(prov, ensure_ascii=False)
        if protected:
            report.protected_fields.extend(f"{draft.id}:{k}" for k in protected)
        if dirty:
            report.updated_edges.append(draft.id)

    # 本来源之前报过、这一轮草稿里没有的自动项 → 打"已消失"标记（打标不删除）
    for rec in session.query(NodeRecord).filter(NodeRecord.origin == "auto", NodeRecord.vanished == False).all():  # noqa: E712
        prov = rec.provenance_dict
        if source in prov.get("sources", {}) and rec.id not in draft_node_ids:
            rec.vanished = True
            report.vanished_nodes.append(rec.id)

    for rec in session.query(EdgeRecord).filter(EdgeRecord.origin == "auto", EdgeRecord.vanished == False).all():  # noqa: E712
        prov = rec.provenance_dict
        if source in prov.get("sources", {}) and rec.id not in draft_edge_ids:
            rec.vanished = True
            report.vanished_edges.append(rec.id)

    session.commit()
    return report
