"""API 冒烟测试：演示数据、图查询、CRUD、追溯、SQL 导入、布局、备份。"""
import io

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_demo_seeded_and_views():
    graph = client.get("/api/graph", params={"view": "system"}).json()
    names = {n["name"] for n in graph["nodes"]}
    assert "订单中心" in names
    # 表节点只出现在字段视图
    assert all(not n["id"].startswith("tbl:") for n in graph["nodes"])
    field_graph = client.get("/api/graph", params={"view": "field"}).json()
    assert any(n["id"] == "tbl:orders.orders" for n in field_graph["nodes"])
    business = client.get("/api/graph", params={"view": "business"}).json()
    assert any(n["type"] == "process_step" for n in business["nodes"])


def test_node_crud_and_manual_flag():
    node = client.post("/api/nodes", json={"type": "system", "name": "测试系统", "attrs": {"owner": "甲"}}).json()
    assert node["origin"] == "manual"
    patched = client.patch(f"/api/nodes/{node['id']}", json={"name": "测试系统改名", "attrs": {"owner": "乙"}}).json()
    assert patched["name"] == "测试系统改名"
    assert "owner" in patched["manualFields"]
    assert client.delete(f"/api/nodes/{node['id']}").json()["ok"] is True
    assert client.get("/api/graph", params={"view": "system"}).json()["nodes"]


def test_node_attr_clear_with_null():
    node = client.post("/api/nodes", json={"type": "system", "name": "清空测试", "attrs": {"owner": "甲"}}).json()
    # null 表示清除属性；清除属于人工修改，应记入保护避免被采集填回
    patched = client.patch(f"/api/nodes/{node['id']}", json={"attrs": {"owner": None}}).json()
    assert "owner" not in patched["attrs"]
    assert "owner" in patched["manualFields"]
    # 再次赋值 → 变为有值
    patched = client.patch(f"/api/nodes/{node['id']}", json={"attrs": {"owner": "乙"}}).json()
    assert patched["attrs"]["owner"] == "乙"
    client.delete(f"/api/nodes/{node['id']}")


def test_edge_create_requires_endpoints():
    resp = client.post("/api/edges", json={"source_id": "sys:不存在的系统", "target_id": "sys:订单中心"})
    assert resp.status_code == 404
    resp = client.post("/api/edges", json={"source_id": "sys:订单中心", "target_id": "sys:BI平台",
                                           "type": "flow", "label": "直连"})
    assert resp.status_code == 200
    edge = resp.json()
    assert client.delete(f"/api/edges/{edge['id']}").json()["ok"] is True


def test_trace_upstream_downstream():
    result = client.get("/api/trace/tbl:dw.ads_sales_daily", params={"direction": "upstream"}).json()
    ids = {n["id"] for n in result["nodes"]}
    assert "tbl:orders.orders" in ids  # 一路追到源表
    result = client.get("/api/trace/tbl:orders.orders", params={"direction": "downstream"}).json()
    ids = {n["id"] for n in result["nodes"]}
    assert "tbl:dw.ads_sales_daily" in ids


def test_sql_import_apply_and_merge():
    sql = """
    INSERT INTO dw.new_table (uid, amt)
    SELECT user_id, amount FROM orders.orders WHERE status = 'PAID'
    """
    result = client.post("/api/import/sql", json={"sql_text": sql, "dialect": "mysql",
                                                  "source_name": "测试脚本", "apply": True}).json()
    assert result["errors"] == []
    assert result["report"]["added_nodes"]
    field_graph = client.get("/api/graph", params={"view": "field"}).json()
    ids = {n["id"] for n in field_graph["nodes"]}
    assert "tbl:dw.new_table" in ids


def test_sql_preview_not_applied():
    result = client.post("/api/import/sql", json={"sql_text": "INSERT INTO dw.pv SELECT id FROM orders.orders",
                                                  "apply": False}).json()
    assert result["report"] is None
    assert any(e["target"] == "tbl:dw.pv" for e in result["edges"])


def test_layout_and_export():
    graph = client.get("/api/graph", params={"view": "system"}).json()
    node_id = graph["nodes"][0]["id"]
    saved = client.post("/api/layout", json={"positions": {node_id: [42.0, 24.0]}}).json()
    assert saved["saved"] >= 1
    assert client.get("/api/graph", params={"view": "system"}).json()["nodes"][0]

    export = client.get("/api/export/json")
    assert export.status_code == 200
    payload = export.json()
    assert payload["nodes"] and payload["edges"]

    restore = client.post("/api/import/json", json={**payload, "mode": "merge"}).json()
    assert restore["nodesUpdated"] + restore["nodesAdded"] >= len(payload["nodes"])


def test_excel_roundtrip_via_api():
    from app.services.excel_import import build_template
    content = build_template()
    resp = client.post("/api/import/excel",
                       files={"file": ("t.xlsx", io.BytesIO(content),
                                       "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
                       params={"apply": "true"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["parsed"]["nodes"] >= 2
    assert body["errors"] == []
