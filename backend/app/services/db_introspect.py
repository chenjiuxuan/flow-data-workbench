"""数据库只读采集：连上 MySQL / PostgreSQL，读取库表元数据生成资产节点。

只调用 SQLAlchemy inspector 做反射（information_schema 级别），不执行任何用户 SQL。
连接失败/驱动缺失统一转成带原因的 ValueError，由 API 层转 400。
"""
from .ids import db_id, table_id
from .merge import NodeDraft

MAX_TABLES = 2000
MAX_COLUMNS_PER_TABLE = 60

DB_TYPES = {
    "mysql": "mysql+pymysql",
    "postgresql": "postgresql+psycopg2",
}


def discover_database(db_type: str, host: str, port: int, user: str, password: str,
                      database: str) -> tuple[list[NodeDraft], list[str]]:
    """返回 (节点草稿, 警告列表)。"""
    if db_type not in DB_TYPES:
        raise ValueError(f"不支持的数据库类型：{db_type}（可选 mysql / postgresql）")
    driver = DB_TYPES[db_type]
    url = f"{driver}://{user}:{password}@{host}:{port}/{database}"
    warnings: list[str] = []

    from sqlalchemy import create_engine, inspect

    try:
        engine = create_engine(url, connect_args={"connect_timeout": 8} if db_type == "postgresql" else {})
        inspector = inspect(engine)
        tables = sorted(inspector.get_table_names())
    except Exception as e:  # noqa: BLE001 连接类错误原样带出
        raise ValueError(f"连接失败：{e}") from e

    if len(tables) > MAX_TABLES:
        tables = tables[:MAX_TABLES]
        warnings.append(f"表数量超过 {MAX_TABLES}，只采集前 {MAX_TABLES} 张")

    nodes: list[NodeDraft] = [
        NodeDraft(db_id(db_type, host, database), "database", f"{database}@{host}",
                  attrs={"db_type": db_type, "host": host, "port": port, "database": database}),
    ]
    for table in tables:
        try:
            columns = inspector.get_columns(table)
        except Exception:  # noqa: BLE001 单表失败不阻断
            columns = []
            warnings.append(f"表 {table} 的字段读取失败，已跳过字段信息")
        col_info = [
            {"name": c.get("name", ""), "type": str(c.get("type", ""))}
            for c in columns[:MAX_COLUMNS_PER_TABLE]
        ]
        if len(columns) > MAX_COLUMNS_PER_TABLE:
            warnings.append(f"表 {table} 字段超过 {MAX_COLUMNS_PER_TABLE} 个，只保留前 {MAX_COLUMNS_PER_TABLE} 个")
        nodes.append(NodeDraft(
            table_id(database, table), "table", f"{database}.{table}".lower(),
            attrs={"database": database.lower(), "table": table.lower(),
                   "host": host, "db_type": db_type, "columns": col_info},
        ))
    return nodes, warnings
