from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from .config import AppConfig, ensure_runtime_dirs
from .knowledge import persist_knowledge_entities, stable_entity_id
from .text import now_run_id, slug_for_filename


def ingest_memory_record(payload: dict[str, Any], config: AppConfig) -> dict[str, Any]:
    ensure_runtime_dirs(config)

    run_id = str(payload.get("run_id") or now_run_id())
    topic = str(payload.get("topic") or "untitled").strip()
    platform = str(payload.get("platform") or "wechat").strip()
    final_article_markdown = str(payload.get("final_article_markdown") or payload.get("draft_text") or "").strip()
    session_summary = str(payload.get("session_summary") or "").strip()
    review_report = dict(payload.get("review_report") or {})
    context_pack = dict(payload.get("context_pack") or {})
    human_feedback = dict(payload.get("human_feedback") or {})

    article_summary = _article_summary(final_article_markdown)
    review_takeaways = list(dict.fromkeys([*(review_report.get("rewrite_actions") or []), *_feedback_takeaways(human_feedback)]))
    raw_knowledge_entities = _derive_knowledge_entities(
        run_id=run_id,
        topic=topic,
        platform=platform,
        review_report=review_report,
        human_feedback=human_feedback,
    )
    knowledge_entities = persist_knowledge_entities(config.knowledge_entities_dir, raw_knowledge_entities)
    new_rules = _rules_from_knowledge_entities(knowledge_entities)
    new_claims = _infer_new_claims(payload, context_pack)

    record = {
        "contract_name": "memory_ingest_record",
        "contract_version": "v1",
        "run_id": run_id,
        "topic": topic,
        "session_summary": session_summary or f"本次围绕“{topic}”完成了一轮写作与审稿。",
        "article_summary": article_summary,
        "human_feedback": human_feedback,
        "review_takeaways": review_takeaways,
        "knowledge_entities": knowledge_entities,
        "new_claims": new_claims,
        "new_rules": new_rules,
        "hotspot_candidates": [str(item) for item in payload.get("hotspot_candidates") or []],
        "archive_refs": [],
    }

    article_path = _write_article(config.articles_dir, run_id, topic, platform, final_article_markdown)
    record["archive_refs"].append(str(article_path.relative_to(config.data_dir)))

    _write_json(config.sessions_dir / f"{run_id}.json", {"payload": payload, "record": record})
    _write_json(config.feedback_history_dir / f"{run_id}.json", {"run_id": run_id, "topic": topic, "human_feedback": human_feedback})
    _write_json(config.review_history_dir / f"{run_id}.json", review_report or {"run_id": run_id, "status": "missing_review_report"})
    _write_json(config.sessions_dir / f"{run_id}.memory.json", record)
    return record


def build_daily_digest(payload: dict[str, Any], config: AppConfig) -> dict[str, Any]:
    ensure_runtime_dirs(config)
    date_label = str(payload.get("date") or datetime.now().strftime("%Y-%m-%d"))
    topic_limit = int(payload.get("topic_limit") or 5)

    session_files = sorted(config.sessions_dir.glob("*.memory.json"), key=lambda path: path.stat().st_mtime, reverse=True)
    recent_records = [_read_json(path) for path in session_files[:topic_limit]]
    digest = {
        "date": date_label,
        "recent_topics": [record.get("topic") for record in recent_records if record.get("topic")],
        "recent_takeaways": [
            takeaway
            for record in recent_records
            for takeaway in record.get("review_takeaways", [])[:2]
        ][:10],
        "recent_feedback": [
            item
            for record in recent_records
            for item in (record.get("human_feedback", {}).get("criticized_points") or [])[:1]
        ][:5],
        "recent_knowledge": [
            entity.get("statement")
            for record in recent_records
            for entity in record.get("knowledge_entities", [])[:2]
            if entity.get("statement")
        ][:8],
        "author_focus": [
            f"最近持续在写：{record.get('topic')}"
            for record in recent_records[:3]
            if record.get("topic")
        ],
    }
    _write_json(config.daily_digests_dir / f"{date_label}.json", digest)
    return digest


def _write_article(root: Path, run_id: str, topic: str, platform: str, body: str) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    filename = f"{run_id}-{slug_for_filename(topic, 'topic')}-{platform}.md"
    path = root / filename
    path.write_text(body + ("\n" if body and not body.endswith("\n") else ""), encoding="utf-8")
    return path


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _article_summary(markdown: str) -> str:
    for block in markdown.split("\n\n"):
        candidate = block.strip()
        if candidate:
            return candidate[:220]
    return ""


def _derive_knowledge_entities(
    *,
    run_id: str,
    topic: str,
    platform: str,
    review_report: dict[str, Any],
    human_feedback: dict[str, Any],
) -> list[dict[str, Any]]:
    entities: list[dict[str, Any]] = []
    topic_keywords = _topic_keywords(topic)
    base_scope = {
        "platform": platform,
        "topic_keywords": topic_keywords,
    }
    for issue in review_report.get("top_issues") or []:
        issue_type = str(issue.get("issue_type") or "")
        if issue_type == "late_thesis":
            entities.append(
                _knowledge_entity(
                    run_id=run_id,
                    entity_type="preference_rule",
                    statement="观点型文章应尽快在开头给出核心判断，而不是先铺背景。",
                    applies_to={**base_scope, "article_stage": "opening"},
                    source="review_feedback",
                    confidence=0.72,
                )
            )
        elif issue_type == "weak_support":
            entities.append(
                _knowledge_entity(
                    run_id=run_id,
                    entity_type="preference_rule",
                    statement="核心判断后需要尽快补充具体场景、案例或论据支撑。",
                    applies_to={**base_scope, "article_stage": "body"},
                    source="review_feedback",
                    confidence=0.74,
                )
            )
        elif issue_type == "filler_heavy":
            entities.append(
                _knowledge_entity(
                    run_id=run_id,
                    entity_type="anti_pattern",
                    statement="模板化提示语过多会明显拉低文章的信息密度和真实感。",
                    applies_to={**base_scope, "article_stage": "global"},
                    source="review_feedback",
                    confidence=0.7,
                )
            )
    for item in human_feedback.get("approved_points") or []:
        text = str(item).strip()
        if not text:
            continue
        entities.append(
            _knowledge_entity(
                run_id=run_id,
                entity_type="success_pattern",
                statement=_normalize_approved_feedback(text),
                applies_to={**base_scope, "article_stage": _infer_stage(text)},
                source="human_feedback",
                confidence=0.82,
            )
        )
    for item in human_feedback.get("criticized_points") or []:
        text = str(item).strip()
        if not text:
            continue
        entities.append(
            _knowledge_entity(
                run_id=run_id,
                entity_type="anti_pattern",
                statement=_normalize_criticized_feedback(text),
                applies_to={**base_scope, "article_stage": _infer_stage(text)},
                source="human_feedback",
                confidence=0.84,
            )
        )
    for item in human_feedback.get("keep_doing") or []:
        text = str(item).strip()
        if not text:
            continue
        entities.append(
            _knowledge_entity(
                run_id=run_id,
                entity_type="preference_rule",
                statement=_normalize_keep_doing(text),
                applies_to={**base_scope, "article_stage": _infer_stage(text)},
                source="human_feedback",
                confidence=0.8,
            )
        )
    for item in human_feedback.get("avoid_next_time") or []:
        text = str(item).strip()
        if not text:
            continue
        entities.append(
            _knowledge_entity(
                run_id=run_id,
                entity_type="anti_pattern",
                statement=_normalize_avoid_next_time(text),
                applies_to={**base_scope, "article_stage": _infer_stage(text)},
                source="human_feedback",
                confidence=0.8,
            )
        )
    deduped: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for entity in entities:
        key = (entity["entity_type"], entity["statement"])
        if key in seen:
            continue
        seen.add(key)
        deduped.append(entity)
    return deduped


def _infer_new_claims(payload: dict[str, Any], context_pack: dict[str, Any]) -> list[dict[str, str]]:
    provided = payload.get("new_claims")
    if provided:
        return [
            {
                "title": str(item.get("title") or "").strip(),
                "summary": str(item.get("summary") or "").strip(),
                "status": str(item.get("status") or "draft"),
            }
            for item in provided
            if str(item.get("title") or "").strip()
        ]
    claims = context_pack.get("core_claims") or []
    return [
        {
            "title": str(claim.get("title") or "").strip(),
            "summary": str(claim.get("summary") or "").strip(),
            "status": "active",
        }
        for claim in claims[:2]
        if str(claim.get("title") or "").strip()
    ]


def _feedback_takeaways(human_feedback: dict[str, Any]) -> list[str]:
    takeaways: list[str] = []
    for item in human_feedback.get("approved_points") or []:
        text = str(item).strip()
        if text:
            takeaways.append(f"作者认可：{text}")
    for item in human_feedback.get("criticized_points") or []:
        text = str(item).strip()
        if text:
            takeaways.append(f"作者批评：{text}")
    return takeaways


def _rules_from_knowledge_entities(entities: list[dict[str, Any]]) -> list[dict[str, str]]:
    rules: list[dict[str, str]] = []
    for entity in entities:
        entity_type = str(entity.get("entity_type") or "")
        if entity_type == "success_pattern":
            rule_type = "style"
        elif entity_type == "anti_pattern":
            rule_type = "review"
        else:
            rule_type = "style"
        rules.append(
            {
                "rule_type": rule_type,
                "rule_text": str(entity.get("statement") or ""),
                "source": str(entity.get("source") or "memory"),
            }
        )
    return rules


def _knowledge_entity(
    *,
    run_id: str,
    entity_type: str,
    statement: str,
    applies_to: dict[str, Any],
    source: str,
    confidence: float,
) -> dict[str, Any]:
    return {
        "entity_id": stable_entity_id(entity_type, statement),
        "entity_type": entity_type,
        "statement": statement,
        "applies_to": applies_to,
        "source_feedback_run_ids": [run_id],
        "confidence": round(confidence, 2),
        "support_count": 1,
        "source": source,
    }


def _topic_keywords(topic: str) -> list[str]:
    return [token.strip().lower() for token in topic.replace("与", " ").replace("-", " ").split() if token.strip()]


def _infer_stage(text: str) -> str:
    if "开头" in text or "首段" in text:
        return "opening"
    if "结尾" in text:
        return "ending"
    if "论据" in text or "例子" in text:
        return "body"
    return "global"


def _normalize_approved_feedback(text: str) -> str:
    if "开头" in text or "首段" in text:
        return "开头直接给出判断时，作者认可度更高。"
    return f"以下写法获得作者正向认可：{text}"


def _normalize_criticized_feedback(text: str) -> str:
    if "结尾" in text and ("空" in text or "虚" in text):
        return "结尾如果只有抽象升华而没有具体落点，作者通常不认可。"
    return f"以下表达模式容易引发作者负反馈：{text}"


def _normalize_keep_doing(text: str) -> str:
    if "首段" in text or "开头" in text or "判断" in text:
        return "首段应尽快给出核心判断。"
    return f"作者偏好以下稳定写法：{text}"


def _normalize_avoid_next_time(text: str) -> str:
    if "结尾" in text and ("总结句" in text or "空泛" in text or "升华" in text):
        return "避免使用只有态度没有落点的空泛结尾。"
    return f"应避免以下写法：{text}"
