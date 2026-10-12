"""独立流程文档 CRUD 与共享资产引用；所有写操作保留原数据流图。"""
import json
import logging
from datetime import datetime, timezone
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import delete, text, update
from sqlalchemy.orm import Session

from .api import _node_out
from .db import get_session
from .models import NodeRecord, ProcessDiagramRecord
from .process_schemas import ProcessCreate, ProcessUpdate

router = APIRouter(prefix="/api")
logger = logging.getLogger(__name__)
ASSET_TYPES = ("system", "external", "database", "table", "api", "file")


def _begin_write(session: Session) -> None:
    # 同步资产可用性检查和文档写入，避免检查后资产在本次提交前被删除。
    if session.get_bind().dialect.name == "sqlite":
        session.execute(text("BEGIN IMMEDIATE"))


def _content(record: ProcessDiagramRecord) -> dict:
    try:
        content = json.loads(record.content)
        if (not isinstance(content, dict) or not isinstance(content.get("nodes"), list)
                or not isinstance(content.get("edges"), list)):
            raise ValueError("invalid document shape")
        return content
    except (TypeError, ValueError) as exc:
        logger.error("Invalid persisted process document id=%s", record.id)
        raise HTTPException(500, "流程文档内容损坏，请从备份恢复") from exc


def _summary(record: ProcessDiagramRecord, content: dict | None = None) -> dict:
    content = _content(record) if content is None else content
    return {"id": record.id, "name": record.name, "description": record.description,
            "revision": record.revision, "nodeCount": len(content["nodes"]),
            "edgeCount": len(content["edges"]), "updatedAt": record.updated_at.isoformat()}


def _diagram(record: ProcessDiagramRecord) -> dict:
    content = _content(record)
    return _summary(record, content) | content


def _serialize(body: ProcessCreate) -> str:
    return json.dumps({"nodes": [node.model_dump() for node in body.nodes],
                       "edges": [edge.model_dump() for edge in body.edges]},
                      ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def _validate_assets(session: Session, body: ProcessCreate, previous: dict | None = None) -> None:
    # 旧引用按步骤 ID 保留。失效资产不能新增到另一步骤，也不能让新文档引用。
    old_assets = {node["id"]: set(node.get("assetIds", []))
                  for node in (previous or {}).get("nodes", [])}
    added = {asset_id for node in body.nodes for asset_id in node.assetIds
             if asset_id not in old_assets.get(node.id, set())}
    if not added:
        return
    available = {record.id for record in session.query(NodeRecord).filter(
        NodeRecord.id.in_(added), NodeRecord.type.in_(ASSET_TYPES), NodeRecord.vanished.is_(False))}
    if added - available:
        raise HTTPException(422, "新增关联的资产不存在或已失效，请刷新资产列表")


@router.get("/diagrams")
def list_diagrams(session: Session = Depends(get_session)):
    return [_summary(record) for record in session.query(ProcessDiagramRecord).order_by(
        ProcessDiagramRecord.updated_at.desc(), ProcessDiagramRecord.id).all()]


@router.post("/diagrams")
def create_diagram(body: ProcessCreate, session: Session = Depends(get_session)):
    _begin_write(session)
    _validate_assets(session, body)
    record = ProcessDiagramRecord(id=f"process:{uuid4().hex}", name=body.name,
                                  description=body.description, content=_serialize(body))
    session.add(record)
    session.commit()
    return _diagram(record)


@router.get("/diagrams/{diagram_id}")
def get_diagram(diagram_id: str, session: Session = Depends(get_session)):
    record = session.get(ProcessDiagramRecord, diagram_id)
    if record is None:
        raise HTTPException(404, "流程文档不存在")
    return _diagram(record)


@router.put("/diagrams/{diagram_id}")
def update_diagram(diagram_id: str, body: ProcessUpdate, session: Session = Depends(get_session)):
    _begin_write(session)
    record = session.get(ProcessDiagramRecord, diagram_id)
    if record is None:
        raise HTTPException(404, "流程文档不存在")
    if record.revision != body.expected_revision:
        raise HTTPException(409, "流程已被其他页面修改，请刷新后再编辑")
    _validate_assets(session, body, _content(record))
    revision = uuid4().hex
    result = session.execute(update(ProcessDiagramRecord).where(
        ProcessDiagramRecord.id == diagram_id,
        ProcessDiagramRecord.revision == body.expected_revision,
    ).values(name=body.name, description=body.description, content=_serialize(body),
             revision=revision, updated_at=datetime.now(timezone.utc)),
        execution_options={"synchronize_session": False})
    if result.rowcount != 1:
        session.rollback()
        raise HTTPException(409, "流程已被其他页面修改，请刷新后再编辑")
    session.refresh(record)
    saved = _diagram(record)
    session.commit()
    return saved


@router.delete("/diagrams/{diagram_id}")
def delete_diagram(diagram_id: str,
                   expected_revision: str = Query(min_length=1, max_length=64),
                   session: Session = Depends(get_session)):
    _begin_write(session)
    record = session.get(ProcessDiagramRecord, diagram_id)
    if record is None:
        raise HTTPException(404, "流程文档不存在")
    result = session.execute(delete(ProcessDiagramRecord).where(
        ProcessDiagramRecord.id == diagram_id, ProcessDiagramRecord.revision == expected_revision),
        execution_options={"synchronize_session": False})
    if result.rowcount != 1:
        session.rollback()
        raise HTTPException(409, "流程已被其他页面修改，请刷新后再删除")
    session.commit()
    return {"ok": True}


@router.get("/assets")
def list_assets(session: Session = Depends(get_session)):
    return [_node_out(record) for record in session.query(NodeRecord).filter(
        NodeRecord.type.in_(ASSET_TYPES), NodeRecord.vanished.is_(False)).order_by(
            NodeRecord.type, NodeRecord.name, NodeRecord.id).all()]


@router.get("/assets/{asset_id:path}/diagrams")
def asset_diagrams(asset_id: str, session: Session = Depends(get_session)):
    # 不要求资产仍存在，文档中保存的旧引用也能反查。
    result = []
    for record in session.query(ProcessDiagramRecord).order_by(ProcessDiagramRecord.name,
                                                              ProcessDiagramRecord.id):
        node_ids = [node["id"] for node in _content(record)["nodes"] if asset_id in node.get("assetIds", [])]
        if node_ids:
            result.append({"id": record.id, "name": record.name, "nodeIds": node_ids})
    return result
