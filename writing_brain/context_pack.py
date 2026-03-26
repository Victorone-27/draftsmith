from __future__ import annotations

from typing import Any

from .config import AppConfig
from .frontmatter import parse_markdown_file
from .knowledge import load_knowledge_entities
from .retrieval import load_markdown_items, load_published_articles, rank_items
from .text import extract_terms, now_run_id


DEFAULT_STYLE_RULES = [
    "开头不要先铺大背景",
    "同一个意思不要重复说两次",
    "没证据的强判断要降级成观察或倾向",
]

DEFAULT_PLATFORM_RULES = {
    "wechat": [
        "公众号首屏要尽快给出判断",
        "结尾不要口号化",
    ],
    "xiaohongshu": [
        "开头要更直接，段落更短",
        "不要写成长篇论文式推进",
    ],
    "zhihu": [
        "要有更清楚的问题定义和方法展开",
        "边界条件要写清楚",
    ],
}


def build_context_pack(payload: dict[str, Any], config: AppConfig) -> dict[str, Any]:
    topic = str(payload.get("topic") or "").strip()
    platform = str(payload.get("platform") or "wechat").strip().lower()
    user_goal = str(payload.get("user_goal") or "").strip()
    audience = str(payload.get("audience") or "").strip()
    tone_target = str(payload.get("tone_target") or "锋利但克制").strip()
    run_id = str(payload.get("run_id") or now_run_id())
    project_claim_refs = _normalize_text_list(payload.get("project_claim_refs"))
    project_constraints = _normalize_text_list(payload.get("constraints"))
    evidence_needs = _normalize_text_list(payload.get("evidence_needs"))
    must_cover_points = [str(item).strip() for item in payload.get("must_cover_points") or [] if str(item).strip()]

    query_terms = extract_terms(
        topic,
        user_goal,
        audience,
        tone_target,
        *must_cover_points,
        *project_constraints,
        *evidence_needs,
        *[str(item) for item in payload.get("keywords") or []],
    )

    all_claims = load_markdown_items(config.claims_dir, "id", "title")
    ranked_claims = rank_items(all_claims, query_terms, extra_text=topic)
    referenced_claims, missing_claim_refs = _resolve_project_claims(all_claims, project_claim_refs)
    claims = _merge_claim_items(referenced_claims, ranked_claims, limit=5)
    sources = rank_items(load_markdown_items(config.sources_dir, "id", "title"), query_terms, extra_text=user_goal)[:3]
    structures = rank_items(load_markdown_items(config.structures_dir, "id", "name"), query_terms, extra_text=platform)[:2]
    image_plans = rank_items(load_markdown_items(config.image_plans_dir, "id", "name"), query_terms, extra_text=platform)[:1]
    recent_articles = rank_items(load_published_articles(config.published_dir), query_terms, extra_text=topic)[:3]
    memory_knowledge, feedback_notes = _load_recent_knowledge(config, topic=topic, platform=platform)
    author_preferences = _load_preferences(config)

    if not must_cover_points:
        must_cover_points = [claim.title for claim in claims[:3]]

    memory_notes = []
    if claims:
        memory_notes.append(f"资料库中已找到 {len(claims)} 条相关 claim，可优先复用。")
    if referenced_claims:
        memory_notes.append(f"项目已显式绑定 {len(referenced_claims)} 条 claim，写作时必须优先使用。")
    if missing_claim_refs:
        memory_notes.append(f"项目中有 {len(missing_claim_refs)} 条 claim ref 未命中资料库，需要人工检查。")
    if structures:
        memory_notes.append(f"已匹配到 {len(structures)} 个结构模板。")
    if image_plans:
        memory_notes.append(f"已匹配到 {len(image_plans)} 个配图策略。")
    if recent_articles:
        memory_notes.append(f"已找到 {len(recent_articles)} 篇已发布文章可参考。")
    if project_constraints:
        memory_notes.append(f"项目有 {len(project_constraints)} 条禁止项或硬约束。")
    if evidence_needs:
        memory_notes.append(f"项目列出 {len(evidence_needs)} 条待补外部论据。")

    return {
        "contract_name": "context_pack",
        "contract_version": "v1",
        "run_id": run_id,
        "topic": topic,
        "platform": platform,
        "audience": audience,
        "user_goal": user_goal,
        "tone_target": tone_target,
        "core_claims": [
            {
                "claim_id": claim.item_id,
                "title": claim.title,
                "summary": _claim_summary(claim.body),
                "evidence_strength": str(claim.meta.get("evidence_strength") or "unknown"),
                "usable_for": list(claim.meta.get("usable_for") or []),
            }
            for claim in claims
        ],
        "must_cover_points": must_cover_points,
        "project_claim_refs": project_claim_refs,
        "project_constraints": project_constraints,
        "evidence_needs": evidence_needs,
        "must_keep_facts": [
            {
                "fact_text": str(item.get("fact_text") or "").strip(),
                "source_ref": str(item.get("source_ref") or "").strip(),
                "is_hard_constraint": bool(item.get("is_hard_constraint", True)),
            }
            for item in payload.get("must_keep_facts") or []
            if str(item.get("fact_text") or "").strip()
        ],
        "source_refs": [
            {
                "source_id": source.item_id,
                "title": source.title,
                "source_url": str(source.meta.get("source_url") or ""),
                "why_relevant": f"与主题“{topic}”或目标“{user_goal}”有较高相关性",
            }
            for source in sources
        ],
        "preferred_structures": [
            {
                "structure_id": structure.item_id,
                "name": structure.title,
                "reason": _structure_reason(structure, platform),
            }
            for structure in structures
        ],
        "style_rules": list(dict.fromkeys([*DEFAULT_STYLE_RULES, *[str(item) for item in payload.get("style_rules") or []]])),
        "memory_knowledge": memory_knowledge,
        "platform_rules": list(
            dict.fromkeys(
                [
                    *DEFAULT_PLATFORM_RULES.get(platform, DEFAULT_PLATFORM_RULES["wechat"]),
                    *[str(item) for item in payload.get("platform_rules") or []],
                ]
            )
        ),
        "image_plan_hints": [_image_plan_hint(plan.body) for plan in image_plans if _image_plan_hint(plan.body)],
        "recent_articles": [
            {
                "article_id": article.item_id,
                "title": article.title,
                "date": str(article.meta.get("date") or ""),
                "summary": _claim_summary(article.body),
            }
            for article in recent_articles
        ],
        "author_preferences": author_preferences,
        "memory_notes": [*memory_notes, *feedback_notes],
    }


def _normalize_text_list(raw: Any) -> list[str]:
    return [str(item).strip() for item in raw or [] if str(item).strip()]


def _resolve_project_claims(all_claims: list[Any], project_claim_refs: list[str]) -> tuple[list[Any], list[str]]:
    if not project_claim_refs:
        return [], []

    indexed: dict[str, Any] = {}
    for claim in all_claims:
        keys = {
            claim.item_id.strip(),
            claim.title.strip(),
            str(claim.meta.get("id") or "").strip(),
        }
        for key in keys:
            normalized = key.lower()
            if normalized:
                indexed[normalized] = claim

    matched: list[Any] = []
    missing: list[str] = []
    seen_ids: set[str] = set()
    for ref in project_claim_refs:
        claim = indexed.get(ref.strip().lower())
        if claim is None:
            missing.append(ref)
            continue
        if claim.item_id in seen_ids:
            continue
        seen_ids.add(claim.item_id)
        matched.append(claim)
    return matched, missing


def _merge_claim_items(primary: list[Any], secondary: list[Any], *, limit: int) -> list[Any]:
    merged: list[Any] = []
    seen_ids: set[str] = set()
    for item in [*primary, *secondary]:
        item_id = str(item.item_id)
        if item_id in seen_ids:
            continue
        seen_ids.add(item_id)
        merged.append(item)
        if len(merged) >= limit:
            break
    return merged


def _claim_summary(body: str) -> str:
    for line in body.splitlines():
        candidate = line.strip()
        if candidate and not candidate.startswith("#") and not candidate.startswith("- "):
            return candidate[:180]
    return body.strip()[:180]


def _image_plan_hint(body: str) -> str:
    for line in body.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        candidate = stripped.lstrip("- ").strip()
        if candidate:
            return candidate[:120]
    return ""


def _structure_reason(structure: Any, platform: str) -> str:
    fit_platforms = [str(item).lower() for item in structure.meta.get("fit_platforms") or []]
    if platform in fit_platforms:
        return f"模板明确适配 {platform}"
    return "与当前主题和目标较匹配"


def _load_recent_knowledge(config: AppConfig, *, topic: str, platform: str) -> tuple[list[dict[str, Any]], list[str]]:
    if not config.knowledge_entities_dir.exists():
        return [], []
    entities: list[dict[str, Any]] = []
    notes: list[str] = []
    topic_tokens = {token.lower() for token in topic.split() if token.strip()}
    for entity in load_knowledge_entities(config.knowledge_entities_dir)[:20]:
        applies_to = entity.get("applies_to") or {}
        entity_platform = str(applies_to.get("platform") or "").lower()
        topic_keywords = {str(item).lower() for item in applies_to.get("topic_keywords") or []}
        if entity_platform and entity_platform != platform:
            continue
        if topic_keywords and topic_tokens and not (topic_tokens & topic_keywords):
            continue
        entities.append(entity)
        if entity.get("entity_type") == "success_pattern":
            notes.append(f"已学习成功模式：{entity.get('statement')}")
        elif entity.get("entity_type") == "anti_pattern":
            notes.append(f"已学习反模式：{entity.get('statement')}")
        elif entity.get("entity_type") == "preference_rule":
            notes.append(f"已学习偏好规则：{entity.get('statement')}")
    return entities[:8], list(dict.fromkeys(notes))[:8]


def _load_preferences(config: AppConfig) -> list[dict[str, Any]]:
    if not config.preferences_dir.exists():
        return []
    items: list[dict[str, Any]] = []
    for path in sorted(config.preferences_dir.glob("*.md")):
        meta, body = parse_markdown_file(path)
        items.append({
            "name": str(meta.get("name") or path.stem),
            "type": str(meta.get("type") or "general"),
            "content": body.strip()[:500],
            "meta": meta,
        })
    return items
