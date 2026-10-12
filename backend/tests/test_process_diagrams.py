"""流程文档隔离、图语义、资产引用和并发更新的独立库回归测试。"""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import json
from threading import Barrier

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.api import router as data_router
from app.db import Base, get_session
from app.models import EdgeRecord, NodeRecord, ProcessDiagramRecord
from app.process_api import router as process_router


@pytest.fixture
def process_env(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'process.db'}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    sessions = sessionmaker(engine, autoflush=False, expire_on_commit=False)

    def session_override():
        with sessions() as session:
            yield session

    app = FastAPI()
    app.include_router(data_router)
    app.include_router(process_router)
    app.dependency_overrides[get_session] = session_override
    with TestClient(app) as client:
        yield client, sessions
    engine.dispose()


def node(node_id="step", node_type="task", **kwargs):
    return {"id": node_id, "type": node_type, "name": node_id,
            "x": 10, "y": 20, "owner": "负责人", "description": "说明",
            "input": "申请", "output": "结果", "assetIds": [], **kwargs}


def edge(source, target, edge_id="edge", **kwargs):
    return {"id": edge_id, "source": source, "target": target, "label": "", **kwargs}


def draft(**kwargs):
    return {"name": "申请流程", "description": "流程说明", "nodes": [node()], "edges": [], **kwargs}


def create(client, payload=None):
    response = client.post("/api/diagrams", json=payload or draft())
    assert response.status_code == 200, response.text
    return response.json()


def save(client, document, **changes):
    body = {key: deepcopy(document[key]) for key in ("name", "description", "nodes", "edges")}
    body.update(changes)
    return client.put(f"/api/diagrams/{document['id']}", json={**body, "expected_revision": document["revision"]})


def add_asset(client, asset_id="asset", asset_type="system"):
    response = client.post("/api/nodes", json={"id": asset_id, "name": asset_id, "type": asset_type})
    assert response.status_code == 200, response.text
    return response.json()


def test_create_list_and_read_preserve_original_graph(process_env):
    client, _ = process_env
    add_asset(client)
    before = client.get("/api/export/json").json()
    document = create(client, draft(nodes=[node("start", "start"), node("step"), node("end", "end")],
                                    edges=[edge("start", "step", "one"), edge("step", "end", "two")]))
    assert document["nodeCount"] == 3 and document["edgeCount"] == 2
    assert document["updatedAt"].endswith("+00:00")
    assert document["revision"] and document["id"]
    assert client.get(f"/api/diagrams/{document['id']}").json() == document
    listed = client.get("/api/diagrams").json()
    assert listed == [{key: value for key, value in document.items() if key not in ("nodes", "edges")}]
    assert client.get("/api/export/json").json() == before
    assert client.get("/api/trace/asset").json()["edges"] == []


def test_diagrams_have_local_ids_layout_and_revision(process_env):
    client, _ = process_env
    first, second = create(client), create(client)
    assert first["nodes"][0]["id"] == second["nodes"][0]["id"]
    assert first["id"] != second["id"]
    edited = save(client, first, name="已改名", nodes=[node(x=999, owner="新的负责人")])
    assert edited.status_code == 200, edited.text
    assert edited.json()["revision"] != first["revision"]
    assert edited.json()["nodes"][0]["x"] == 999
    assert client.get(f"/api/diagrams/{second['id']}").json() == second
    assert save(client, first, name="过期编辑").status_code == 409
    assert client.get(f"/api/diagrams/{first['id']}").json() == edited.json()


def test_incomplete_drafts_and_cross_step_cycles_are_allowed(process_env):
    client, _ = process_env
    empty = create(client, {"name": "空草稿"})
    assert empty["nodes"] == empty["edges"] == []
    create(client, draft(nodes=[node("a"), node("b"), node("c", "decision")],
                         edges=[edge("a", "b", "1"), edge("b", "c", "2"),
                                edge("c", "a", "3", sourceHandle="no"),
                                edge("c", "b", "4", sourceHandle="yes", targetHandle="in")]))


@pytest.mark.parametrize("payload", [
    draft(name=" \t"),
    draft(name="x" * 201),
    draft(unknown="silently dropped"),
    draft(nodes=[node(node_type="system")]),
    draft(nodes=[node(name=" ")]),
    draft(nodes=[node(node_id=" ")]),
    draft(nodes=[node(x="NaN")]),
    draft(nodes=[node(x="123")]),
    draft(nodes=[node(x=True)]),
    draft(nodes=[node(y="Infinity")]),
    draft(nodes=[node(x=1_000_001)]),
    draft(nodes=[node(unknown="silently dropped")]),
    draft(nodes=[node(), node()]),
    draft(nodes=[node(assetIds=["asset", "asset"])]),
    draft(nodes=[node(assetIds=[""])]),
    draft(edges=[edge("step", "missing")]),
    draft(edges=[edge("step", "step")]),
    draft(nodes=[node("a"), node("b")], edges=[edge("a", "b"), edge("b", "a")]),
    draft(nodes=[node("a"), node("b", "start")], edges=[edge("a", "b")]),
    draft(nodes=[node("a", "end"), node("b")], edges=[edge("a", "b")]),
    draft(nodes=[node("a"), node("b")], edges=[edge("a", "b", sourceHandle="yes")]),
    draft(nodes=[node("a", "decision"), node("b")], edges=[edge("a", "b", sourceHandle="out")]),
    draft(nodes=[node("a"), node("b")], edges=[edge("a", "b", targetHandle="out")]),
    draft(nodes=[node("a"), node("b")], edges=[edge("a", "b", unknown="bad")]),
    draft(nodes=[node(str(index)) for index in range(501)]),
])
def test_invalid_create_and_update_leave_document_unchanged(process_env, payload):
    client, _ = process_env
    original = create(client)
    assert client.post("/api/diagrams", json=payload).status_code == 422
    assert client.put(f"/api/diagrams/{original['id']}", json={
        **payload, "expected_revision": original["revision"]}).status_code == 422
    assert client.get(f"/api/diagrams/{original['id']}").json() == original
    assert len(client.get("/api/diagrams").json()) == 1


def test_atomic_cas_accepts_exactly_one_concurrent_writer(process_env):
    client, _ = process_env
    original = create(client)
    barrier = Barrier(2)

    def writer(name):
        barrier.wait(timeout=5)
        return save(client, original, name=name)

    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(writer, ["页面甲", "页面乙"]))
    assert sorted(response.status_code for response in responses) == [200, 409]
    winner = next(response.json() for response in responses if response.status_code == 200)
    assert client.get(f"/api/diagrams/{original['id']}").json() == winner


@pytest.mark.parametrize("coordinate", [float("nan"), float("inf"), float("-inf")])
def test_nonfinite_json_numbers_return_validation_error(process_env, coordinate):
    client, _ = process_env
    original = create(client)
    response = client.post("/api/diagrams", content=json.dumps(draft(nodes=[node(x=coordinate)])),
                           headers={"content-type": "application/json"})
    assert response.status_code == 422, response.text
    assert client.get(f"/api/diagrams/{original['id']}").json() == original


def test_create_all_adds_document_table_without_changing_legacy_assets(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'legacy.db'}")
    NodeRecord.__table__.create(engine)
    EdgeRecord.__table__.create(engine)
    sessions = sessionmaker(engine, expire_on_commit=False)
    with sessions() as session:
        session.add(NodeRecord(id="existing", type="system", name="已有系统", x=123, attrs='{"owner":"甲"}'))
        session.commit()
    Base.metadata.create_all(engine)
    with sessions() as session:
        existing = session.get(NodeRecord, "existing")
        assert (existing.name, existing.x, existing.attrs_dict) == ("已有系统", 123, {"owner": "甲"})
        assert session.query(ProcessDiagramRecord).count() == 0
    engine.dispose()


def test_assets_filter_unavailable_and_business_steps(process_env):
    client, sessions = process_env
    for asset_type in ("system", "external", "database", "table", "api", "file", "process_step"):
        add_asset(client, asset_type, asset_type)
    add_asset(client, "vanished")
    with sessions() as session:
        session.get(NodeRecord, "vanished").vanished = True
        session.commit()
    assert {asset["id"] for asset in client.get("/api/assets").json()} == {
        "system", "external", "database", "table", "api", "file"}
    for asset_id in ("missing", "vanished", "process_step"):
        assert client.post("/api/diagrams", json=draft(nodes=[node(assetIds=[asset_id])])).status_code == 422


@pytest.mark.parametrize("invalidation", ["delete", "vanish"])
def test_lost_asset_reference_remains_editable_but_cannot_be_newly_linked(process_env, invalidation):
    client, sessions = process_env
    add_asset(client)
    original = create(client, draft(nodes=[node(assetIds=["asset"])]))
    if invalidation == "delete":
        assert client.delete("/api/nodes/asset").status_code == 200
    else:
        with sessions() as session:
            session.get(NodeRecord, "asset").vanished = True
            session.commit()
    assert client.get("/api/assets").json() == []
    edited = save(client, original, nodes=[node(assetIds=["asset"], x=777)])
    assert edited.status_code == 200, edited.text
    assert client.get("/api/assets/asset/diagrams").json() == [{
        "id": original["id"], "name": original["name"], "nodeIds": ["step"]}]
    edited = edited.json()
    assert save(client, edited, nodes=[node(assetIds=["asset"]), node("new", assetIds=["asset"])]).status_code == 422
    assert save(client, edited, nodes=[node("replacement", assetIds=["asset"])]).status_code == 422
    assert client.post("/api/diagrams", json=draft(nodes=[node(assetIds=["asset"])] )).status_code == 422
    removed = save(client, edited, nodes=[node()]).json()
    assert client.get("/api/assets/asset/diagrams").json() == []
    assert save(client, removed, nodes=[node(assetIds=["asset"])]).status_code == 422
    assert client.get(f"/api/diagrams/{original['id']}").json() == removed


def test_reverse_usage_multiple_diagrams_and_delete_preserves_assets(process_env):
    client, _ = process_env
    add_asset(client, "asset")
    first = create(client, draft(nodes=[node("a", assetIds=["asset"]), node("b", assetIds=["asset"])]))
    second = create(client, draft(name="第二条流程", nodes=[node(assetIds=["asset"])]))
    usages = client.get("/api/assets/asset/diagrams").json()
    assert {usage["id"] for usage in usages} == {first["id"], second["id"]}
    assert next(usage for usage in usages if usage["id"] == first["id"])["nodeIds"] == ["a", "b"]
    before = client.get("/api/export/json").json()
    assert client.delete(f"/api/diagrams/{first['id']}").status_code == 422
    assert client.delete(f"/api/diagrams/{first['id']}", params={"expected_revision": "stale"}).status_code == 409
    assert client.get(f"/api/diagrams/{first['id']}").json() == first
    assert client.delete(f"/api/diagrams/{first['id']}", params={"expected_revision": first["revision"]}).json() == {"ok": True}
    assert client.get(f"/api/diagrams/{first['id']}").status_code == 404
    assert client.get("/api/export/json").json() == before
    assert client.get("/api/assets/asset/diagrams").json() == [{
        "id": second["id"], "name": second["name"], "nodeIds": ["step"]}]


def test_asset_reverse_usage_accepts_slash_in_legacy_asset_id(process_env):
    client, _ = process_env
    add_asset(client, "file:archive/orders.csv", "file")
    document = create(client, draft(nodes=[node(assetIds=["file:archive/orders.csv"])]))
    assert client.get("/api/assets/file%3Aarchive%2Forders.csv/diagrams").json() == [{
        "id": document["id"], "name": document["name"], "nodeIds": ["step"]}]


def test_missing_documents_return_404_and_corrupt_content_is_reported(process_env, caplog):
    client, sessions = process_env
    assert client.get("/api/diagrams/missing").status_code == 404
    assert client.put("/api/diagrams/missing", json={**draft(), "expected_revision": "stale"}).status_code == 404
    assert client.delete("/api/diagrams/missing", params={"expected_revision": "stale"}).status_code == 404
    document = create(client)
    with sessions() as session:
        session.get(ProcessDiagramRecord, document["id"]).content = "broken"
        session.commit()
    assert client.get(f"/api/diagrams/{document['id']}").status_code == 500
    assert "Invalid persisted process document" in caplog.text
