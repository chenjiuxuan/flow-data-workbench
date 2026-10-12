"""API 请求/响应模型。"""
from pydantic import BaseModel, Field


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
    positions: dict[str, list[float]] = Field(..., description="节点id -> [x, y]")


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


class JsonRestoreIn(BaseModel):
    nodes: list[dict] = []
    edges: list[dict] = []
    mode: str = Field("merge", description="merge=合并恢复，replace=清空后恢复")


class GraphOut(BaseModel):
    nodes: list[dict]
    edges: list[dict]
