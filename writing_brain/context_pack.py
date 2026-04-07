from __future__ import annotations

from typing import Any

from .config import AppConfig
from .frontmatter import parse_markdown_file
from .knowledge import load_knowledge_entities
from .platforms import normalize_platform, normalize_platform_list
from .retrieval import load_markdown_items, load_published_articles, rank_items
from .text import extract_terms, now_run_id


DEFAULT_STYLE_RULES = [
    "Do not open with broad background context",
    "Do not repeat the same point twice",
    "Downgrade unsupported strong claims to observations or tendencies",
]

DEFAULT_PLATFORM_RULES = {
    "wechat": [
        "First screen must present the core judgment — no background preamble",
        "Do not end with sloganeering or vague calls to action",
        "Use natural paragraph flow, not numbered lists or subheadings",
        "Keep paragraphs under 4 sentences for mobile reading rhythm",
    ],
    "xiaohongshu": [
        "First sentence must hook — use a surprising claim or direct question",
        "Keep paragraphs to 1-2 sentences maximum",
        "Use conversational tone — write like talking to a friend, not lecturing",
        "Avoid academic structure — no 'firstly/secondly/finally'",
        "End with a question that invites comments",
    ],
    "zhihu": [
        "Open with a clear problem definition — what exactly is being answered",
        "Spell out boundary conditions and applicability limits explicitly",
        "Include method exposition with concrete steps, not just opinions",
        "Use evidence and counterexamples to build credibility",
        "Longer, more analytical paragraphs are acceptable",
    ],
    "toutiao": [
        "Open with the most newsworthy or contrarian claim",
        "State judgments in absolute terms — hedging loses readers",
        "Keep information density high — every paragraph must add new information",
        "Use short paragraphs and clear topic sentences",
    ],
    "csdn": [
        "Structure like a technical analysis — problem, approach, implementation, evaluation",
        "Include framework names, version numbers, and concrete technical references",
        "Add code-adjacent thinking — how would an engineer act on this",
        "Use markdown subheadings to organize sections clearly",
    ],
    "juejin": [
        "Write like a senior practitioner sharing hard-won lessons",
        "Include specific scenarios and decision points from real projects",
        "Balance opinion with practical takeaways the reader can use tomorrow",
        "Use a direct, peer-to-peer tone — not authoritative or academic",
    ],
    "renrendoushichanpinjingli": [
        "Frame everything through product thinking — user needs, trade-offs, metrics",
        "Include organizational context — team dynamics, stakeholder management",
        "Provide actionable frameworks or mental models, not just observations",
        "Use case studies or scenario analysis to illustrate points",
    ],
}


def build_context_pack(payload: dict[str, Any], config: AppConfig) -> dict[str, Any]:
    topic = str(payload.get("topic") or "").strip()
    target_platforms = normalize_platform_list(payload.get("target_platforms"))
    platform = normalize_platform(payload.get("platform") or (target_platforms[0] if target_platforms else "wechat"))
    if not target_platforms:
        target_platforms = [platform]
    user_goal = str(payload.get("user_goal") or "").strip()
    audience = str(payload.get("audience") or "").strip()
    tone_target = str(payload.get("tone_target") or "Sharp but restrained").strip()
    include_recent_articles = bool(payload.get("include_recent_articles", False))
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
    ranked_claims = _filter_ranked_items(rank_items(all_claims, query_terms, extra_text=topic), min_score=1.0, min_ratio=0.6)
    referenced_claims, missing_claim_refs = _resolve_project_claims(all_claims, project_claim_refs)
    claims = _merge_claim_items(referenced_claims, ranked_claims, limit=5)
    sources = rank_items(load_markdown_items(config.sources_dir, "id", "title"), query_terms, extra_text=user_goal)[:3]
    structures = rank_items(load_markdown_items(config.structures_dir, "id", "name"), query_terms, extra_text=platform)[:2]
    image_plans = rank_items(load_markdown_items(config.image_plans_dir, "id", "name"), query_terms, extra_text=platform)[:1]
    recent_articles: list[Any] = []
    if include_recent_articles:
        recent_articles = _filter_ranked_items(
            rank_items(load_published_articles(config.published_dir), query_terms, extra_text=topic),
            min_score=3.0,
            min_ratio=0.6,
        )[:3]
    memory_knowledge, feedback_notes = _load_recent_knowledge(config, topic=topic, platform=platform)
    author_preferences = _load_preferences(config)

    if not must_cover_points and referenced_claims:
        must_cover_points = [claim.title for claim in referenced_claims[:3]]

    memory_notes = []
    if claims:
        memory_notes.append(f"Found {len(claims)} highly relevant claims in the library — reuse selectively by relevance.")
    if referenced_claims:
        memory_notes.append(f"Project explicitly binds {len(referenced_claims)} claims — must prioritize these during writing.")
    if missing_claim_refs:
        memory_notes.append(f"{len(missing_claim_refs)} claim refs in the project did not match the library — manual check needed.")
    if structures:
        memory_notes.append(f"Matched {len(structures)} structure template(s).")
    if image_plans:
        memory_notes.append(f"Matched {len(image_plans)} image plan(s).")
    if recent_articles:
        memory_notes.append(f"Found {len(recent_articles)} published article(s) for reference.")
    if project_constraints:
        memory_notes.append(f"Project has {len(project_constraints)} constraint(s) or hard rules.")
    if evidence_needs:
        memory_notes.append(f"Project lists {len(evidence_needs)} external evidence need(s) to fill.")

    return {
        "contract_name": "context_pack",
        "contract_version": "v1",
        "run_id": run_id,
        "topic": topic,
        "platform": platform,
        "target_platforms": target_platforms,
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
                "why_relevant": f"Highly relevant to topic '{topic}' or goal '{user_goal}'",
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


def _filter_ranked_items(items: list[Any], *, min_score: float, min_ratio: float) -> list[Any]:
    if not items:
        return []
    top_score = max(float(getattr(item, "score", 0.0)) for item in items)
    threshold = max(min_score, round(top_score * min_ratio, 4))
    return [item for item in items if float(getattr(item, "score", 0.0)) >= threshold]


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
    fit_platforms = [normalize_platform(item, default="") for item in structure.meta.get("fit_platforms") or []]
    if platform in fit_platforms:
        return f"Template explicitly fits {platform}"
    return "Matches current topic and goal"


def _load_recent_knowledge(config: AppConfig, *, topic: str, platform: str) -> tuple[list[dict[str, Any]], list[str]]:
    if not config.knowledge_entities_dir.exists():
        return [], []
    entities: list[dict[str, Any]] = []
    notes: list[str] = []
    topic_tokens = {t.lower() for t in extract_terms(topic) if t.strip()}
    for entity in load_knowledge_entities(config.knowledge_entities_dir)[:20]:
        applies_to = entity.get("applies_to") or {}
        entity_platform = normalize_platform(applies_to.get("platform"), default="")
        topic_keywords = {str(item).lower() for item in applies_to.get("topic_keywords") or []}
        if entity_platform and entity_platform != platform:
            continue
        if topic_keywords and topic_tokens and not (topic_tokens & topic_keywords):
            continue
        entities.append(entity)
        if entity.get("entity_type") == "success_pattern":
            notes.append(f"Learned success pattern: {entity.get('statement')}")
        elif entity.get("entity_type") == "anti_pattern":
            notes.append(f"Learned anti-pattern: {entity.get('statement')}")
        elif entity.get("entity_type") == "preference_rule":
            notes.append(f"Learned preference rule: {entity.get('statement')}")
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
