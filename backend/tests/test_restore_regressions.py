"""备份往返、失败原子性、撤销 CAS 和失败快照的 API 回归测试。"""
from concurrent.futures import ThreadPoolExecutor
import io

from fastapi import FastAPI
from fastapi.testclient import TestClient
from openpyxl import Workbook
import pytest
from sqlalchemy import create_engine
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from app.api import router
from app.db import Base, get_session
from app.services.excel_import import HEADERS, TEMPLATE_SHEET


@pytest.fixture
def client(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'api.db'}",
                           connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    sessions = sessionmaker(engine, autoflush=False, expire_on_commit=False)

    def session_override():
        with sessions() as session:
            yield session

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = session_override
    with TestClient(app) as api:
        yield api
    engine.dispose()


def populated(client):
    for node_id in ("a", "b", "c"):
        response = client.post("/api/nodes", json={
            "id": node_id, "type": "system", "name": node_id, "x": 10, "y": 20,
            "attrs": {"owner": "original"},
        })
        assert response.status_code == 200
    assert client.post("/api/edges", json={
        "id": "edge", "source_id": "a", "target_id": "b", "type": "flow", "label": "original",
    }).status_code == 200
    return client.get("/api/export/json").json()


def test_backup_roundtrip_into_empty_graph_with_edges(client):
    backup = populated(client)
    assert client.post("/api/import/json", json={"nodes": [], "edges": [], "mode": "replace"}).status_code == 200
    restored = client.post("/api/import/json", json={**backup, "mode": "replace"})
    assert restored.status_code == 200, restored.text
    assert client.get("/api/export/json").json() == backup
    assert restored.json()["revision"] == backup["revision"]


def test_merge_restores_missing_edge_and_existing_positions_labels_and_endpoints(client):
    backup = populated(client)
    assert client.patch("/api/nodes/a", json={"x": 999, "y": 888}).status_code == 200
    assert client.patch("/api/edges/edge", json={"label": "changed"}).status_code == 200
    changed = client.get("/api/export/json").json()
    changed["edges"][0]["target"] = "c"
    assert client.post("/api/import/json", json={**changed, "mode": "merge"}).status_code == 200
    assert client.post("/api/import/json", json={**backup, "mode": "merge"}).status_code == 200
    assert client.get("/api/export/json").json() == backup
    assert client.delete("/api/edges/edge").status_code == 200
    assert client.post("/api/import/json", json={**backup, "mode": "merge"}).status_code == 200
    assert client.get("/api/export/json").json() == backup


@pytest.mark.parametrize("invalid", [
    {"nodes": [], "edges": [{"id": "bad", "source": "missing", "target": "also-missing"}]},
    {"nodes": [{"id": "a", "type": "system", "name": "A", "x": "bad"}], "edges": []},
    {"nodes": [{"id": "a", "type": "system", "name": "A", "provenance": {"manual_fields": {}}}], "edges": []},
    {"nodes": [{"id": "a", "type": "system", "name": "A"}] * 2, "edges": []},
    {},
])
def test_invalid_replace_preserves_original_graph(client, invalid):
    backup = populated(client)
    response = client.post("/api/import/json", json={**invalid, "mode": "replace"})
    assert response.status_code in (400, 422), response.text
    assert client.get("/api/export/json").json() == backup


def test_persistence_failure_rolls_back_deletion_and_replacement(client, monkeypatch):
    backup = populated(client)

    def fail_flush(*args, **kwargs):
        raise SQLAlchemyError("synthetic persistence failure")

    with monkeypatch.context() as patch:
        patch.setattr(Session, "flush", fail_flush)
        response = client.post("/api/import/json", json={"nodes": [], "edges": [], "mode": "replace"})
    assert response.status_code == 400
    assert client.get("/api/export/json").json() == backup


def test_restore_preserves_manual_protection_and_source_states(client):
    backup = populated(client)
    node = backup["nodes"][0]
    node["origin"] = "auto"
    node["provenance"] = {
        "manual_fields": ["owner"],
        "sources": {"job": "2026-10-12T00:00:00"},
        "source_states": {"job": {"active": True, "last_seen": "2026-10-12T00:00:00"}},
    }
    assert client.post("/api/import/json", json={**backup, "mode": "replace"}).status_code == 200
    restored = client.get("/api/export/json").json()["nodes"][0]
    assert restored["provenance"] == node["provenance"]
    assert restored["manualFields"] == ["owner"]


def test_stale_revision_cannot_overwrite_new_edit(client):
    backup = populated(client)
    assert client.patch("/api/nodes/a", json={"name": "new edit"}).status_code == 200
    current = client.get("/api/export/json").json()
    response = client.post("/api/import/json", json={
        **backup, "mode": "replace", "expected_revision": backup["revision"],
    })
    assert response.status_code == 409
    assert client.get("/api/export/json").json() == current


def test_two_writers_with_same_revision_only_one_commits(client):
    backup = populated(client)

    def update(name):
        nodes = [dict(n, name=name) if n["id"] == "a" else n for n in backup["nodes"]]
        return client.post("/api/import/json", json={
            **backup, "nodes": nodes, "mode": "replace", "expected_revision": backup["revision"],
        }).status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        statuses = list(pool.map(update, ["first", "second"]))
    assert sorted(statuses) == [200, 409]
    current = client.get("/api/export/json").json()
    assert len(current["nodes"]) == 3 and len(current["edges"]) == 1
    assert next(n for n in current["nodes"] if n["id"] == "a")["name"] in ("first", "second")


def test_failed_sql_snapshot_does_not_hide_old_graph(client):
    request = {"sql_text": "INSERT INTO dw.t SELECT id FROM src.users", "source_name": "job", "apply": True}
    assert client.post("/api/import/sql", json=request).status_code == 200
    before = client.get("/api/graph?view=field").json()
    failed = client.post("/api/import/sql", json={**request, "sql_text": "INSERT INTO dw.t SELECT FROM"})
    assert failed.status_code == 200 and failed.json()["errors"]
    assert failed.json()["report"] is None
    assert client.get("/api/graph?view=field").json() == before


def test_failed_excel_row_does_not_hide_old_graph(client):
    def upload(target):
        wb = Workbook()
        ws = wb.active
        ws.title = TEMPLATE_SHEET
        ws.append(HEADERS)
        ws.append(["A", "系统", target, "系统", "接口", "payload", "id->id"])
        stream = io.BytesIO()
        wb.save(stream)
        return client.post("/api/import/excel", files={"file": ("same.xlsx", stream.getvalue())})

    assert upload("B").status_code == 200
    before = client.get("/api/graph?view=system").json()
    failed = upload("")
    assert failed.status_code == 200 and failed.json()["errors"]
    assert failed.json()["report"] is None
    assert client.get("/api/graph?view=system").json() == before


def test_reimport_of_one_sql_source_keeps_shared_source_node_visible(client):
    def apply(sql, source):
        response = client.post("/api/import/sql", json={"sql_text": sql, "source_name": source})
        assert response.status_code == 200 and not response.json()["errors"]

    apply("INSERT INTO dw.a SELECT id FROM src.shared", "A")
    apply("INSERT INTO dw.b SELECT id FROM src.shared", "B")
    apply("INSERT INTO dw.a SELECT id FROM src.replacement", "A")
    graph = client.get("/api/graph?view=field").json()
    assert any(n["id"] == "tbl:src.shared" for n in graph["nodes"])
    assert any(e["source"] == "tbl:src.shared" and e["target"] == "tbl:dw.b" for e in graph["edges"])


def test_postgresql_selection_is_supported_by_api(client):
    response = client.post("/api/import/sql", json={
        "sql_text": "CREATE TABLE dw.t AS SELECT id FROM src.users", "dialect": "postgresql", "apply": False,
    })
    assert response.status_code == 200, response.text
    assert response.json()["errors"] == []
    assert response.json()["edges"][0]["fields"] == ["src.users.id -> id"]
