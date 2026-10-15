"""资产只读匹配。模型只看见 a1..aN，真实 ID 留在服务端。"""
import json

from sqlalchemy.orm import Session

from ..api import _node_out
from ..models import NodeRecord
from ..process_api import ASSET_TYPES

ASSET_LIMIT = 200
HIGH_CONFIDENCE = 0.8
_SECRET_KEYS = {"password", "secret", "token", "api_key", "apikey", "api-key"}


def load_available_assets(session: Session) -> list[dict]:
    records = session.query(NodeRecord).filter(
        NodeRecord.type.in_(ASSET_TYPES),
        NodeRecord.vanished.is_(False),
    ).order_by(NodeRecord.type, NodeRecord.name, NodeRecord.id).all()
    return [_node_out(record) for record in records]


def prefilter_assets(assets: list[dict], source: str, limit: int = ASSET_LIMIT) -> list[dict]:
    ranked = sorted(assets, key=lambda asset: (
        -_score(asset, source),
        asset.get("type") or "",
        asset.get("name") or "",
        asset.get("id") or "",
    ))
    return ranked[:limit]


def catalog_rows(assets: list[dict]) -> tuple[list[dict], dict[str, dict]]:
    """短编号清单。行里不含真实资产 ID。"""
    rows = []
    mapping: dict[str, dict] = {}
    for index, asset in enumerate(assets, start=1):
        ref = f"a{index}"
        mapping[ref] = asset
        rows.append({
            "ref": ref,
            "type": str(asset.get("type") or ""),
            "name": str(asset.get("name") or ""),
            "attrs": _summarize_attrs(asset.get("attrs") or {}),
        })
    return rows, mapping


def name_matches(node_id: str, haystack: str, assets: list[dict]) -> list[dict]:
    if not haystack.strip() or not assets:
        return []
    hits: list[tuple[dict, str]] = []
    label_owners: dict[str, int] = {}
    prepared = [(asset, _labels(asset)) for asset in assets]
    for _, labels in prepared:
        for label in labels:
            label_owners[label] = label_owners.get(label, 0) + 1
    seen: set[tuple[str, str]] = set()
    for asset, labels in prepared:
        asset_id = str(asset.get("id") or "")
        if not asset_id:
            continue
        for label in labels:
            if label not in haystack or (asset_id, label) in seen:
                continue
            seen.add((asset_id, label))
            hits.append((asset, label))
    suggestions = []
    for asset, label in hits:
        unique = label_owners.get(label, 0) == 1
        suggestions.append({
            "nodeId": node_id,
            "assetId": str(asset["id"]),
            "assetName": str(asset.get("name") or asset["id"]),
            "mention": label,
            "confidence": 0.86 if unique else 0.55,
            "preselect": False,
        })
    return suggestions


def apply_preselect(suggestions: list[dict]) -> list[dict]:
    groups: dict[tuple[str, str], list[dict]] = {}
    for item in suggestions:
        groups.setdefault((item["nodeId"], item["mention"]), []).append(item)
    for group in groups.values():
        preselect = len(group) == 1 and group[0]["confidence"] >= HIGH_CONFIDENCE
        for item in group:
            item["preselect"] = preselect
    return suggestions


def dedupe_suggestions(suggestions: list[dict]) -> list[dict]:
    best: dict[tuple[str, str, str], dict] = {}
    for item in suggestions:
        key = (item["nodeId"], item["assetId"], item["mention"])
        current = best.get(key)
        if current is None or item["confidence"] > current["confidence"]:
            best[key] = dict(item)
    return list(best.values())


def _score(asset: dict, source: str) -> int:
    if not source:
        return 0
    score = 0
    name = str(asset.get("name") or "").strip()
    if _usable_label(name) and name in source:
        score += 10
    asset_id = str(asset.get("id") or "")
    tail = asset_id.split(":")[-1]
    if _usable_label(tail) and tail in source:
        score += 4
    for value in _iter_strings(asset.get("attrs") or {}):
        cleaned = value.strip()
        if _usable_label(cleaned) and cleaned in source:
            score += 3
            break
    return score


def _labels(asset: dict) -> list[str]:
    labels: list[str] = []
    name = str(asset.get("name") or "").strip()
    if _usable_label(name):
        labels.append(name)
    for value in _iter_strings(asset.get("attrs") or {}):
        cleaned = value.strip()
        if _usable_label(cleaned) and cleaned not in labels:
            labels.append(cleaned)
    return labels


def _usable_label(value: str) -> bool:
    if len(value) < 2 or not any(char.isalnum() for char in value):
        return False
    if all(ord(char) < 128 for char in value) and len(value) < 4:
        return False
    return True


def _iter_strings(value, depth: int = 0):
    if depth > 4:
        return
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for key, item in value.items():
            if str(key).lower() in _SECRET_KEYS:
                continue
            yield from _iter_strings(item, depth + 1)
    elif isinstance(value, list):
        for item in value[:30]:
            yield from _iter_strings(item, depth + 1)


def _summarize_attrs(attrs: dict) -> str:
    if not isinstance(attrs, dict) or not attrs:
        return ""
    public = {key: value for key, value in attrs.items() if str(key).lower() not in _SECRET_KEYS}
    try:
        text = json.dumps(public, ensure_ascii=False, separators=(",", ":"), default=str)
    except (TypeError, ValueError):
        return ""
    return text[:180]
