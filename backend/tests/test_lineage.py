"""SQL 血缘解析测试。"""
from app.services.sql_lineage import analyze_sql


def test_insert_select_with_join_and_aliases():
    sql = """
    INSERT INTO dw.dwd_order (order_id, pay_amount, dt)
    SELECT o.order_id, o.amount + f.fee, o.created_at
    FROM orders.orders o
    JOIN orders.order_fee f ON f.order_id = o.order_id
    WHERE o.status = 'PAID'
    """
    result = analyze_sql(sql, "mysql")
    assert "tbl:orders.orders" in result.tables
    assert "tbl:orders.order_fee" in result.tables
    assert "tbl:dw.dwd_order" in result.tables
    edges = {(e.source_id, e.target_id) for e in result.edges}
    assert ("tbl:orders.orders", "tbl:dw.dwd_order") in edges
    assert ("tbl:orders.order_fee", "tbl:dw.dwd_order") in edges
    edge = next(e for e in result.edges if e.source_id == "tbl:orders.orders")
    assert "orders.o.order_id -> order_id" in edge.fields or any("order_id -> order_id" in f for f in edge.fields)


def test_ctas():
    sql = "CREATE TABLE dw.t AS SELECT id, name FROM src.users WHERE age > 18"
    result = analyze_sql(sql, "mysql")
    assert "tbl:dw.t" in result.tables
    assert "tbl:src.users" in result.tables
    assert len(result.edges) == 1
    assert any("src.users.id -> id" in f for f in result.edges[0].fields)


def test_update_from():
    sql = "UPDATE dw.t SET name = s.name, amt = s.amount FROM src.stg s WHERE s.id = dw.t.id"
    result = analyze_sql(sql, "mysql")
    assert ("tbl:src.stg", "tbl:dw.t") in {(e.source_id, e.target_id) for e in result.edges}
    edge = result.edges[0]
    assert any("-> name" in f for f in edge.fields)
    assert any("-> amt" in f for f in edge.fields)


def test_cte_excluded_from_sources():
    sql = """
    INSERT INTO dw.result
    WITH base AS (SELECT * FROM raw.events)
    SELECT * FROM base
    """
    result = analyze_sql(sql, "hive")
    sources = {e.source_id for e in result.edges}
    assert "tbl:raw.events" in sources
    assert "tbl:base" not in sources
    assert "tbl:dw.result" in result.tables


def test_unqualified_table():
    result = analyze_sql("INSERT INTO summary SELECT * FROM events", "mysql")
    assert "tbl:events" in result.tables
    assert "tbl:summary" in result.tables


def test_parse_error_recorded_not_raised():
    result = analyze_sql("INSERT INTO x SELECT ??? FROM", "mysql")
    assert len(result.errors) == 1
    assert result.tables == {}


def test_non_write_statement_ignored():
    result = analyze_sql("SELECT * FROM some_table", "mysql")
    assert result.edges == []
    assert result.tables == {}
