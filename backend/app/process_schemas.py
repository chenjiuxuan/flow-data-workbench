"""流程文档输入契约：允许未完成草稿，但拒绝断边和错误节点语义。"""
import math
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class ProcessModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ProcessNode(ProcessModel):
    id: str = Field(min_length=1, max_length=200)
    type: Literal["start", "task", "decision", "end"]
    name: str = Field(min_length=1, max_length=200)
    x: float = Field(strict=True, allow_inf_nan=False, ge=-1_000_000, le=1_000_000)
    y: float = Field(strict=True, allow_inf_nan=False, ge=-1_000_000, le=1_000_000)
    owner: str = Field("", max_length=300)
    description: str = Field("", max_length=4000)
    input: str = Field("", max_length=2000)
    output: str = Field("", max_length=2000)
    assetIds: list[str] = Field(default_factory=list, max_length=100)

    @field_validator("x", "y", mode="before")
    @classmethod
    def serializable_coordinate_error(cls, value):
        # Python JSON 解析器接受 NaN/Infinity。先转为字符串再由有限数约束拒绝，
        # 避免 FastAPI 的 422 响应携带非有限 float，进而在编码时变成 500。
        if isinstance(value, float) and not math.isfinite(value):
            return str(value)
        return value

    @field_validator("id", "name")
    @classmethod
    def nonblank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("节点 ID 和名称不能为空")
        return value

    @field_validator("assetIds")
    @classmethod
    def unique_assets(cls, values: list[str]) -> list[str]:
        if any(not value.strip() or len(value) > 220 for value in values):
            raise ValueError("资产 ID 必须是 1 至 220 个字符的非空字符串")
        if len(values) != len(set(values)):
            raise ValueError("同一步骤不能重复关联相同资产")
        return values


class ProcessEdge(ProcessModel):
    id: str = Field(min_length=1, max_length=200)
    source: str = Field(min_length=1, max_length=200)
    target: str = Field(min_length=1, max_length=200)
    label: str = Field("", max_length=300)
    sourceHandle: Literal["out", "yes", "no"] | None = None
    targetHandle: Literal["in"] | None = None

    @field_validator("id", "source", "target")
    @classmethod
    def nonblank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("连线 ID 和端点不能为空")
        return value


class ProcessCreate(ProcessModel):
    name: str = Field(min_length=1, max_length=200)
    description: str = Field("", max_length=4000)
    nodes: list[ProcessNode] = Field(default_factory=list, max_length=500)
    edges: list[ProcessEdge] = Field(default_factory=list, max_length=2000)

    @field_validator("name")
    @classmethod
    def nonblank_name(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("流程名称不能为空")
        return value.strip()

    @model_validator(mode="after")
    def valid_graph(self):
        nodes = {node.id: node for node in self.nodes}
        if len(nodes) != len(self.nodes):
            raise ValueError("节点 ID 在同一流程内必须唯一")
        if len({edge.id for edge in self.edges}) != len(self.edges):
            raise ValueError("连线 ID 在同一流程内必须唯一")
        for edge in self.edges:
            if edge.source not in nodes or edge.target not in nodes:
                raise ValueError("连线的起点和终点必须存在于当前流程")
            if edge.source == edge.target:
                raise ValueError("不支持节点自连接")
            source, target = nodes[edge.source], nodes[edge.target]
            if source.type == "end":
                raise ValueError("结束节点不能有出边")
            if target.type == "start":
                raise ValueError("开始节点不能有入边")
            allowed = {"yes", "no"} if source.type == "decision" else {"out"}
            if edge.sourceHandle is not None and edge.sourceHandle not in allowed:
                raise ValueError("连线起点句柄与节点类型不匹配")
        return self


class ProcessUpdate(ProcessCreate):
    expected_revision: str = Field(min_length=1, max_length=64)
