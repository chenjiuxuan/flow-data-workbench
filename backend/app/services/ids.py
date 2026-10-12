"""稳定 ID：SQL/Excel 使用逻辑表身份，数据库采集使用独立的物理实例身份。

- 系统类:   sys:{名称}
- 数据库:   db:{库类型}:{主机}:{端口}:{库名}
- 逻辑表:   tbl:{库名.表名}（小写，SQL/Excel 一致）
- 物理表:   physical-tbl:{完整数据库实例身份}:{表名}
- 业务环节: step:{名称}
- 流转边:   flow:{源id}->{目标id}:{内容摘要}
- 血缘边:   lineage:{源表}->{目标表}
- 使用边:   uses:{环节}->{系统}
"""
import re
from urllib.parse import quote

from sqlglot import exp


def sys_id(name: str) -> str:
    return f"sys:{name.strip()}"


def step_id(name: str) -> str:
    return f"step:{name.strip()}"


def db_id(db_type: str, host: str, database: str, *, port: int | None = None) -> str:
    if port is None:
        # 兼容既有调用与备份。采集服务必须显式传端口，旧资产不会被改名/删除。
        return f"db:{db_type}:{host}:{database}".lower()
    parts = (db_type.strip().lower(), host.strip().lower(), str(port), database)
    return "db:" + ":".join(quote(part, safe="") for part in parts)


def database_source(db_type: str, host: str, port: int, database: str) -> str:
    """完整采集来源键，同时也是本次采集数据库节点的 ID。"""
    return db_id(db_type, host, database, port=port)


def physical_table_id(instance_id: str, table: str) -> str:
    return f"physical-tbl:{instance_id.removeprefix('db:')}:{quote(table, safe='')}"


def table_id(database: str | None, table: str) -> str:
    parts = ".".join(p for p in [(database or "").strip().lower(), table.strip().lower()] if p)
    return f"tbl:{parts}"


def sqlglot_table_id(table: exp.Table) -> str:
    """把 sqlglot 解析出的 Table 转成稳定 id：取 catalog.db.name 中非空段，小写。"""
    parts = [p for p in [table.catalog, table.db, table.name] if p]
    return "tbl:" + ".".join(parts).lower()


def flow_id(source_id: str, target_id: str, content: str) -> str:
    digest = re.sub(r"\s+", "", content.strip().lower())[:80] or "-"
    return f"flow:{source_id}->{target_id}:{digest}"


def lineage_id(source_tbl_id: str, target_tbl_id: str) -> str:
    return f"lineage:{source_tbl_id}->{target_tbl_id}"


def uses_id(step: str, system: str) -> str:
    return f"uses:{step_id(step)}->{sys_id(system)}"
