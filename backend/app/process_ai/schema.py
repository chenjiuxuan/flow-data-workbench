"""中间稿与对外响应。落库 schema 不在这里扩展。"""
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from ..process_schemas import ProcessCreate

UncertaintyKind = Literal[
    "order", "missing_branch", "owner", "conflict",
    "missing_start", "missing_end", "unreachable", "other",
]
InputKind = Literal["description", "minutes", "sop"]


def _clamp_unit(value) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    if number != number or number in (float("inf"), float("-inf")):
        return 0.0
    return max(0.0, min(1.0, number))


class GenModel(BaseModel):
    model_config = ConfigDict(extra="ignore")


class GenBranch(GenModel):
    to: str | None = None
    label: str = ""


class GenAssetRef(GenModel):
    ref: str = ""
    mention: str = ""
    confidence: float = 0.5

    @field_validator("confidence", mode="before")
    @classmethod
    def clamp_confidence(cls, value):
        return _clamp_unit(value)


class GenStep(GenModel):
    key: str = ""
    kind: Literal["task", "decision", "start", "end"] = "task"
    name: str = ""
    owner: str = ""
    ownerCandidates: list[str] = Field(default_factory=list)
    role: str = ""
    description: str = ""
    input: str = ""
    output: str = ""
    next: str | None = None
    yes: GenBranch | None = None
    no: GenBranch | None = None
    uncertain: bool = False
    reason: str = ""
    sourceQuote: str = ""
    confidence: float = 1.0
    assetRefs: list[GenAssetRef] = Field(default_factory=list)
    unmatchedMentions: list[str] = Field(default_factory=list)

    @field_validator("kind", mode="before")
    @classmethod
    def normalize_kind(cls, value):
        if isinstance(value, str):
            return value.strip().lower()
        return value

    @field_validator("confidence", mode="before")
    @classmethod
    def clamp_confidence(cls, value):
        return _clamp_unit(value)


class GenDraft(GenModel):
    isProcess: bool
    notProcessReason: str = ""
    suggestedName: str = ""
    summary: str = ""
    steps: list[GenStep] = Field(default_factory=list)


class UncertaintyOut(GenModel):
    model_config = ConfigDict(extra="forbid")
    nodeId: str
    kind: UncertaintyKind
    reason: str
    sourceQuote: str = ""


class AssetSuggestionOut(GenModel):
    model_config = ConfigDict(extra="forbid")
    nodeId: str
    assetId: str
    assetName: str
    mention: str
    confidence: float
    preselect: bool


class UnmatchedAssetOut(GenModel):
    model_config = ConfigDict(extra="forbid")
    nodeId: str
    mention: str


class DraftStatsOut(GenModel):
    model_config = ConfigDict(extra="forbid")
    model: str
    latencyMs: int = Field(ge=0)
    llmCalls: Literal[1, 2]
    repaired: bool


class AiDraftResponseModel(GenModel):
    model_config = ConfigDict(extra="forbid")
    draft: ProcessCreate
    uncertainties: list[UncertaintyOut]
    assetSuggestions: list[AssetSuggestionOut]
    unmatchedAssets: list[UnmatchedAssetOut]
    sourceText: str
    stats: DraftStatsOut


def _nullable(schema: dict) -> dict:
    return {"anyOf": [schema, {"type": "null"}]}


_BRANCH_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "to": {"type": ["string", "null"]},
        "label": {"type": "string"},
    },
    "required": ["to", "label"],
}

_ASSET_REF_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "ref": {"type": "string"},
        "mention": {"type": "string"},
        "confidence": {"type": "number"},
    },
    "required": ["ref", "mention", "confidence"],
}

_STEP_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "key": {"type": "string"},
        "kind": {"type": "string", "enum": ["task", "decision", "start", "end"]},
        "name": {"type": "string"},
        "owner": {"type": "string"},
        "ownerCandidates": {"type": "array", "items": {"type": "string"}},
        "role": {"type": "string"},
        "description": {"type": "string"},
        "input": {"type": "string"},
        "output": {"type": "string"},
        "next": {"type": ["string", "null"]},
        "yes": _nullable(_BRANCH_SCHEMA),
        "no": _nullable(_BRANCH_SCHEMA),
        "uncertain": {"type": "boolean"},
        "reason": {"type": "string"},
        "sourceQuote": {"type": "string"},
        "confidence": {"type": "number"},
        "assetRefs": {"type": "array", "items": _ASSET_REF_SCHEMA},
        "unmatchedMentions": {"type": "array", "items": {"type": "string"}},
    },
    "required": [
        "key", "kind", "name", "owner", "ownerCandidates", "role",
        "description", "input", "output", "next", "yes", "no",
        "uncertain", "reason", "sourceQuote", "confidence",
        "assetRefs", "unmatchedMentions",
    ],
}

GEN_DRAFT_JSON_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "isProcess": {"type": "boolean"},
        "notProcessReason": {"type": "string"},
        "suggestedName": {"type": "string"},
        "summary": {"type": "string"},
        "steps": {"type": "array", "items": _STEP_SCHEMA},
    },
    "required": ["isProcess", "notProcessReason", "suggestedName", "summary", "steps"],
}
