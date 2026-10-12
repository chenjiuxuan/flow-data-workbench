"""API 请求/响应模型。"""
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator


class NodeIn(BaseModel):
    id: str | None = Field(None, description="不传则按名称自动生成")
    type: str
    name: str
    attrs: dict = {}
    x: float = 0
    y: float = 0


class NodePatch(BaseModel):
    name: str | None = None
    attrs: dict | None = None
    x: float | None = None
    y: float | None = None
    vanished: bool | None = None
    manual: bool = Field(True, description="人工编辑的键会被记入 manual_fields，采集合并时不再覆盖")


class EdgeIn(BaseModel):
    id: str | None = None
    source_id: str
    target_id: str
    type: str = "flow"
    label: str = ""
    attrs: dict = {}


class EdgePatch(BaseModel):
    label: str | None = None
    attrs: dict | None = None
    manual: bool = True


class LayoutIn(BaseModel):
    positions: dict[str, tuple[float, float]] = Field(..., description="节点id -> [x, y]")


class SqlImportIn(BaseModel):
    sql_text: str
    dialect: str = "mysql"
    source_name: str = "SQL 脚本"
    apply: bool = Field(True, description="true 解析并入图；false 只预览")


class DbDiscoverIn(BaseModel):
    db_type: str = "mysql"
    host: str
    port: int = 3306
    user: str
    password: str = ""
    database: str
    apply: bool = True


class BackupItem(BaseModel):
    id: str = Field(min_length=1)
    type: str = Field(min_length=1)
    attrs: dict = Field(default_factory=dict)
    origin: Literal["auto", "manual"] = "manual"
    vanished: bool = Field(False, strict=True)
    provenance: dict = Field(default_factory=dict)
    manualFields: list[str] = Field(default_factory=list)

    @field_validator("provenance")
    @classmethod
    def validate_provenance(cls, value: dict) -> dict:
        fields = value.get("manual_fields", [])
        if not isinstance(fields, list) or any(not isinstance(f, str) for f in fields):
            raise ValueError("provenance.manual_fields 必须是字符串数组")
        for key in ("sources", "source_states"):
            if key in value and not isinstance(value[key], dict):
                raise ValueError(f"provenance.{key} 必须是对象")
        if not set(value.get("source_states", {})).issubset(value.get("sources", {})):
            raise ValueError("source_states 中的来源必须同时出现在 sources 中")
        for state in value.get("source_states", {}).values():
            if not isinstance(state, dict) or not isinstance(state.get("active"), bool):
                raise ValueError("source_states 中每个来源必须包含布尔 active 状态")
        return value

    @model_validator(mode="after")
    def retain_manual_fields(self):
        if "manual_fields" not in self.provenance and self.manualFields:
            self.provenance = {**self.provenance, "manual_fields": self.manualFields}
        return self


class BackupNode(BackupItem):
    name: str = Field(min_length=1)
    x: float = Field(0, allow_inf_nan=False)
    y: float = Field(0, allow_inf_nan=False)


class BackupEdge(BackupItem):
    source: str = Field(min_length=1)
    target: str = Field(min_length=1)
    type: str = "flow"
    label: str = ""


class JsonRestoreIn(BaseModel):
    nodes: list[BackupNode]
    edges: list[BackupEdge]
    mode: Literal["merge", "replace"] = Field("merge", description="merge=合并恢复，replace=清空后恢复")
    expected_revision: str | None = None

    @model_validator(mode="after")
    def unique_ids(self):
        for kind, items in (("节点", self.nodes), ("连线", self.edges)):
            ids = [item.id for item in items]
            if len(ids) != len(set(ids)):
                raise ValueError(f"备份中存在重复{kind} ID")
        return self


class GraphOut(BaseModel):
    nodes: list[dict]
    edges: list[dict]
