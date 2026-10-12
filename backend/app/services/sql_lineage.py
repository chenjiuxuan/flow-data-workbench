"""SQL 表级和字段级血缘，使用 sqlglot 的查询作用域和 lineage API。

没有元数据时只推导能确定的字段来源；未知的星号保留表级血缘，
歧义字段和解析失败会记录 errors，让调用方将结果视为部分快照。
"""
from dataclasses import dataclass, field

import sqlglot
from sqlglot import exp
from sqlglot.dialects import Dialect
from sqlglot.errors import SqlglotError
from sqlglot.lineage import Node, lineage
from sqlglot.optimizer.qualify import qualify
from sqlglot.optimizer.normalize_identifiers import normalize_identifiers
from sqlglot.optimizer.scope import Scope, build_scope, find_all_in_scope
from sqlglot.tokens import TokenType

DIALECTS = ["mysql", "postgresql", "oracle", "hive", "spark", "duckdb", "sqlite", "tsql"]


@dataclass
class LineageEdge:
    source_id: str
    target_id: str
    source_name: str
    target_name: str
    fields: list[str] = field(default_factory=list)
    statements: list[str] = field(default_factory=list)


@dataclass
class LineageResult:
    tables: dict[str, str] = field(default_factory=dict)
    edges: list[LineageEdge] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def add_table(self, tid: str, display: str) -> None:
        self.tables.setdefault(tid, display)

    def add_edge(self, source_id: str, target_id: str, source_name: str,
                 target_name: str, fields: list[str], statement: str) -> None:
        for edge in self.edges:
            if edge.source_id == source_id and edge.target_id == target_id:
                for mapping in fields:
                    if mapping not in edge.fields:
                        edge.fields.append(mapping)
                if statement not in edge.statements:
                    edge.statements.append(statement)
                return
        self.edges.append(LineageEdge(source_id, target_id, source_name, target_name,
                                      list(fields), [statement]))


def _full_name(table: exp.Table) -> str:
    return ".".join(p for p in (table.catalog, table.db, table.name) if p)


def _target_of(statement: exp.Expression) -> exp.Table | None:
    if isinstance(statement, (exp.Insert, exp.Update, exp.Merge)) or (
        isinstance(statement, exp.Create) and statement.kind == "TABLE"
    ):
        target = statement.this
        if isinstance(target, exp.Schema):
            target = target.this
        return target if isinstance(target, exp.Table) else None
    return None


def _query_of(statement: exp.Expression) -> tuple[exp.Expression | None, list[str] | None]:
    """UPDATE 的赋值投影成 SELECT，从而复用相同的作用域规则。"""
    targets = None
    if isinstance(statement, (exp.Insert, exp.Create)):
        query = statement.expression.copy() if statement.expression else None
        if isinstance(statement.this, exp.Schema):
            targets = [column.name for column in statement.this.expressions]
        if isinstance(query, exp.Values) and query.expressions:
            rows = [exp.Select(expressions=[exp.alias_(value.copy(), f"__dataflow_value_{i}")
                                           for i, value in enumerate(row.expressions)])
                    for row in query.expressions]
            query = rows[0]
            for row in rows[1:]:
                query = exp.Union(this=query, expression=row, distinct=False)
            if targets is None:
                targets = [""] * len(rows[0].expressions)
    elif isinstance(statement, exp.Update):
        assignments = [item for item in statement.expressions
                       if isinstance(item, exp.EQ) and isinstance(item.this, exp.Column)]
        targets = [item.this.name for item in assignments]
        query = exp.Select(expressions=[
            exp.alias_(item.expression.copy(), f"__dataflow_update_{i}")
            for i, item in enumerate(assignments)
        ])
        target = statement.this.copy()
        joins = list(target.args.pop("joins", []) or [])
        query = query.from_(target)
        from_clause = statement.args.get("from_")
        if from_clause:
            for source in [from_clause.this, *from_clause.expressions]:
                if source is not None:
                    joins.append(exp.Join(this=source.copy(), kind="CROSS"))
        joins.extend(join.copy() for join in statement.args.get("joins", []) or [])
        query.set("joins", joins)
        if statement.args.get("where"):
            query.set("where", statement.args["where"].copy())
    elif isinstance(statement, exp.Merge):
        using = statement.args.get("using")
        query = exp.select("*").from_(using.copy()) if using is not None else None
        if query is not None and statement.args.get("on"):
            query = query.where(statement.args["on"].copy())
        whens = statement.args.get("whens")
        if query is not None and whens is not None:
            for child in whens.find_all(exp.Query):
                if child.find_ancestor(exp.Query) is None:
                    subquery = child.copy() if isinstance(child, exp.Subquery) else child.copy().subquery()
                    query.append("expressions", subquery)
    else:
        query = None

    # WITH ... INSERT 和 INSERT ... WITH ... 两种写法的 WITH 所属节点不同。
    if query is not None and statement.args.get("with_"):
        outer_with = statement.args["with_"].copy()
        inner_with = query.args.get("with_")
        if inner_with:
            outer_with.set("expressions", outer_with.expressions + inner_with.expressions)
        query.set("with_", outer_with)
    return query, targets


def _local_sources(scope: Scope) -> dict[str, exp.Table | Scope]:
    # selected_sources 校验同一作用域中的重复别名。references 还包括 SEMI/ANTI
    # JOIN，它们不输出字段，但依旧是实际读取的表。
    scope.selected_sources
    return {name: scope.sources[name] for name, _ in scope.references if name in scope.sources}


def _physical_sources(root: Scope) -> dict[str, str]:
    sources: dict[str, str] = {}
    visited: set[int] = set()

    def visit(scope: Scope) -> None:
        if id(scope) in visited:
            return
        visited.add(id(scope))
        for source in _local_sources(scope).values():
            if isinstance(source, exp.Table):
                name = _full_name(source)
                if name:
                    sources.setdefault(name.lower(), name)
            elif isinstance(source, Scope):
                visit(source)
        for child in (*scope.set_operation_scopes, *scope.subquery_scopes):
            visit(child)

    visit(root)
    return sources


class _FieldResolver:
    """读取官方 lineage 图；补上无元数据的单来源和相关子查询引用。"""

    def __init__(self, scope: Scope, dialect: str, result: LineageResult, prefix: str):
        self.dialect = dialect
        self.result = result
        self.prefix = prefix
        self.scopes = {id(item.expression): item for item in scope.traverse()}
        self.resolving: set[tuple[int, str, str]] = set()
        self.column_cache: dict[tuple[int, str, str], tuple[set[tuple[str, str]], bool]] = {}
        self.failures = 0

    def error(self, message: str) -> None:
        self.failures += 1
        message = f"{self.prefix}字段血缘：{message}"
        if message not in self.result.errors:
            self.result.errors.append(message)

    def warning(self, message: str) -> None:
        message = f"{self.prefix}字段血缘：{message}"
        if message not in self.result.warnings:
            self.result.warnings.append(message)

    def column(self, column: exp.Column, scope: Scope) -> set[tuple[str, str]]:
        key = (id(scope), column.table, column.name)
        if key in self.column_cache:
            dependencies, valid = self.column_cache[key]
            if not valid:
                self.failures += 1
            return dependencies
        if key in self.resolving:
            self.error(f"循环依赖无法确定列 {column.sql()} 的物理来源")
            return set()
        self.resolving.add(key)
        try:
            failures_before = self.failures
            dependencies = self._column(column, scope)
            self.column_cache[key] = (dependencies, self.failures == failures_before)
            return dependencies
        finally:
            self.resolving.remove(key)

    def _column(self, column: exp.Column, scope: Scope) -> set[tuple[str, str]]:
        sources = _local_sources(scope)
        if column.table:
            source = sources.get(column.table)
            if source is None and scope.can_be_correlated and scope.parent:
                return self.column(column, scope.parent)
        elif len(sources) == 1:
            source = next(iter(sources.values()))
        elif not sources and scope.can_be_correlated and scope.parent:
            return self.column(column, scope.parent)
        else:
            source = None

        if isinstance(source, exp.Table):
            return {(_full_name(source), column.name)}
        if isinstance(source, Scope):
            projections = list(source.expression.selects)
            matching = [item for item in projections if item.alias_or_name == column.name]
            if len(matching) == 1:
                node = lineage(column.name, source.expression, schema={}, scope=source,
                               dialect=self.dialect, trim_selects=False)
                return self.dependencies(node, source)
            if len(matching) > 1:
                self.error(f"派生来源列 {column.sql()} 有多个同名输出")
                return set()
            stars = [item for item in projections if item.is_star]
            if len(stars) == 1:
                star = stars[0]
                return self.column(exp.column(column.name, table=star.table
                                               if isinstance(star, exp.Column) else None), source)
            self.error(f"派生来源没有可确定的输出列 {column.sql()}")
            return set()
        self.error(f"列 {column.sql()} 的来源不明确（需要限定表别名或提供元数据）")
        return set()

    def dependencies(self, node: Node, scope: Scope) -> set[tuple[str, str]]:
        scope = self.scopes.get(id(node.source), scope)
        if isinstance(node.expression, exp.Table):
            if node.name == "*":
                self.warning("缺少表字段元数据，* 仅保留表级血缘")
                return set()
            column = sqlglot.parse_one(node.name, dialect=self.dialect, into=exp.Column)
            return {(_full_name(node.expression), column.name)}
        if isinstance(node.expression, exp.Placeholder):
            column = sqlglot.parse_one(node.name, dialect=self.dialect, into=exp.Column)
            return self.column(column, scope)
        if isinstance(node.expression, exp.Star):
            if node.name == "*":
                self.warning("缺少表字段元数据，* 仅保留表级血缘")
                return set()
            # SELECT c.id FROM (SELECT * FROM src) c 可确定 id 的单一来源，
            # 但不据此虚构整张表的其他字段。
            requested = sqlglot.parse_one(node.name, dialect=self.dialect, into=exp.Column)
            stars = [item for item in scope.expression.selects if item.is_star]
            table = stars[0].table if len(stars) == 1 and isinstance(stars[0], exp.Column) else None
            return self.column(exp.column(requested.name, table=table), scope)
        dependencies: set[tuple[str, str]] = set()
        if not isinstance(scope.expression, exp.SetOperation):
            failures_before = self.failures
            for column in find_all_in_scope(node.expression, exp.Column):
                if not column.is_star:
                    dependencies.update(self.column(column, scope))
            if self.failures != failures_before:
                # lineage 对同名派生输出默认选择第一个；该列实际有歧义时
                # 不能继续采纳其下游叶子，造成看似完整的错误字段映射。
                return set()
        for child in node.downstream:
            dependencies.update(self.dependencies(child, scope))
        return dependencies


def _first_select(query: exp.Expression) -> exp.Select | None:
    while isinstance(query, (exp.SetOperation, exp.Subquery)):
        query = query.this
    return query if isinstance(query, exp.Select) else None


def _field_mappings(query: exp.Expression, targets: list[str] | None, dialect: str,
                    result: LineageResult, prefix: str) -> dict[str, list[str]]:
    qualified = qualify(query.copy(), dialect=dialect, schema={},
                        validate_qualify_columns=False, identify=False)
    scope = build_scope(qualified)
    if scope is None:
        return {}
    resolver = _FieldResolver(scope, dialect, result, prefix)

    # scope.sources 也含未使用的 CTE。仅按该作用域实际的 FROM/JOIN 来源
    # 为无元数据的单来源列补别名，避免 lineage 的宽泛回填误认 CTE。
    for item in scope.traverse():
        local_sources = _local_sources(item)
        if len(local_sources) == 1:
            alias = next(iter(local_sources))
            for column in find_all_in_scope(item.expression, exp.Column):
                if not column.table:
                    column.set("table", exp.to_identifier(alias))
        if isinstance(item.expression, exp.SetOperation):
            arities = {len(child.expression.selects) for child in item.set_operation_scopes
                       if not child.expression.is_star}
            if len(arities) > 1:
                resolver.error("集合查询各分支的输出列数量不同")
                return {}

    projections = list(qualified.selects)
    if any(item.is_star for item in projections):
        resolver.warning("缺少表字段元数据，* 仅保留表级血缘")
        return {}
    if targets is not None and len(targets) != len(projections):
        resolver.error(f"目标列数量 {len(targets)} 与查询输出数量 {len(projections)} 不同")
        return {}
    if targets is None:
        targets = [item.alias_or_name for item in projections]
        original = list(query.selects)
        if len(original) == len(projections):
            for i, item in enumerate(original):
                if not item.alias_or_name:
                    targets[i] = ""

    # 输出别名允许重复；临时别名只用于 lineage 查找，最终映射严格按位置。
    first = _first_select(qualified)
    if first is None:
        return {}
    parser_dialect = Dialect.get_or_raise(dialect)
    names = [parser_dialect.normalize_identifier(exp.to_identifier(f"__dataflow_output_{i}")).name
             for i in range(len(projections))]
    first.set("expressions", [exp.alias_(item.this.copy() if isinstance(item, exp.Alias)
                                       else item.copy(), name)
                              for item, name in zip(first.expressions, names)])
    scope = build_scope(qualified)
    resolver = _FieldResolver(scope, dialect, result, prefix)
    mappings: dict[str, list[str]] = {}
    for target, output in zip(targets, names):
        if not target:
            resolver.warning("无别名表达式无法确定目标字段名，已保留表级血缘")
            continue
        try:
            node = lineage(output, qualified, schema={}, dialect=dialect, scope=scope,
                           trim_selects=False)
            dependencies = resolver.dependencies(node, scope)
            by_source: dict[str, list[str]] = {}
            for source, column in sorted(dependencies):
                by_source.setdefault(source.lower(), []).append(f"{source}.{column}")
            for source, columns in by_source.items():
                mapping = f"{'+'.join(columns)} -> {target}"
                if mapping not in mappings.setdefault(source, []):
                    mappings[source].append(mapping)
        except (SqlglotError, ValueError, RecursionError) as error:
            resolver.error(f"目标列 {target} 无法解析：{error}")
    return mappings


def analyze_sql(sql_text: str, dialect: str = "mysql") -> LineageResult:
    result = LineageResult()
    dialect = (dialect or "mysql").strip().lower()
    dialect = {"postgresql": "postgres"}.get(dialect, dialect)
    try:
        parser_dialect = Dialect.get_or_raise(dialect)
        tokens = parser_dialect.tokenize(sql_text)
    except (SqlglotError, ValueError) as error:
        result.errors.append(f"SQL 方言或词法解析失败：{error}")
        return result

    # 使用方言 tokenizer 分隔语句，不会拆开字符串/注释里的分号。
    # 单句语法错误不会丢弃同一脚本中其他有效语句。
    boundaries = [token.start for token in tokens if token.token_type == TokenType.SEMICOLON]
    start = 0
    chunks: list[str] = []
    for boundary in boundaries:
        chunks.append(sql_text[start:boundary])
        start = boundary + 1
    chunks.append(sql_text[start:])
    for index, chunk in enumerate(chunks, 1):
        if not chunk.strip():
            continue
        prefix = f"第 {index} 条 SQL："
        try:
            statement = sqlglot.parse(chunk, dialect=dialect)[0]
        except (SqlglotError, ValueError) as error:
            result.errors.append(f"{prefix}解析失败：{error}")
            continue
        if statement is None:
            continue
        target = _target_of(statement)
        if target is None:
            continue
        target_name = _full_name(target)
        target_id = "tbl:" + target_name.lower()
        result.add_table(target_id, target_name)
        query, targets = _query_of(statement)
        if query is None or not isinstance(query, exp.Query):
            continue
        query = normalize_identifiers(query, dialect=dialect)
        try:
            scope = build_scope(query)
            sources = _physical_sources(scope) if scope else {}
        except (SqlglotError, ValueError, RecursionError) as error:
            result.errors.append(f"{prefix}来源作用域解析失败：{error}")
            continue
        mappings: dict[str, list[str]] = {}
        if not isinstance(statement, exp.Merge):
            try:
                mappings = _field_mappings(query, targets, dialect, result, prefix)
            except (SqlglotError, ValueError, RecursionError) as error:
                result.errors.append(f"{prefix}字段血缘解析失败：{error}")
        summary = f"{statement.key.upper()}: {chunk.strip()[:120]}"
        for source_key, source_name in sources.items():
            if source_key == target_name.lower():
                continue
            source_id = "tbl:" + source_key
            result.add_table(source_id, source_name)
            result.add_edge(source_id, target_id, source_name, target_name,
                            mappings.get(source_key, []), summary)
    return result
