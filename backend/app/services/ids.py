"""稳定 ID 规范：不同采集来源对同一对象产出相同 id，合并时才能对上。

- 系统类:   sys:{名称}
- 数据库:   db:{库类型}:{主机}:{库名}
- 表:      tbl:{库名.表名}（小写；SQL/Excel/采集三方一致）
- 业务环节: step:{名称}
- 流转边:   flow:{源id}->{目标id}:{内容摘要}
- 血缘边:   lineage:{源表}->{目标表}
- 使用边:   uses:{环节}->{系统}
"""
import re

from sqlglot import exp


def sys_id(name: str) -> str:
    return f"sys:{name.strip()}"


def step_id(name: str) -> str:
    return f"step:{name.strip()}"


def db_id(db_type: str, host: str, database: str) -> str:
    return f"db:{db_type}:{host}:{database}".lower()


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
