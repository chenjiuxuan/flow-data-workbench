"""来源活跃性、台账清空语义及物理数据库身份的回归测试。"""
import io
import json
from unittest.mock import Mock

import pytest
import sqlalchemy
from openpyxl import Workbook
from sqlalchemy.orm import Session

from app.db import Base
from app.models import EdgeRecord, NodeRecord
from app.services import db_introspect
from app.services.excel_import import HEADERS, TEMPLATE_SHEET, parse_excel
from app.services.ids import database_source, db_id, physical_table_id
from app.services.merge import EdgeDraft, NodeDraft, merge_drafts


@pytest.fixture
def session():
    engine = sqlalchemy.create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as test_session:
        yield test_session
    engine.dispose()


def _flow():
    return [NodeDraft("sys:A", "system", "A"), NodeDraft("sys:B", "system", "B")], [
        EdgeDraft("flow:A-B", "sys:A", "sys:B")
    ]


def test_shared_items_vanish_only_after_every_source_is_inactive(session):
    nodes, edges = _flow()
    merge_drafts(session, "sql:A", nodes, edges)
    merge_drafts(session, "sql:B", nodes, edges)
    first_missing = merge_drafts(session, "sql:A", [], [])
    assert first_missing.vanished_nodes == first_missing.vanished_edges == []
    assert not session.get(NodeRecord, "sys:A").vanished
    assert not session.get(EdgeRecord, "flow:A-B").vanished
    states = session.get(NodeRecord, "sys:A").provenance_dict["source_states"]
    assert states["sql:A"]["active"] is False
    assert states["sql:B"]["active"] is True

    last_missing = merge_drafts(session, "sql:B", [], [])
    assert set(last_missing.vanished_nodes) == {"sys:A", "sys:B"}
    assert last_missing.vanished_edges == ["flow:A-B"]
    assert session.get(NodeRecord, "sys:A").vanished
    merge_drafts(session, "sql:A", nodes, edges)
    assert not session.get(NodeRecord, "sys:A").vanished
    assert not session.get(EdgeRecord, "flow:A-B").vanished


def test_partial_snapshot_does_not_deactivate_absent_items(session):
    nodes, edges = _flow()
    merge_drafts(session, "excel:ledger", nodes, edges)
    report = merge_drafts(session, "excel:ledger",
                          [NodeDraft("sys:C", "system", "C")], [], complete=False)
    assert report.added_nodes == ["sys:C"]
    assert report.vanished_nodes == report.vanished_edges == []
    assert not session.get(NodeRecord, "sys:A").vanished
    assert session.get(EdgeRecord, "flow:A-B").provenance_dict[
        "source_states"]["excel:ledger"]["active"] is True
    merge_drafts(session, "excel:ledger", [], [])
    assert session.get(NodeRecord, "sys:A").vanished


def test_legacy_shared_vanished_state_is_migrated_conservatively(session):
    session.add(NodeRecord(id="tbl:shared", type="table", name="shared", origin="auto",
                           vanished=True, provenance=json.dumps({
                               "sources": {"sql:A": "old-A", "sql:B": "old-B"}
                           })))
    session.commit()
    merge_drafts(session, "sql:A", [], [])
    record = session.get(NodeRecord, "tbl:shared")
    assert not record.vanished
    assert record.provenance_dict["sources"] == {"sql:A": "old-A", "sql:B": "old-B"}
    assert record.provenance_dict["source_states"]["sql:B"]["active"] is True
    merge_drafts(session, "sql:B", [], [])
    assert record.vanished


def test_legacy_single_vanished_source_stays_inactive(session):
    session.add(NodeRecord(id="tbl:old", type="table", name="old", origin="auto",
                           vanished=True, provenance=json.dumps({"sources": {"sql:A": "old"}})))
    session.commit()
    merge_drafts(session, "sql:B", [NodeDraft("tbl:old", "table", "old")], [])
    record = session.get(NodeRecord, "tbl:old")
    assert not record.provenance_dict["source_states"]["sql:A"]["active"]
    merge_drafts(session, "sql:B", [], [])
    assert record.vanished


def _excel(fields="id->id", source_kind="系统"):
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = TEMPLATE_SHEET
    sheet.append(HEADERS)
    sheet.append(["A", source_kind, "B", "系统", "接口", "订单", fields, "日", "", ""])
    output = io.BytesIO()
    workbook.save(output)
    workbook.close()
    return output.getvalue()


def test_excel_reimport_can_clear_mapping_and_update_node_type(session):
    nodes, edges, errors = parse_excel(_excel())
    assert errors == []
    merge_drafts(session, "excel:ledger", nodes, edges)
    nodes, edges, errors = parse_excel(_excel(fields="", source_kind="外部方"))
    merge_drafts(session, "excel:ledger", nodes, edges)
    assert session.get(NodeRecord, "sys:A").type == "external"
    assert session.get(EdgeRecord, edges[0].id).attrs_dict["fields"] == []


def test_type_and_mapping_manual_protection_still_apply(session):
    nodes, edges, _ = parse_excel(_excel())
    merge_drafts(session, "excel:ledger", nodes, edges)
    node = session.get(NodeRecord, "sys:A")
    edge = session.get(EdgeRecord, edges[0].id)
    node.provenance = json.dumps({**node.provenance_dict, "manual_fields": ["type"]})
    edge.provenance = json.dumps({**edge.provenance_dict, "manual_fields": ["fields"]})
    session.commit()
    nodes, edges, _ = parse_excel(_excel(fields="", source_kind="外部方"))
    report = merge_drafts(session, "excel:ledger", nodes, edges)
    assert node.type == "system"
    assert edge.attrs_dict["fields"] == ["id->id"]
    assert "sys:A:type" in report.protected_fields
    assert f"{edge.id}:fields" in report.protected_fields


def test_manual_records_remain_completely_untouched(session):
    node = NodeRecord(id="sys:A", type="system", name="Manual", origin="manual",
                      vanished=True, attrs='{"owner": "manual"}', provenance='{}')
    session.add(node)
    session.commit()
    merge_drafts(session, "excel:ledger", [NodeDraft("sys:A", "external", "Auto")], [])
    assert node.name == "Manual" and node.type == "system" and node.vanished
    assert node.provenance_dict == {}


def _collector(monkeypatch, *, tables=("orders",), columns=None, column_error=False,
               table_error=False):
    inspector = Mock()
    inspector.get_table_names.return_value = list(tables)
    if table_error:
        inspector.get_table_names.side_effect = RuntimeError("cannot list tables")
    if column_error:
        inspector.get_columns.side_effect = RuntimeError("temporary metadata error")
    else:
        inspector.get_columns.return_value = columns if columns is not None else [
            {"name": "id", "type": "INTEGER"}
        ]
    engine = Mock()
    create_engine = Mock(return_value=engine)
    monkeypatch.setattr(sqlalchemy, "create_engine", create_engine)
    monkeypatch.setattr(sqlalchemy, "inspect", Mock(return_value=inspector))
    return engine, create_engine


def test_discovery_url_preserves_credentials_and_releases_engine(monkeypatch):
    engine, create_engine = _collector(monkeypatch)
    db_introspect.discover_database("mysql", "db.internal", 3307,
                                    "user@domain", "p@ss:/#%", "sales")
    url = create_engine.call_args.args[0]
    assert url.username == "user@domain" and url.password == "p@ss:/#%"
    assert url.host == "db.internal" and url.port == 3307 and url.database == "sales"
    engine.dispose.assert_called_once()


def test_physical_instances_do_not_overwrite_or_vanish_each_other(monkeypatch, session):
    _collector(monkeypatch)
    first_nodes, first_warnings = db_introspect.discover_database(
        "mysql", "db.internal", 3306, "reader", "test", "sales")
    second_nodes, second_warnings = db_introspect.discover_database(
        "mysql", "db.internal", 3307, "reader", "test", "sales")
    assert first_warnings == second_warnings == []
    assert first_nodes[0].id != second_nodes[0].id
    assert first_nodes[1].id != second_nodes[1].id
    assert first_nodes[1].attrs["logical_table"] == second_nodes[1].attrs["logical_table"]
    merge_drafts(session, first_nodes[0].id, first_nodes, [])
    merge_drafts(session, second_nodes[0].id, second_nodes, [])
    assert session.query(NodeRecord).count() == 4
    assert not any(record.vanished for record in session.query(NodeRecord).all())
    assert session.get(NodeRecord, first_nodes[0].id).attrs_dict["port"] == 3306


def test_instance_identity_includes_type_host_port_database_and_case():
    original = database_source("mysql", "db.internal", 3306, "sales")
    alternatives = {
        database_source("postgresql", "db.internal", 3306, "sales"),
        database_source("mysql", "other.internal", 3306, "sales"),
        database_source("mysql", "db.internal", 3307, "sales"),
        database_source("mysql", "db.internal", 3306, "other"),
        database_source("mysql", "db.internal", 3306, "Sales"),
        database_source("mysql", "db.internal", 3306, " sales "),
    }
    assert original not in alternatives and len(alternatives) == 6
    assert database_source("mysql", "DB.INTERNAL", 3306, "sales") == original
    assert physical_table_id(original, "Orders") != physical_table_id(original, "orders")
    assert db_id("mysql", "db.internal", "sales") == "db:mysql:db.internal:sales"


def test_recollect_retains_ambiguous_legacy_assets(monkeypatch, session):
    legacy_source = db_id("mysql", "db.internal", "sales")
    merge_drafts(session, legacy_source,
                 [NodeDraft("tbl:sales.orders", "table", "sales.orders", attrs={
                     "columns": [{"name": "old", "type": "TEXT"}]
                 })], [])
    _collector(monkeypatch)
    nodes, warnings = db_introspect.discover_database(
        "mysql", "db.internal", 3306, "reader", "test", "sales")
    merge_drafts(session, nodes[0].id, nodes, [], complete=not warnings)
    legacy = session.get(NodeRecord, "tbl:sales.orders")
    assert not legacy.vanished
    assert legacy.attrs_dict["columns"] == [{"name": "old", "type": "TEXT"}]
    assert session.get(NodeRecord, nodes[1].id) is not None


def test_column_reflection_failure_preserves_previous_columns(monkeypatch, session):
    _collector(monkeypatch)
    nodes, warnings = db_introspect.discover_database(
        "mysql", "db.internal", 3306, "reader", "test", "sales")
    merge_drafts(session, nodes[0].id, nodes, [], complete=not warnings)
    engine, _ = _collector(monkeypatch, column_error=True)
    retry_nodes, warnings = db_introspect.discover_database(
        "mysql", "db.internal", 3306, "reader", "test", "sales")
    assert warnings and "columns" not in retry_nodes[1].attrs
    merge_drafts(session, retry_nodes[0].id, retry_nodes, [], complete=not warnings)
    assert session.get(NodeRecord, nodes[1].id).attrs_dict["columns"] == [
        {"name": "id", "type": "INTEGER"}
    ]
    engine.dispose.assert_called_once()


def test_discovery_limit_does_not_deactivate_uncollected_table(monkeypatch, session):
    _collector(monkeypatch, tables=("a", "b"))
    nodes, warnings = db_introspect.discover_database(
        "mysql", "db.internal", 3306, "reader", "test", "sales")
    merge_drafts(session, nodes[0].id, nodes, [], complete=not warnings)
    monkeypatch.setattr(db_introspect, "MAX_TABLES", 1)
    limited_nodes, warnings = db_introspect.discover_database(
        "mysql", "db.internal", 3306, "reader", "test", "sales")
    assert warnings and len(limited_nodes) == 2
    merge_drafts(session, limited_nodes[0].id, limited_nodes, [], complete=not warnings)
    assert not session.get(NodeRecord, physical_table_id(nodes[0].id, "b")).vanished


def test_failed_table_reflection_still_releases_engine(monkeypatch):
    engine, _ = _collector(monkeypatch, table_error=True)
    with pytest.raises(ValueError, match="连接失败"):
        db_introspect.discover_database("postgresql", "db.internal", 5432,
                                        "reader", "test", "sales")
    engine.dispose.assert_called_once()
