"""REST API 路由。"""
import hashlib
import json
import logging

from fastapi import APIRouter, Depends, HTTPException, UploadFile
from fastapi.responses import Response
from sqlalchemy.orm import Session
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from . import schemas
from .db import get_session
from .models import EdgeRecord, NodeRecord
from .services import db_introspect, excel_import, graph as graph_service
from .services import sql_lineage
from .services.demo_seed import demo_drafts
from .services.ids import database_source, manual_flow_id, sys_id
from .services.merge import EdgeDraft, NodeDraft, merge_drafts

router = APIRouter(prefix="/api")
logger = logging.getLogger(__name__)
MAX_EXCEL_UPLOAD_BYTES = 10 * 1024 * 1024


@router.get("/health")
def health(session: Session = Depends(get_session)):
    try:
        session.execute(text("SELECT 1"))
    except SQLAlchemyError as exc:
        logger.warning("health database unavailable error_type=%s", type(exc).__name__)
        raise HTTPException(503, "数据库暂不可用") from exc
    return {"status": "ok", "database": "ok"}


def _node_out(rec: NodeRecord) -> dict:
    return {
        "id": rec.id, "type": rec.type, "name": rec.name,
        "attrs": rec.attrs_dict, "origin": rec.origin, "vanished": rec.vanished,
        "x": rec.x, "y": rec.y,
        "manualFields": rec.provenance_dict.get("manual_fields", []),
    }


def _edge_out(rec: EdgeRecord) -> dict:
    return {
        "id": rec.id, "source": rec.source_id, "target": rec.target_id,
        "type": rec.type, "label": rec.label, "attrs": rec.attrs_dict,
        "origin": rec.origin, "vanished": rec.vanished,
        "manualFields": rec.provenance_dict.get("manual_fields", []),
    }


def _mark_manual(provenance: dict, keys: list[str]) -> None:
    fields = set(provenance.setdefault("manual_fields", []))
    fields.update(keys)
    provenance["manual_fields"] = sorted(fields)


def _snapshot(session: Session) -> dict:
    """完整图的规范化快照；revision 仅由持久化内容决定。"""
    payload = {
        "nodes": [_node_out(n) | {"provenance": n.provenance_dict}
                  for n in session.query(NodeRecord).order_by(NodeRecord.id).all()],
        "edges": [_edge_out(e) | {"provenance": e.provenance_dict}
                  for e in session.query(EdgeRecord).order_by(EdgeRecord.id).all()],
    }
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True,
                           separators=(",", ":"), allow_nan=False)
    return payload | {"revision": hashlib.sha256(canonical.encode("utf-8")).hexdigest()}


def _begin_snapshot(session: Session, *, write: bool = False) -> None:
    # pysqlite 的 SELECT 默认不启动数据库事务。显式 BEGIN 让两次表查询共享
    # 同一快照；IMMEDIATE 将 revision 校验和写入置于同一写锁内，防止并发覆盖。
    if session.get_bind().dialect.name == "sqlite":
        session.execute(text("BEGIN IMMEDIATE" if write else "BEGIN"))


# ---------- 图查询 ----------

@router.get("/graph")
def get_graph(view: str = "system", include_vanished: bool = False,
              session: Session = Depends(get_session)):
    nodes, edges = graph_service.get_graph(session, view, include_vanished)
    return {"nodes": [_node_out(n) for n in nodes], "edges": [_edge_out(e) for e in edges]}


@router.get("/trace/{node_id}")
def trace(node_id: str, direction: str = "both", session: Session = Depends(get_session)):
    nodes, edges = graph_service.trace(session, node_id, direction)
    return {"nodes": [_node_out(n) for n in nodes], "edges": [_edge_out(e) for e in edges]}


# ---------- 节点 ----------

@router.post("/nodes")
def create_node(body: schemas.NodeIn, session: Session = Depends(get_session)):
    node_id = body.id or sys_id(body.name)
    if session.get(NodeRecord, node_id):
        raise HTTPException(409, f"节点已存在：{node_id}")
    rec = NodeRecord(id=node_id, type=body.type, name=body.name, x=body.x, y=body.y,
                     attrs=json.dumps(body.attrs, ensure_ascii=False), origin="manual")
    session.add(rec)
    session.commit()
    return _node_out(rec)


@router.patch("/nodes/{node_id}")
def patch_node(node_id: str, body: schemas.NodePatch, session: Session = Depends(get_session)):
    rec = session.get(NodeRecord, node_id)
    if rec is None:
        raise HTTPException(404, f"节点不存在：{node_id}")
    changed = []
    if body.name is not None and body.name != rec.name:
        rec.name = body.name
        changed.append("name")
    if body.attrs is not None:
        old = rec.attrs_dict
        for key, value in body.attrs.items():
            if value is None:  # null 表示清除该属性
                if key in old:
                    del old[key]
                    changed.append(key)
                continue
            if old.get(key) != value:
                changed.append(key)
            old[key] = value
        rec.attrs = json.dumps(old, ensure_ascii=False)
    if body.x is not None:
        rec.x = body.x
    if body.y is not None:
        rec.y = body.y
    if body.vanished is not None:
        rec.vanished = body.vanished
    if body.manual and changed:
        prov = rec.provenance_dict
        _mark_manual(prov, changed)
        rec.provenance = json.dumps(prov, ensure_ascii=False)
    session.commit()
    return _node_out(rec)


@router.delete("/nodes/{node_id}")
def delete_node(node_id: str, session: Session = Depends(get_session)):
    rec = session.get(NodeRecord, node_id)
    if rec is None:
        raise HTTPException(404, f"节点不存在：{node_id}")
    session.query(EdgeRecord).filter(
        (EdgeRecord.source_id == node_id) | (EdgeRecord.target_id == node_id)).delete()
    session.delete(rec)
    session.commit()
    return {"ok": True}


# ---------- 边 ----------

@router.post("/edges")
def create_edge(body: schemas.EdgeIn, session: Session = Depends(get_session)):
    if not session.get(NodeRecord, body.source_id) or not session.get(NodeRecord, body.target_id):
        raise HTTPException(404, "边的端点节点不存在，请先创建节点")
    edge_id = body.id or manual_flow_id(body.source_id, body.target_id, body.label, body.type)
    if session.get(EdgeRecord, edge_id):
        raise HTTPException(409, f"连线已存在：{edge_id}")
    rec = EdgeRecord(id=edge_id, source_id=body.source_id, target_id=body.target_id,
                     type=body.type, label=body.label,
                     attrs=json.dumps(body.attrs, ensure_ascii=False), origin="manual")
    session.add(rec)
    session.commit()
    return _edge_out(rec)


@router.patch("/edges/{edge_id}")
def patch_edge(edge_id: str, body: schemas.EdgePatch, session: Session = Depends(get_session)):
    rec = session.get(EdgeRecord, edge_id)
    if rec is None:
        raise HTTPException(404, f"连线不存在：{edge_id}")
    changed = []
    if body.label is not None and body.label != rec.label:
        rec.label = body.label
        changed.append("label")
    if body.attrs is not None:
        old = rec.attrs_dict
        for key, value in body.attrs.items():
            if value is None:  # null 表示清除该属性
                if key in old:
                    del old[key]
                    changed.append(key)
                continue
            if old.get(key) != value:
                changed.append(key)
            old[key] = value
        rec.attrs = json.dumps(old, ensure_ascii=False)
    if body.manual and changed:
        prov = rec.provenance_dict
        _mark_manual(prov, changed)
        rec.provenance = json.dumps(prov, ensure_ascii=False)
    session.commit()
    return _edge_out(rec)


@router.delete("/edges/{edge_id}")
def delete_edge(edge_id: str, session: Session = Depends(get_session)):
    rec = session.get(EdgeRecord, edge_id)
    if rec is None:
        raise HTTPException(404, f"连线不存在：{edge_id}")
    session.delete(rec)
    session.commit()
    return {"ok": True}


# ---------- 布局 ----------

@router.post("/layout")
def save_layout(body: schemas.LayoutIn, session: Session = Depends(get_session)):
    count = 0
    for node_id, (x, y) in body.positions.items():
        rec = session.get(NodeRecord, node_id)
        if rec:
            rec.x, rec.y = float(x), float(y)
            count += 1
    session.commit()
    return {"saved": count}


# ---------- Excel 导入 ----------

@router.get("/import/excel/template")
def excel_template():
    return Response(
        content=excel_import.build_template(),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": "attachment; filename=dataflow-template.xlsx"},
    )


@router.post("/import/excel")
async def import_excel(file: UploadFile, apply: bool = True,
                       session: Session = Depends(get_session)):
    # UploadFile 已由框架落临时文件；此处限量分块读取，不能把整个大文件加载到内存。
    chunks = bytearray()
    try:
        if file.size is not None and file.size > MAX_EXCEL_UPLOAD_BYTES:
            logger.warning("excel upload rejected reason=size_limit")
            raise HTTPException(413, "Excel 文件过大，最大支持 10 MiB")
        while chunk := await file.read(min(1024 * 1024, MAX_EXCEL_UPLOAD_BYTES + 1 - len(chunks))):
            chunks.extend(chunk)
            if len(chunks) > MAX_EXCEL_UPLOAD_BYTES:
                logger.warning("excel upload rejected reason=size_limit")
                raise HTTPException(413, "Excel 文件过大，最大支持 10 MiB")
    finally:
        await file.close()
    try:
        node_drafts, edge_drafts, errors = excel_import.parse_excel(bytes(chunks))
    except ValueError as e:
        logger.warning("excel import rejected error_type=%s", type(e).__name__)
        raise HTTPException(400, str(e)) from e
    if errors:
        logger.warning("excel validation failed errors=%d apply=%s", len(errors), apply)
    report = merge_drafts(session, f"excel:{file.filename}", node_drafts, edge_drafts,
                          complete=not errors) \
        if apply and not errors else None
    return {
        "parsed": {"nodes": len(node_drafts), "edges": len(edge_drafts)},
        "errors": errors,
        "report": report.to_dict() if report else None,
    }


# ---------- SQL 血缘 ----------

@router.post("/import/sql")
def import_sql(body: schemas.SqlImportIn, session: Session = Depends(get_session)):
    result = sql_lineage.analyze_sql(body.sql_text, body.dialect)
    edges = [
        {
            "source": e.source_id, "target": e.target_id,
            "sourceName": e.source_name, "targetName": e.target_name,
            "fields": e.fields, "statements": e.statements,
        } for e in result.edges
    ]
    report = None
    if body.apply and not result.errors:
        node_drafts = [NodeDraft(tid, "table", name) for tid, name in result.tables.items()]
        edge_drafts = [
            EdgeDraft(
                id=f"lineage:{e.source_id}->{e.target_id}",
                source_id=e.source_id, target_id=e.target_id, type="lineage",
                label=body.source_name,
                attrs={"method": body.source_name, "fields": e.fields,
                       "statements": e.statements},
            )
            for e in result.edges
        ]
        report = merge_drafts(session, f"sql:{body.source_name}", node_drafts, edge_drafts,
                              complete=not result.errors)
    return {"tables": result.tables, "edges": edges, "errors": result.errors,
            "warnings": result.warnings,
            "report": report.to_dict() if report else None}


# ---------- 数据库采集 ----------

@router.post("/discovery/db")
def discovery_db(body: schemas.DbDiscoverIn, session: Session = Depends(get_session)):
    try:
        node_drafts, warnings = db_introspect.discover_database(
            body.db_type, body.host, body.port, body.user, body.password, body.database)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    report = merge_drafts(session, database_source(body.db_type, body.host, body.port, body.database),
                          node_drafts, [], complete=not warnings) if body.apply else None
    return {
        "collected": {"database": 1, "tables": len(node_drafts) - 1},
        "warnings": warnings,
        "report": report.to_dict() if report else None,
    }


# ---------- 演示数据 / 备份 ----------

@router.post("/seed/demo")
def seed_demo(session: Session = Depends(get_session)):
    nodes, edges = demo_drafts()
    report = merge_drafts(session, "demo", nodes, edges)
    return report.to_dict()


@router.get("/export/json")
def export_json(session: Session = Depends(get_session)):
    _begin_snapshot(session)
    payload = _snapshot(session)
    return Response(
        content=json.dumps(payload, ensure_ascii=False, indent=2),
        media_type="application/json",
        headers={"Content-Disposition": "attachment; filename=dataflow-backup.json"},
    )


@router.post("/import/json")
def restore_json(body: schemas.JsonRestoreIn, session: Session = Depends(get_session)):
    try:
        _begin_snapshot(session, write=True)
        current = _snapshot(session)
        if body.expected_revision is not None and body.expected_revision != current["revision"]:
            raise HTTPException(409, "图数据已变化，请刷新后重试；本次操作未覆盖现有数据")

        node_ids = {n.id for n in body.nodes}
        if body.mode == "merge":
            node_ids.update(n["id"] for n in current["nodes"])
        for edge in body.edges:
            if edge.source not in node_ids or edge.target not in node_ids:
                raise HTTPException(400, f"连线 {edge.id} 引用了备份/现有图中不存在的节点")

        # 在任何删除前完成类型验证和 JSON 编码，清空和重建只做一次提交。
        def common(item):
            return dict(id=item.id, type=item.type, origin=item.origin, vanished=item.vanished,
                        attrs=json.dumps(item.attrs, ensure_ascii=False, allow_nan=False),
                        provenance=json.dumps(item.provenance, ensure_ascii=False, allow_nan=False))

        node_data = [common(n) | {"name": n.name, "x": n.x, "y": n.y} for n in body.nodes]
        edge_data = [common(e) | {"source_id": e.source, "target_id": e.target, "label": e.label}
                     for e in body.edges]

        if body.mode == "replace":
            session.query(EdgeRecord).delete(synchronize_session="fetch")
            session.query(NodeRecord).delete(synchronize_session="fetch")

        def restore_items(records, cls):
            added, updated = 0, 0
            for data in records:
                rec = session.get(cls, data["id"])
                if rec is None:
                    session.add(cls(**data))
                    added += 1
                else:
                    for key, value in data.items():
                        setattr(rec, key, value)
                    updated += 1
            return added, updated

        node_added, node_updated = restore_items(node_data, NodeRecord)
        edge_added, edge_updated = restore_items(edge_data, EdgeRecord)
        session.flush()
        revision = _snapshot(session)["revision"]
        session.commit()
        logger.info("backup restore completed mode=%s nodes_added=%d nodes_updated=%d "
                    "edges_added=%d edges_updated=%d", body.mode, node_added, node_updated,
                    edge_added, edge_updated)
        return {"nodesAdded": node_added, "nodesUpdated": node_updated,
                "edgesAdded": edge_added, "edgesUpdated": edge_updated, "revision": revision}
    except HTTPException as exc:
        session.rollback()
        logger.warning("backup restore rejected status=%d", exc.status_code)
        raise
    except (ValueError, TypeError, SQLAlchemyError) as exc:
        session.rollback()
        logger.warning("backup restore rolled back error_type=%s", type(exc).__name__)
        raise HTTPException(400, "备份恢复失败，原图已保留；请检查备份内容") from exc
