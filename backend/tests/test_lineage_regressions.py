"""SQL 评审 R09–R12：核对真实物理字段，不只检查是否生成表级边。"""
import pytest

from app.services.sql_lineage import DIALECTS, analyze_sql


def fields(sql, dialect="mysql"):
    result = analyze_sql(sql, dialect)
    assert result.errors == []
    return {edge.source_name: edge.fields for edge in result.edges}


@pytest.mark.parametrize("dialect", DIALECTS)
def test_all_advertised_dialects_are_accepted(dialect):
    result = analyze_sql("CREATE TABLE dw.t AS SELECT id FROM src.users", dialect)
    assert result.errors == []
    assert result.edges[0].source_id == "tbl:src.users"
    assert [item.lower() for item in result.edges[0].fields] == ["src.users.id -> id"]


def test_unknown_dialect_returns_controlled_error():
    result = analyze_sql("CREATE TABLE dw.t AS SELECT id FROM src.users", "not_a_dialect")
    assert len(result.errors) == 1
    assert result.tables == {}
    assert result.edges == []


def test_postgres_unquoted_cte_names_follow_identifier_case_rules():
    sql = "INSERT INTO dw.t WITH Base AS (SELECT id AS UID FROM src.Users) SELECT UID FROM base"
    assert fields(sql, "postgresql") == {"src.users": ["src.users.id -> uid"]}


def test_quoted_names_with_spaces_remain_valid():
    sql = 'INSERT INTO dw.t (id) SELECT "User Alias"."Id" FROM "Source Table" AS "User Alias"'
    assert fields(sql, "postgresql") == {"Source Table": ["Source Table.Id -> id"]}


@pytest.mark.parametrize("sql", [
    "INSERT INTO dw.t WITH base AS (SELECT id AS user_id FROM src.users) SELECT user_id FROM base",
    "WITH base(user_id) AS (SELECT id FROM src.users) INSERT INTO dw.t SELECT user_id FROM base",
    """INSERT INTO dw.t WITH a AS (SELECT id AS uid FROM src.users),
       b AS (SELECT uid AS user_id FROM a) SELECT user_id FROM b""",
    """CREATE TABLE dw.t AS SELECT user_id FROM
       (SELECT uid AS user_id FROM (SELECT id AS uid FROM src.users) x) x""",
])
def test_cte_and_derived_outputs_trace_original_column(sql):
    assert fields(sql) == {"src.users": ["src.users.id -> user_id"]}


def test_unused_cte_is_excluded_without_poisoning_single_source_inference():
    sql = """INSERT INTO dw.t WITH unused AS (SELECT id FROM src.unused),
             used AS (SELECT id AS uid FROM src.users)
             SELECT uid FROM used"""
    result = analyze_sql(sql)
    assert result.errors == []
    assert set(result.tables) == {"tbl:dw.t", "tbl:src.users"}
    assert result.edges[0].fields == ["src.users.id -> uid"]


def test_unused_cte_is_not_a_default_source_for_query_without_from():
    result = analyze_sql("INSERT INTO dw.t WITH unused AS (SELECT id FROM src.unused) SELECT id")
    assert result.errors
    assert result.edges == []
    assert "tbl:src.unused" not in result.tables


def test_qualified_physical_table_is_not_hidden_by_same_cte_name():
    sql = """INSERT INTO dw.t WITH users AS (SELECT id FROM src.other)
             SELECT id FROM src.users"""
    assert fields(sql) == {"src.users": ["src.users.id -> id"]}


def test_join_edges_only_contain_their_own_columns_and_expression_dependencies():
    sql = """INSERT INTO dw.t (uid, amount, total)
             SELECT u.id, o.amount, u.credit + u.adjustment + o.amount
             FROM src.users u JOIN src.orders o ON u.id=o.user_id"""
    assert fields(sql) == {
        "src.users": ["src.users.id -> uid", "src.users.adjustment+src.users.credit -> total"],
        "src.orders": ["src.orders.amount -> amount", "src.orders.amount -> total"],
    }


def test_self_join_keeps_both_aliases_of_same_physical_source():
    sql = """INSERT INTO dw.t (child_id, parent_name) SELECT child.id, parent.name
             FROM src.users child JOIN src.users parent ON child.parent_id=parent.id"""
    assert fields(sql) == {"src.users": ["src.users.id -> child_id", "src.users.name -> parent_name"]}


def test_nested_scalar_subquery_alias_does_not_overwrite_outer_alias():
    sql = """INSERT INTO dw.t (uid, score) SELECT x.id,
             (SELECT MAX(x.value) FROM src.scores x) FROM src.users x"""
    assert fields(sql) == {
        "src.users": ["src.users.id -> uid"],
        "src.scores": ["src.scores.value -> score"],
    }


def test_correlated_subquery_projection_resolves_outer_scope():
    sql = """INSERT INTO dw.t (uid) SELECT
             (SELECT u.id FROM src.audit a WHERE a.user_id=u.id) FROM src.users u"""
    assert fields(sql) == {"src.users": ["src.users.id -> uid"], "src.audit": []}


def test_subquery_only_used_in_filter_is_table_dependency_without_false_field_mapping():
    sql = """INSERT INTO dw.t (uid) SELECT x.id FROM src.users x
             WHERE EXISTS (SELECT 1 FROM src.audit x WHERE x.flag=1)"""
    assert fields(sql) == {"src.users": ["src.users.id -> uid"], "src.audit": []}


@pytest.mark.parametrize("sql", [
    "INSERT INTO dw.t (id) SELECT id FROM src.a UNION ALL SELECT code FROM src.b",
    "CREATE TABLE dw.t AS SELECT id FROM src.a UNION SELECT code FROM src.b",
    """INSERT INTO dw.t (id) WITH combined AS
       (SELECT id AS uid FROM src.a UNION ALL SELECT code AS different_name FROM src.b)
       SELECT uid FROM combined""",
    """INSERT INTO dw.t (id) SELECT uid FROM
       (SELECT id AS uid FROM src.a UNION ALL SELECT code FROM src.b) x""",
])
def test_union_maps_every_branch_by_position_not_alias(sql):
    assert fields(sql) == {"src.a": ["src.a.id -> id"], "src.b": ["src.b.code -> id"]}


def test_nested_union_three_branches_and_two_targets():
    sql = """INSERT INTO dw.t (uid, label)
             SELECT id, name FROM src.a UNION ALL
             SELECT code, title FROM src.b UNION ALL SELECT key_id, description FROM src.c"""
    assert fields(sql) == {
        "src.a": ["src.a.id -> uid", "src.a.name -> label"],
        "src.b": ["src.b.code -> uid", "src.b.title -> label"],
        "src.c": ["src.c.key_id -> uid", "src.c.description -> label"],
    }


def test_duplicate_projection_aliases_still_map_by_explicit_target_position():
    sql = "INSERT INTO dw.t (uid, label) SELECT id AS repeated, name AS repeated FROM src.users"
    assert fields(sql) == {"src.users": ["src.users.id -> uid", "src.users.name -> label"]}


@pytest.mark.parametrize("sql", [
    "INSERT INTO dw.t (id) SELECT id FROM src.a a JOIN src.b b ON a.id=b.id",
    "INSERT INTO dw.t (id) SELECT missing.id FROM src.a a",
    "INSERT INTO dw.t (id) SELECT x.missing FROM (SELECT id FROM src.a) x",
    """INSERT INTO dw.t (id, other_id) SELECT x.id, x.id FROM
       (SELECT a.id,b.id FROM src.a a JOIN src.b b ON a.id=b.id) x""",
    "INSERT INTO dw.t (id) SELECT id FROM src.a UNION ALL SELECT id, name FROM src.b",
    "INSERT INTO dw.t (id, label) SELECT id FROM src.a",
    "INSERT INTO dw.t (id) SELECT x.id FROM src.a x JOIN src.b x ON x.id=x.id",
])
def test_unresolved_or_invalid_columns_are_errors_and_never_fabricated(sql):
    result = analyze_sql(sql)
    assert result.errors
    assert all(edge.fields == [] for edge in result.edges)


@pytest.mark.parametrize("sql", [
    "INSERT INTO dw.t SELECT * FROM src.users",
    "INSERT INTO dw.t (id, name) SELECT u.* FROM src.users u",
    "INSERT INTO dw.t WITH base AS (SELECT * FROM src.users) SELECT * FROM base",
])
def test_unknown_schema_star_preserves_table_lineage_and_warns(sql):
    result = analyze_sql(sql)
    assert result.errors == []
    assert result.warnings
    assert len(result.edges) == 1
    assert result.edges[0].source_id == "tbl:src.users"
    assert result.edges[0].fields == []


@pytest.mark.parametrize("sql, expected", [
    ("INSERT INTO dw.t (id) WITH c AS (SELECT * FROM src.users) SELECT id FROM c",
     {"src.users": ["src.users.id -> id"]}),
    ("""INSERT INTO dw.t (id) WITH c AS
        (SELECT u.* FROM src.users u JOIN src.audit a ON u.id=a.user_id) SELECT id FROM c""",
     {"src.users": ["src.users.id -> id"], "src.audit": []}),
])
def test_specific_column_through_single_known_star_source_is_resolved(sql, expected):
    assert fields(sql) == expected


def test_bad_statement_preserves_other_valid_preview_results():
    sql = """INSERT INTO dw.one (id) SELECT id FROM src.a;
             INSERT INTO broken SELECT ??? FROM;
             INSERT INTO dw.two (id) SELECT code FROM src.b"""
    result = analyze_sql(sql)
    assert len(result.errors) == 1
    assert {(edge.source_id, edge.target_id) for edge in result.edges} == {
        ("tbl:src.a", "tbl:dw.one"), ("tbl:src.b", "tbl:dw.two"),
    }
    assert result.edges[0].fields == ["src.a.id -> id"]
    assert result.edges[1].fields == ["src.b.code -> id"]


def test_statement_boundaries_ignore_strings_comments_and_trailing_comment():
    sql = """INSERT INTO dw.one (label) SELECT CONCAT(name, ';') FROM src.a;
             /* embedded ; separator */ INSERT INTO dw.two (id) SELECT id FROM src.b;
             -- trailing comment with ;"""
    result = analyze_sql(sql)
    assert result.errors == []
    assert len(result.edges) == 2
    assert result.edges[0].fields == ["src.a.name -> label"]


@pytest.mark.parametrize("sql", [
    "UPDATE dw.t SET name=s.name FROM src.users s WHERE s.id=dw.t.id",
    "UPDATE dw.t t JOIN src.users s ON t.id=s.id SET t.name=s.name",
    "UPDATE dw.t SET name=(SELECT MAX(name) FROM src.users)",
    "WITH c AS (SELECT name FROM src.users) UPDATE dw.t SET name=c.name FROM c",
])
def test_update_reuses_scoped_field_resolution(sql):
    assert fields(sql) == {"src.users": ["src.users.name -> name"]}


def test_merge_retains_table_only_lineage_from_derived_using():
    sql = """MERGE INTO dw.t t USING
             (SELECT u.id FROM src.users u JOIN src.audit a ON u.id=a.user_id) s
             ON t.id=s.id WHEN MATCHED THEN UPDATE SET t.id=s.id"""
    assert fields(sql) == {"src.users": [], "src.audit": []}


def test_merge_assignment_subquery_remains_a_table_dependency():
    sql = """MERGE INTO dw.t t USING src.users u ON t.id=u.id
             WHEN MATCHED THEN UPDATE SET name=(SELECT MAX(name) FROM src.audit)"""
    assert fields(sql) == {"src.users": [], "src.audit": []}


def test_insert_values_scalar_subqueries_preserve_read_tables():
    sql = """INSERT INTO dw.t (id) VALUES
             ((SELECT id FROM src.a)), ((SELECT code FROM src.b))"""
    assert fields(sql) == {"src.a": ["src.a.id -> id"], "src.b": ["src.b.code -> id"]}
