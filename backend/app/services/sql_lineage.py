"""SQL 血缘解析：基于 sqlglot，从写入型语句中提取表级 + 字段级血缘。

支持的语句：INSERT INTO ... SELECT、CREATE TABLE ... AS SELECT、
UPDATE ... SET col = ... FROM、MERGE INTO。
解析失败的语句不中断整体，逐条记录错误返回。

表命名：库名.表名（小写）；SQL 里未带库名的表只记表名。
字段映射：目标列 ← 来源列；投影是表达式时记录其涉及的全部来源列。
"""
from dataclasses import dataclass, field

import sqlglot
from sqlglot import exp

DIALECTS = ["mysql", "postgresql", "oracle", "hive", "spark", "duckdb", "sqlite", "tsql"]


@dataclass
class LineageEdge:
    """一条表级血缘；fields 是 "来源列 -> 目标列" 的映射描述列表。"""

    source_id: str
    target_id: str
    source_name: str
    target_name: str
    fields: list[str] = field(default_factory=list)
    statements: list[str] = field(default_factory=list)


@dataclass
class LineageResult:
    tables: dict[str, str] = field(default_factory=dict)  # id -> 显示名
    edges: list[LineageEdge] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def add_table(self, tid: str, display: str) -> None:
        self.tables.setdefault(tid, display)

    def add_edge(self, source_id: str, target_id: str, source_name: str,
                 target_name: str, fields: list[str], statement: str) -> None:
        for edge in self.edges:
            if edge.source_id == source_id and edge.target_id == target_id:
                for f in fields:
                    if f not in edge.fields:
                        edge.fields.append(f)
                if statement not in edge.statements:
                    edge.statements.append(statement)
                return
        self.edges.append(LineageEdge(source_id, target_id, source_name, target_name,
                                      list(fields), [statement]))


def _full_name(table: exp.Table) -> str:
    parts = [p for p in [table.catalog, table.db, table.name] if p]
    return ".".join(parts)


def _target_of(statement: exp.Expression) -> exp.Table | None:
    if isinstance(statement, exp.Insert):
        node = statement.this
        if isinstance(node, exp.Schema):
            node = node.this
        return node if isinstance(node, exp.Table) else None
    if isinstance(statement, exp.Create) and statement.kind == "TABLE":
        node = statement.this
        if isinstance(node, exp.Schema):
            node = node.this
        return node if isinstance(node, exp.Table) else None
    if isinstance(statement, exp.Update):
        return statement.this if isinstance(statement.this, exp.Table) else None
    if isinstance(statement, exp.Merge):
        return statement.this if isinstance(statement.this, exp.Table) else None
    return None


def _source_tables(statement: exp.Expression, target: exp.Table) -> list[exp.Table]:
    """取出语句里除写入目标外引用的物理表（排除 CTE 名）。"""
    cte_names = {cte.alias_or_name.lower() for cte in statement.find_all(exp.CTE)}
    target_key = (_full_name(target) or "").lower()
    tables: list[exp.Table] = []
    seen: set[str] = set()
    for table in statement.find_all(exp.Table):
        key = (_full_name(table) or "").lower()
        if not key or key == target_key or key in cte_names or key in seen:
            continue
        seen.add(key)
        tables.append(table)
    return tables


def _columns_under(node: exp.Expression) -> list[exp.Column]:
    return list(node.find_all(exp.Column))


def _qualify(col: exp.Column, alias_map: dict[str, str], default_table: str | None = None) -> str:
    """把列限定成 表名.列名：别名还原成真实表名；无别名列在有唯一来源表时回填，否则只返回列名。"""
    table = alias_map.get(col.table, col.table) if col.table else (default_table or "")
    return f"{table}.{col.name}" if table else col.name


def _column_mapping(statement: exp.Expression, select: exp.Expression | None,
                    alias_map: dict[str, str], default_table: str | None = None) -> list[str]:
    """目标列 <- 来源列 映射列表。无 SELECT（如 INSERT VALUES）返回空。"""
    if select is None:
        return []

    # 目标列名清单
    explicit_cols: list[str] | None = None
    if isinstance(statement, (exp.Insert, exp.Create)) and isinstance(statement.this, exp.Schema):
        explicit_cols = [c.name for c in statement.this.expressions]

    if isinstance(statement, exp.Update):
        mappings: list[str] = []
        for eq in statement.expressions:
            if not (isinstance(eq, exp.EQ) and isinstance(eq.this, exp.Column)):
                continue
            cols = _columns_under(eq.expression)
            qualified = [_qualify(c, alias_map, default_table) for c in cols] or ["(表达式)"]
            mappings.append(f"{'+'.join(qualified)} -> {eq.this.name}")
        return mappings

    if not isinstance(select, (exp.Select, exp.Union)):
        return []
    projections = list(select.expressions) if isinstance(select, exp.Select) else []
    mappings = []

    if explicit_cols is not None:
        # 显式列清单与 SELECT 投影按位置对应
        for i, tgt in enumerate(explicit_cols):
            proj = projections[i] if i < len(projections) else None
            if proj is None:
                continue
            proj_cols = _columns_under(proj)
            if not proj_cols:
                continue
            qualified = [_qualify(c, alias_map, default_table) for c in proj_cols]
            mappings.append(f"{'+'.join(qualified)} -> {tgt}")
        return mappings

    # 无显式列清单：投影别名/列名即目标列
    for proj in projections:
        tgt = proj.alias_or_name
        proj_cols = _columns_under(proj)
        if isinstance(proj, exp.Column):
            mappings.append(f"{_qualify(proj, alias_map, default_table)} -> {tgt}")
        elif proj_cols:
            qualified = [_qualify(c, alias_map, default_table) for c in proj_cols]
            mappings.append(f"{'+'.join(qualified)} -> {tgt}")
        else:
            mappings.append(f"(表达式) -> {tgt}")
    return mappings


def _select_of(statement: exp.Expression) -> exp.Expression | None:
    if isinstance(statement, exp.Insert):
        return statement.expression
    if isinstance(statement, exp.Create):
        return statement.expression
    if isinstance(statement, exp.Update):
        return statement
    if isinstance(statement, exp.Merge):
        return None  # MERGE 只做表级
    return None


def analyze_sql(sql_text: str, dialect: str = "mysql") -> LineageResult:
    result = LineageResult()
    try:
        statements = sqlglot.parse(sql_text, dialect=dialect)
    except sqlglot.errors.ParseError as e:
        result.errors.append(f"SQL 整体解析失败：{e}")
        return result

    for statement in statements:
        if statement is None:
            continue
        target = _target_of(statement)
        if target is None:
            continue  # 纯查询等语句不产生血缘

        target_name = _full_name(target)
        target_id = "tbl:" + target_name.lower()
        result.add_table(target_id, target_name)
        summary = f"{statement.key.upper()}: {sql_text.strip()[:120]}"

        sources = _source_tables(statement, target)
        if not sources:
            continue

        # 别名 -> 表全名
        alias_map: dict[str, str] = {}
        for table in sources:
            alias_map[table.alias_or_name] = _full_name(table)

        select = _select_of(statement)
        # 只有一个来源表时，无表限定列默认归到它名下
        default_table = _full_name(sources[0]) if len(sources) == 1 else None
        fields = _column_mapping(statement, select, alias_map, default_table) if select else []

        for source in sources:
            source_name = _full_name(source)
            source_id = "tbl:" + source_name.lower()
            result.add_table(source_id, source_name)
            result.add_edge(source_id, target_id, source_name, target_name, fields, summary)

    return result
