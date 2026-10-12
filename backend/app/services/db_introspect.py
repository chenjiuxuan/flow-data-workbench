"""数据库只读采集：生成按实例隔离的物理资产，不执行用户 SQL。

返回 (节点草稿, 警告列表)。出现警告意味着快照不完整，调用方应使用
merge_drafts(..., complete=False)，避免因限额或反射失败错误地停用资产。
"""
from .ids import database_source, physical_table_id, table_id
from .merge import NodeDraft

MAX_TABLES = 2000
MAX_COLUMNS_PER_TABLE = 60

DB_TYPES = {
    "mysql": "mysql+pymysql",
    "postgresql": "postgresql+psycopg2",
}


def discover_database(db_type: str, host: str, port: int, user: str, password: str,
                      database: str) -> tuple[list[NodeDraft], list[str]]:
    """返回物理库/表草稿；字段失败时省略 columns，以保留先前成功的字段。"""
    if db_type not in DB_TYPES:
        raise ValueError(f"不支持的数据库类型：{db_type}（可选 mysql / postgresql）")
    from sqlalchemy import create_engine, inspect
    from sqlalchemy.engine import URL

    url = URL.create(DB_TYPES[db_type], username=user, password=password,
                     host=host, port=port, database=database)
    instance_id = database_source(db_type, host, port, database)
    warnings: list[str] = []
    engine = None
    try:
        engine = create_engine(url, connect_args={"connect_timeout": 8})
        inspector = inspect(engine)
        tables = sorted(inspector.get_table_names())
        if len(tables) > MAX_TABLES:
            tables = tables[:MAX_TABLES]
            warnings.append(f"表数量超过 {MAX_TABLES}，只采集前 {MAX_TABLES} 张；保留未采集资产")

        nodes: list[NodeDraft] = [
            NodeDraft(instance_id, "database", f"{database}@{host}:{port}",
                      attrs={"db_type": db_type, "host": host, "port": port, "database": database}),
        ]
        for table in tables:
            attrs = {"database": database, "table": table, "host": host, "port": port,
                     "db_type": db_type, "instance_id": instance_id,
                     "logical_table": table_id(database, table)}
            try:
                columns = inspector.get_columns(table)
            except Exception:  # noqa: BLE001 单表失败不阻断，更不能把失败当成空字段
                warnings.append(f"表 {table} 的字段读取失败，已保留上次字段信息")
            else:
                attrs["columns"] = [
                    {"name": c.get("name", ""), "type": str(c.get("type", ""))}
                    for c in columns[:MAX_COLUMNS_PER_TABLE]
                ]
                if len(columns) > MAX_COLUMNS_PER_TABLE:
                    warnings.append(f"表 {table} 字段超过 {MAX_COLUMNS_PER_TABLE} 个，只保留前 {MAX_COLUMNS_PER_TABLE} 个")
            nodes.append(NodeDraft(physical_table_id(instance_id, table), "table",
                                   f"{database}.{table}@{host}:{port}", attrs=attrs))
        return nodes, warnings
    except Exception as e:  # noqa: BLE001 连接/表列表反射错误统一由 API 转为 400
        raise ValueError(f"连接失败：{e}") from e
    finally:
        if engine is not None:
            engine.dispose()
