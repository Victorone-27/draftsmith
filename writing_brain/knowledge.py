from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .text import slug_for_filename


def stable_entity_id(entity_type: str, statement: str) -> str:
    slug = slug_for_filename(statement, entity_type).lower()
    return f"{entity_type}_{slug[:48]}"


def load_knowledge_entities(root: Path) -> list[dict[str, Any]]:
    if not root.exists():
        return []

    merged: dict[tuple[str, str], dict[str, Any]] = {}
    files = sorted(root.glob("*.json"), key=lambda path: path.stat().st_mtime, reverse=True)
    for path in files:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        entity = dict(payload.get("knowledge_entity") or {})
        entity_type = str(entity.get("entity_type") or "").strip()
        statement = str(entity.get("statement") or "").strip()
        if not entity_type or not statement:
            continue
        key = (entity_type, statement)
        canonical = _normalize_entity(entity)
        previous = merged.get(key)
        merged[key] = canonical if previous is None else merge_knowledge_entity(previous, canonical)

    return sorted(
        merged.values(),
        key=lambda item: (-float(item.get("confidence", 0.0)), -int(item.get("support_count", 1)), item.get("statement", "")),
    )


def merge_knowledge_entity(existing: dict[str, Any], incoming: dict[str, Any]) -> dict[str, Any]:
    entity_type = str(incoming.get("entity_type") or existing.get("entity_type") or "").strip()
    statement = str(incoming.get("statement") or existing.get("statement") or "").strip()
    existing_runs = _dedup_texts(existing.get("source_feedback_run_ids") or [])
    incoming_runs = _dedup_texts(incoming.get("source_feedback_run_ids") or [])
    all_runs = _dedup_texts([*existing_runs, *incoming_runs])
    support_count = max(len(all_runs), int(existing.get("support_count") or 1), int(incoming.get("support_count") or 1))
    base_confidence = max(float(existing.get("confidence") or 0.0), float(incoming.get("confidence") or 0.0))
    confidence = min(0.95, round(base_confidence + min(0.02 * max(support_count - 1, 0), 0.1), 2))

    existing_source = str(existing.get("source") or "").strip()
    incoming_source = str(incoming.get("source") or "").strip()
    if existing_source and incoming_source and existing_source != incoming_source:
        source = "mixed"
    else:
        source = incoming_source or existing_source

    return {
        "entity_id": str(existing.get("entity_id") or incoming.get("entity_id") or stable_entity_id(entity_type, statement)),
        "entity_type": entity_type,
        "statement": statement,
        "applies_to": _merge_applies_to(existing.get("applies_to") or {}, incoming.get("applies_to") or {}),
        "source_feedback_run_ids": all_runs,
        "confidence": confidence,
        "support_count": support_count,
        "source": source,
    }


def persist_knowledge_entities(root: Path, entities: list[dict[str, Any]]) -> list[dict[str, Any]]:
    root.mkdir(parents=True, exist_ok=True)
    existing_entities = load_knowledge_entities(root)
    merged_index = {
        (str(item.get("entity_type") or ""), str(item.get("statement") or "")): item
        for item in existing_entities
    }

    persisted: list[dict[str, Any]] = []
    for entity in entities:
        normalized = _normalize_entity(entity)
        key = (normalized["entity_type"], normalized["statement"])
        previous = merged_index.get(key)
        merged = normalized if previous is None else merge_knowledge_entity(previous, normalized)
        merged["entity_id"] = str(previous.get("entity_id") if previous else merged["entity_id"])
        merged_index[key] = merged
        path = root / f"{merged['entity_id']}.json"
        path.write_text(json.dumps({"knowledge_entity": merged}, ensure_ascii=False, indent=2), encoding="utf-8")
        persisted.append(merged)
    return persisted


def _normalize_entity(entity: dict[str, Any]) -> dict[str, Any]:
    entity_type = str(entity.get("entity_type") or "").strip()
    statement = str(entity.get("statement") or "").strip()
    applies_to = dict(entity.get("applies_to") or {})
    runs = _dedup_texts(entity.get("source_feedback_run_ids") or [])
    support_count = max(len(runs), int(entity.get("support_count") or 1))
    return {
        "entity_id": str(entity.get("entity_id") or stable_entity_id(entity_type, statement)),
        "entity_type": entity_type,
        "statement": statement,
        "applies_to": {
            "platform": str(applies_to.get("platform") or "").strip(),
            "topic_keywords": _dedup_texts(applies_to.get("topic_keywords") or []),
            "article_stage": str(applies_to.get("article_stage") or "").strip(),
        },
        "source_feedback_run_ids": runs,
        "confidence": round(float(entity.get("confidence") or 0.0), 2),
        "support_count": support_count,
        "source": str(entity.get("source") or "").strip(),
    }


def _merge_applies_to(existing: dict[str, Any], incoming: dict[str, Any]) -> dict[str, Any]:
    existing_platform = str(existing.get("platform") or "").strip()
    incoming_platform = str(incoming.get("platform") or "").strip()
    if existing_platform and incoming_platform and existing_platform != incoming_platform:
        platform = ""
    else:
        platform = incoming_platform or existing_platform

    existing_stage = str(existing.get("article_stage") or "").strip()
    incoming_stage = str(incoming.get("article_stage") or "").strip()
    if existing_stage and incoming_stage and existing_stage != incoming_stage:
        article_stage = "global"
    else:
        article_stage = incoming_stage or existing_stage or "global"

    return {
        "platform": platform,
        "topic_keywords": _dedup_texts([*(existing.get("topic_keywords") or []), *(incoming.get("topic_keywords") or [])]),
        "article_stage": article_stage,
    }


def _dedup_texts(values: list[Any]) -> list[str]:
    ordered: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = str(value).strip()
        if not text or text in seen:
            continue
        seen.add(text)
        ordered.append(text)
    return ordered
