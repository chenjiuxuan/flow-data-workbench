"""持久化模型：统一图模型的节点与边。

设计要点（自动为主、人工修正的关键）：
- id 是跨导入稳定的业务标识（如 tbl:orders.orders），同一来源重复导入按 id 合并；
- provenance JSON 记录来源与人工改过的字段名（manual_fields），合并时跳过这些字段；
- vanished 只用于自动采集项：源中消失时打标而非删除。
"""
import hashlib
import json
import logging
from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import Boolean, DateTime, Float, String, Text
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import TypeDecorator

from .db import Base

logger = logging.getLogger(__name__)


def _now() -> datetime:
    return datetime.now(timezone.utc)


class UTCDateTime(TypeDecorator):
    """SQLite DATETIME 写入 UTC naive，读取时补回 UTC 时区。

    timezone=True 本身不能阻止 SQLite 丢弃偏移，因此显式转换。已有 naive
    历史值不改写，按 UTC 解释；其原始时区没有保存，无法自动可靠迁移。
    """

    impl = DateTime
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            return value  # 兼容历史 naive 值，不猜测其原时区或平移原值。
        return value.astimezone(timezone.utc).replace(tzinfo=None)

    def process_result_value(self, value: datetime | None, dialect) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)


def _json_dict(record, field: str) -> dict:
    """损坏或非对象 JSON 兼容为空对象；只记录定位摘要，不记录原始内容。"""
    try:
        value = json.loads(getattr(record, field))
    except (TypeError, ValueError):
        reason = "invalid_json"
    else:
        if isinstance(value, dict):
            return value
        reason = "not_an_object"
    record_key = hashlib.sha256(str(record.id).encode("utf-8")).hexdigest()[:12]
    logger.warning("Persisted JSON rejected model=%s field=%s record_fingerprint=%s reason=%s",
                   type(record).__name__, field, record_key, reason)
    return {}


class NodeRecord(Base):
    __tablename__ = "nodes"

    id: Mapped[str] = mapped_column(String(220), primary_key=True)
    type: Mapped[str] = mapped_column(String(30), index=True)
    name: Mapped[str] = mapped_column(String(300))
    attrs: Mapped[str] = mapped_column(Text, default="{}")
    origin: Mapped[str] = mapped_column(String(20), default="manual")  # auto | manual
    vanished: Mapped[bool] = mapped_column(Boolean, default=False)
    x: Mapped[float] = mapped_column(Float, default=0)
    y: Mapped[float] = mapped_column(Float, default=0)
    provenance: Mapped[str] = mapped_column(Text, default="{}")
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=_now, onupdate=_now)

    @property
    def attrs_dict(self) -> dict:
        return _json_dict(self, "attrs")

    @property
    def provenance_dict(self) -> dict:
        return _json_dict(self, "provenance")


class EdgeRecord(Base):
    __tablename__ = "edges"

    id: Mapped[str] = mapped_column(String(300), primary_key=True)
    source_id: Mapped[str] = mapped_column(String(220), index=True)
    target_id: Mapped[str] = mapped_column(String(220), index=True)
    type: Mapped[str] = mapped_column(String(30), default="flow")  # flow | lineage | uses
    label: Mapped[str] = mapped_column(String(300), default="")
    attrs: Mapped[str] = mapped_column(Text, default="{}")
    origin: Mapped[str] = mapped_column(String(20), default="manual")
    vanished: Mapped[bool] = mapped_column(Boolean, default=False)
    provenance: Mapped[str] = mapped_column(Text, default="{}")
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=_now, onupdate=_now)

    @property
    def attrs_dict(self) -> dict:
        return _json_dict(self, "attrs")

    @property
    def provenance_dict(self) -> dict:
        return _json_dict(self, "provenance")


class ProcessDiagramRecord(Base):
    """独立流程文档：图中步骤、顺序边和排版不进入共享数据资产图。"""

    __tablename__ = "process_diagrams"

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, default="")
    content: Mapped[str] = mapped_column(Text, default='{"nodes":[],"edges":[]}')
    revision: Mapped[str] = mapped_column(String(64), default=lambda: uuid4().hex)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=_now, onupdate=_now)
