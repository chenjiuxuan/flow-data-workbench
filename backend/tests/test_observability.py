"""关键日志不泄漏输入，并验证 SQLite 时间戳的实际往返行为。"""
import logging
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock

import pytest
import sqlalchemy
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.db import Base
from app.models import EdgeRecord, NodeRecord, _now
from app.services import db_introspect
from app.services.merge import NodeDraft, merge_drafts
from app.services.sql_lineage import analyze_sql


@pytest.fixture
def session():
    engine = sqlalchemy.create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as test_session:
        yield test_session
    engine.dispose()


@pytest.mark.parametrize("model", [NodeRecord, EdgeRecord])
@pytest.mark.parametrize("field", ["attrs", "provenance"])
@pytest.mark.parametrize("raw", ['{"SECRET_JSON_VALUE":', '["SECRET_JSON_VALUE"]', "null"])
def test_corrupt_json_falls_back_to_dict_and_logs_safe_location(caplog, model, field, raw):
    record = model(id="SECRET_RECORD_ID", **{field: raw})
    with caplog.at_level(logging.WARNING):
        assert getattr(record, f"{field}_dict") == {}
    assert getattr(record, field) == raw  # 读取不会悄悄改写或丢弃原始数据。
    assert f"model={model.__name__} field={field} record_fingerprint=" in caplog.text
    assert "SECRET_JSON_VALUE" not in caplog.text
    assert "SECRET_RECORD_ID" not in caplog.text


def test_valid_json_dictionary_has_no_warning(caplog):
    record = NodeRecord(id="safe", attrs='{"owner": "someone"}', provenance="{}")
    with caplog.at_level(logging.WARNING):
        assert record.attrs_dict == {"owner": "someone"}
        assert record.provenance_dict == {}
    assert caplog.records == []


def test_utc_timestamp_round_trip_and_update_use_utc(session):
    before = _now()
    assert before.tzinfo is timezone.utc
    record = NodeRecord(id="node", type="table", name="node")
    session.add(record)
    session.commit()
    session.expire_all()
    loaded = session.get(NodeRecord, "node")
    assert loaded.updated_at.tzinfo is timezone.utc
    assert before <= loaded.updated_at <= _now()
    original = loaded.updated_at
    loaded.name = "changed"
    session.commit()
    session.expire_all()
    assert loaded.updated_at.tzinfo is timezone.utc
    assert loaded.updated_at >= original


def test_offset_timestamp_is_normalized_in_sqlite_and_read_back_aware(session):
    record = NodeRecord(id="offset", type="table", name="offset", updated_at=datetime(
        2026, 10, 12, 12, 30, tzinfo=timezone(timedelta(hours=8))))
    session.add(record)
    session.commit()
    raw = session.execute(text("SELECT updated_at FROM nodes WHERE id = :id"),
                          {"id": "offset"}).scalar_one()
    assert raw.startswith("2026-10-12 04:30:00")
    session.expire_all()
    assert session.get(NodeRecord, "offset").updated_at == datetime(
        2026, 10, 12, 4, 30, tzinfo=timezone.utc)


def test_legacy_naive_timestamp_keeps_wall_time_and_assumes_utc(session):
    record = EdgeRecord(id="legacy", source_id="a", target_id="b",
                        updated_at=datetime(2020, 1, 2, 3, 4, 5))
    session.add(record)
    session.commit()
    session.expire_all()
    assert session.get(EdgeRecord, "legacy").updated_at == datetime(
        2020, 1, 2, 3, 4, 5, tzinfo=timezone.utc)


def test_merge_provenance_is_utc_and_log_has_only_counts(session, caplog):
    with caplog.at_level(logging.INFO):
        merge_drafts(session, "sql:SECRET_SOURCE_NAME", [NodeDraft(
            "SECRET_NODE_ID", "table", "SECRET_NAME", {"SECRET_ATTR": "SECRET_VALUE"})], [])
    provenance = session.get(NodeRecord, "SECRET_NODE_ID").provenance_dict
    seen = provenance["sources"]["sql:SECRET_SOURCE_NAME"]
    assert datetime.fromisoformat(seen).utcoffset() == timedelta(0)
    assert "Graph merge completed nodes_added=1" in caplog.text
    assert "SECRET_" not in caplog.text


def test_failed_merge_logs_error_type_without_sql_parameters(session, caplog, monkeypatch):
    monkeypatch.setattr(session, "commit", Mock(side_effect=SQLAlchemyError("SECRET_SQL_PARAMS")))
    with caplog.at_level(logging.ERROR), pytest.raises(SQLAlchemyError):
        merge_drafts(session, "SECRET_SOURCE", [], [])
    assert "Graph merge commit failed error_type=SQLAlchemyError" in caplog.text
    assert "SECRET_" not in caplog.text


def test_discovery_failure_log_and_public_error_do_not_echo_driver_credentials(monkeypatch, caplog):
    engine = Mock()
    monkeypatch.setattr(sqlalchemy, "create_engine", Mock(return_value=engine))
    monkeypatch.setattr(sqlalchemy, "inspect", Mock(side_effect=RuntimeError(
        "postgresql://SECRET_USER:SECRET_PASSWORD@SECRET_HOST/SECRET_DATABASE")))
    with caplog.at_level(logging.WARNING), pytest.raises(ValueError, match="连接失败") as caught:
        db_introspect.discover_database("postgresql", "SECRET_HOST", 5432, "SECRET_USER",
                                        "SECRET_PASSWORD", "SECRET_DATABASE")
    assert "phase=connection error_type=RuntimeError" in caplog.text
    assert "SECRET_" not in caplog.text
    assert "SECRET_" not in str(caught.value)
    engine.dispose.assert_called_once()


def test_discovery_partial_column_failure_logs_phase_without_driver_details(monkeypatch, caplog):
    engine, inspector = Mock(), Mock()
    inspector.get_table_names.return_value = ["SECRET_TABLE"]
    inspector.get_columns.side_effect = RuntimeError("SECRET_DRIVER_DETAILS")
    monkeypatch.setattr(sqlalchemy, "create_engine", Mock(return_value=engine))
    monkeypatch.setattr(sqlalchemy, "inspect", Mock(return_value=inspector))
    with caplog.at_level(logging.INFO):
        nodes, warnings = db_introspect.discover_database(
            "mysql", "SECRET_HOST", 3306, "SECRET_USER", "SECRET_PASSWORD", "SECRET_DATABASE")
    assert warnings and "columns" not in nodes[1].attrs
    assert "reason=column_reflection table_index=1 error_type=RuntimeError" in caplog.text
    assert "Database discovery completed db_type=mysql tables=1 warnings=1" in caplog.text
    assert "SECRET_" not in caplog.text


@pytest.mark.parametrize("sql", [
    "INSERT INTO SECRET_TARGET SELECT SECRET_COL FROM",
    "INSERT INTO SECRET_TARGET SELECT * FROM SECRET_SOURCE",
    "ALTER SOMETHING SECRET_SQL_VALUE",
])
def test_sql_diagnostics_do_not_log_sql_or_identifiers(caplog, sql):
    with caplog.at_level(logging.INFO):
        analyze_sql(sql)
    assert "SQL lineage analyzed statements=" in caplog.text
    assert "SECRET_" not in caplog.text


def test_dependency_warning_is_redacted(caplog):
    with caplog.at_level(logging.WARNING):
        logging.getLogger("sqlglot").warning("SQL diagnostic: %s", "SECRET_SQL_CONTENT")
    assert "SQL parser diagnostic (SQL text omitted)" in caplog.text
    assert "SECRET_" not in caplog.text
