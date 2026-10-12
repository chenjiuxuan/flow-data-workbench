"""持久化模型：统一图模型的节点与边。

设计要点（自动为主、人工修正的关键）：
- id 是跨导入稳定的业务标识（如 tbl:orders.orders），同一来源重复导入按 id 合并；
- provenance JSON 记录来源与人工改过的字段名（manual_fields），合并时跳过这些字段；
- vanished 只用于自动采集项：源中消失时打标而非删除。
"""
import json
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base


def _now() -> datetime:
    return datetime.now()


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
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now, onupdate=_now)

    @property
    def attrs_dict(self) -> dict:
        try:
            return json.loads(self.attrs)
        except (TypeError, ValueError):
            return {}

    @property
    def provenance_dict(self) -> dict:
        try:
            return json.loads(self.provenance)
        except (TypeError, ValueError):
            return {}


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
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now, onupdate=_now)

    @property
    def attrs_dict(self) -> dict:
        try:
            return json.loads(self.attrs)
        except (TypeError, ValueError):
            return {}

    @property
    def provenance_dict(self) -> dict:
        try:
            return json.loads(self.provenance)
        except (TypeError, ValueError):
            return {}
