"""第二轮审查：输入契约、安全边 ID、上传边界与健康检查。"""
import asyncio
import io
import re
from zipfile import ZipFile

from fastapi import FastAPI, HTTPException, UploadFile
from fastapi.testclient import TestClient
import pytest
from sqlalchemy import create_engine
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import sessionmaker

from app import api as api_module
from app.db import Base, get_session
from app.services import excel_import


@pytest.fixture
def client(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'review.db'}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    sessions = sessionmaker(engine, autoflush=False, expire_on_commit=False)

    def session_override():
        with sessions() as session:
            yield session

    app = FastAPI()
    app.include_router(api_module.router)
    app.dependency_overrides[get_session] = session_override
    with TestClient(app) as test_client:
        yield test_client
    engine.dispose()


def add_node(client, node_id="a", node_type="system"):
    response = client.post("/api/nodes", json={"id": node_id, "type": node_type, "name": node_id})
    assert response.status_code == 200, response.text
    return response.json()


@pytest.mark.parametrize("node_type", ["system", "database", "api", "file", "external", "process_step", "table"])
def test_supported_node_types_are_visible(client, node_type):
    add_node(client, node_type=node_type)
    views = [client.get(f"/api/graph?view={view}").json() for view in ("system", "business", "field")]
    assert any(any(n["id"] == "a" for n in graph["nodes"]) for graph in views)


def test_invalid_types_and_manual_type_patch_are_rejected_before_writes(client):
    add_node(client)
    add_node(client, "b")
    before = client.get("/api/export/json").json()
    assert client.post("/api/nodes", json={"type": "xyz", "name": "invisible"}).status_code == 422
    assert client.patch("/api/nodes/a", json={"type": "table"}).status_code == 422
    assert client.post("/api/edges", json={"source_id": "a", "target_id": "b", "type": "xyz"}).status_code == 422
    for kind in ("node", "edge"):
        payload = {**before, "mode": "replace"}
        if kind == "node":
            payload["nodes"] = [{**before["nodes"][0], "type": "xyz"}]
        else:
            payload["edges"] = [{"id": "e", "source": "a", "target": "b", "type": "system"}]
        assert client.post("/api/import/json", json=payload).status_code == 422
        assert client.get("/api/export/json").json() == before


@pytest.mark.parametrize("edge_type", ["flow", "lineage", "uses"])
def test_supported_edge_types_survive_backup_roundtrip(client, edge_type):
    add_node(client)
    add_node(client, "b")
    assert client.post("/api/edges", json={"source_id": "a", "target_id": "b", "type": edge_type}).status_code == 200
    backup = client.get("/api/export/json").json()
    assert client.post("/api/import/json", json={**backup, "mode": "replace"}).status_code == 200
    assert client.get("/api/export/json").json() == backup


@pytest.mark.parametrize("db_type,expected", [("mysql", 3306), ("postgresql", 5432)])
def test_database_default_port_and_explicit_port(client, monkeypatch, db_type, expected):
    seen = []

    def discover(*args):
        seen.append(args[2])
        return [], []

    monkeypatch.setattr(api_module.db_introspect, "discover_database", discover)
    body = {"db_type": db_type, "host": "local", "user": "reader", "database": "db", "apply": False}
    assert client.post("/api/discovery/db", json=body).status_code == 200
    assert client.post("/api/discovery/db", json={**body, "port": 15432}).status_code == 200
    assert seen == [expected, 15432]
    for invalid in (0, 65536):
        assert client.post("/api/discovery/db", json={**body, "port": invalid}).status_code == 422


def test_generated_edge_id_is_bounded_url_safe_stable_and_unambiguous(client):
    for node_id in ("a->b", "c", "a", "b->c"):
        add_node(client, node_id)
    label = "同步:/?# >\n金额"
    first = client.post("/api/edges", json={"source_id": "a->b", "target_id": "c", "label": label})
    second = client.post("/api/edges", json={"source_id": "a", "target_id": "b->c", "label": label})
    assert first.status_code == second.status_code == 200
    edge = first.json()
    assert edge["id"] != second.json()["id"]
    assert len(edge["id"]) <= 300
    assert not any(char.isspace() or char in "/?#><" for char in edge["id"])
    assert client.post("/api/edges", json={"source_id": "a->b", "target_id": "c", "label": label}).status_code == 409
    assert client.patch(f"/api/edges/{edge['id']}", json={"label": "changed"}).status_code == 200
    assert client.delete(f"/api/edges/{edge['id']}").status_code == 200


def test_oversized_excel_is_rejected_without_parsing_or_mutation(client, monkeypatch):
    add_node(client)
    before = client.get("/api/export/json").json()
    monkeypatch.setattr(api_module, "MAX_EXCEL_UPLOAD_BYTES", 128, raising=False)

    def unexpected_parse(_):
        raise AssertionError("oversized input reached Excel parser")

    monkeypatch.setattr(api_module.excel_import, "parse_excel", unexpected_parse)
    response = client.post("/api/import/excel", files={"file": ("large.xlsx", b"x" * 129)})
    assert response.status_code == 413
    assert client.get("/api/export/json").json() == before


def test_invalid_excel_is_a_controlled_error_and_preserves_graph(client):
    add_node(client)
    before = client.get("/api/export/json").json()
    response = client.post("/api/import/excel", files={"file": ("invalid.xlsx", b"not an Excel ZIP")})
    assert response.status_code == 400
    assert client.get("/api/export/json").json() == before


def test_health_checks_database_without_changing_graph(client):
    add_node(client)
    before = client.get("/api/export/json").json()
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "database": "ok"}
    assert client.get("/api/export/json").json() == before


def test_health_reports_unavailable_database_without_driver_details(client, monkeypatch):
    def fail_query(*args, **kwargs):
        raise SQLAlchemyError("SECRET_CONNECTION_DETAILS")

    monkeypatch.setattr(api_module.Session, "execute", fail_query)
    response = client.get("/api/health")
    assert response.status_code == 503
    assert "SECRET" not in response.text


def test_upload_limit_also_applies_without_size_metadata_and_closes_file(monkeypatch):
    monkeypatch.setattr(api_module, "MAX_EXCEL_UPLOAD_BYTES", 128)
    upload = UploadFile(io.BytesIO(b"x" * 129), filename="large.xlsx")
    assert upload.size is None
    with pytest.raises(HTTPException) as caught:
        asyncio.run(api_module.import_excel(upload, apply=False, session=None))
    assert caught.value.status_code == 413
    assert upload.file.closed


def test_zip_expansion_limit_is_checked_before_workbook_parsing(monkeypatch):
    monkeypatch.setattr(excel_import, "MAX_EXCEL_EXPANDED_BYTES", 128)
    stream = io.BytesIO()
    with ZipFile(stream, "w") as archive:
        archive.writestr("large.xml", b"x" * 129)
    with pytest.raises(ValueError, match="解压后过大"):
        excel_import.parse_excel(stream.getvalue())


def test_zip_entry_limit_is_checked_before_workbook_parsing(monkeypatch):
    monkeypatch.setattr(excel_import, "MAX_EXCEL_ZIP_ENTRIES", 1)
    stream = io.BytesIO()
    with ZipFile(stream, "w") as archive:
        archive.writestr("a.xml", "a")
        archive.writestr("b.xml", "b")
    with pytest.raises(ValueError, match="过多文件"):
        excel_import.parse_excel(stream.getvalue())


def test_workbook_row_limit_preserves_graph(client, monkeypatch):
    monkeypatch.setattr(excel_import, "MAX_EXCEL_ROWS", 1)
    before = client.get("/api/export/json").json()
    # 模板有两行真实数据，缩小阈值使本测试无需创建十万行。
    response = client.post("/api/import/excel", files={"file": ("rows.xlsx", excel_import.build_template())})
    assert response.status_code == 400
    assert client.get("/api/export/json").json() == before


def rewrite_sheet(content, transform):
    output = io.BytesIO()
    with ZipFile(io.BytesIO(content)) as original, ZipFile(output, "w") as changed:
        for entry in original.infolist():
            body = original.read(entry.filename)
            if entry.filename == "xl/worksheets/sheet1.xml":
                body = transform(body)
            changed.writestr(entry, body)
    return output.getvalue()


@pytest.mark.parametrize("dimension", ["A1:A1", "A1:XFD1048576"])
def test_inaccurate_sheet_dimension_does_not_hide_real_rows(client, dimension):
    original = excel_import.build_template()
    changed = rewrite_sheet(original, lambda body: re.sub(
        rb'<dimension [^>]*/>', f'<dimension ref="{dimension}"/>'.encode(), body))
    baseline = client.post("/api/import/excel", files={"file": ("ledger.xlsx", original)}).json()
    result = client.post("/api/import/excel", files={"file": ("ledger.xlsx", changed)})
    assert result.status_code == 200, result.text
    assert result.json()["errors"] == []
    assert result.json()["parsed"] == baseline["parsed"]
    assert result.json()["report"]["vanished_nodes"] == []
    assert result.json()["report"]["vanished_edges"] == []


def test_corrupt_sheet_xml_is_controlled_and_keeps_graph(client):
    add_node(client)
    before = client.get("/api/export/json").json()
    malformed = rewrite_sheet(excel_import.build_template(), lambda body: body.replace(b"</row>", b"</invalid>", 1))
    response = client.post("/api/import/excel", files={"file": ("corrupt.xlsx", malformed)})
    assert response.status_code == 400
    assert client.get("/api/export/json").json() == before
