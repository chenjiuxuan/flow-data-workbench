"""REST API 路由。"""
import json

from fastapi import APIRouter, Depends, HTTPException, UploadFile
from fastapi.responses import Response
from sqlalchemy.orm import Session

from . import schemas
from .db import get_session
from .models import EdgeRecord, NodeRecord
from .services import db_introspect, excel_import, graph as graph_service
from .services import sql_lineage
from .services.demo_seed import demo_drafts
from .services.ids import sys_id
from .services.merge import EdgeDraft, NodeDraft, merge_drafts

router = APIRouter(prefix="/api")


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
    edge_id = body.id or f"flow:{body.source_id}->{body.target_id}:{body.label or body.type}"
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
    content = await file.read()
    try:
        node_drafts, edge_drafts, errors = excel_import.parse_excel(content)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    report = merge_drafts(session, f"excel:{file.filename}", node_drafts, edge_drafts) \
        if apply else None
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
    if body.apply:
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
        report = merge_drafts(session, f"sql:{body.source_name}", node_drafts, edge_drafts)
    return {"tables": result.tables, "edges": edges, "errors": result.errors,
            "report": report.to_dict() if report else None}


# ---------- 数据库采集 ----------

@router.post("/discovery/db")
def discovery_db(body: schemas.DbDiscoverIn, session: Session = Depends(get_session)):
    try:
        node_drafts, warnings = db_introspect.discover_database(
            body.db_type, body.host, body.port, body.user, body.password, body.database)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    report = merge_drafts(session, f"db:{body.db_type}:{body.host}:{body.database}",
                          node_drafts, []) if body.apply else None
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
    nodes = session.query(NodeRecord).all()
    edges = session.query(EdgeRecord).all()
    payload = {
        "nodes": [_node_out(n) | {"provenance": n.provenance_dict} for n in nodes],
        "edges": [_edge_out(e) | {"provenance": e.provenance_dict} for e in edges],
    }
    return Response(
        content=json.dumps(payload, ensure_ascii=False, indent=2),
        media_type="application/json",
        headers={"Content-Disposition": "attachment; filename=dataflow-backup.json"},
    )


@router.post("/import/json")
def restore_json(body: schemas.JsonRestoreIn, session: Session = Depends(get_session)):
    if body.mode == "replace":
        session.query(EdgeRecord).delete()
        session.query(NodeRecord).delete()
        session.commit()

    def restore_items(records, cls):
        added, updated = 0, 0
        for item in records:
            node_id = item.get("id") or ""
            if not node_id:
                continue
            prov = item.get("provenance") or {}
            data = dict(
                id=node_id,
                type=item.get("type", "system"),
                name=item.get("name", node_id),
                attrs=json.dumps(item.get("attrs") or {}, ensure_ascii=False),
                origin=item.get("origin", "manual"),
                vanished=bool(item.get("vanished", False)),
                provenance=json.dumps(prov, ensure_ascii=False),
            )
            rec = session.get(cls, node_id)
            if rec is None:
                if cls is NodeRecord:
                    session.add(NodeRecord(x=item.get("x", 0), y=item.get("y", 0), **data))
                else:
                    session.add(EdgeRecord(
                        source_id=item.get("source", ""), target_id=item.get("target", ""),
                        type=item.get("type", "flow"), label=item.get("label", ""), **data))
                added += 1
            else:
                for key, value in data.items():
                    setattr(rec, key, value)
                updated += 1
        return added, updated

    node_added, node_updated = restore_items(body.nodes, NodeRecord)
    edge_added, edge_updated = restore_items(body.edges, EdgeRecord)
    session.commit()
    return {"nodesAdded": node_added, "nodesUpdated": node_updated,
            "edgesAdded": edge_added, "edgesUpdated": edge_updated}
