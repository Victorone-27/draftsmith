from __future__ import annotations

from typing import Any

from ..platforms import normalize_platform, normalize_platform_list
from ..prompt_assets import load_prompt_asset
from ..review import build_review_report
from ..text import compact_whitespace, count_evidence_signals, filler_count, repeated_paragraph_count, split_paragraphs


def build_assignment_contract(payload: dict[str, Any], *, context_pack: dict[str, Any], run_id: str) -> dict[str, Any]:
    topic = str(payload.get("topic") or context_pack.get("topic") or "Untitled topic").strip()
    user_goal = str(payload.get("user_goal") or context_pack.get("user_goal") or "Produce a directly deliverable article").strip()
    target_platforms = normalize_platform_list(payload.get("target_platforms") or context_pack.get("target_platforms"))
    platform = normalize_platform(payload.get("platform") or context_pack.get("platform") or (target_platforms[0] if target_platforms else "wechat"))
    if not target_platforms:
        target_platforms = [platform]
    elif platform not in target_platforms:
        target_platforms = [platform, *[item for item in target_platforms if item != platform]]
    tone_target = str(payload.get("tone_target") or context_pack.get("tone_target") or "Judgment-first, continuous argumentation, avoid template-speak").strip()
    title = str(payload.get("title") or topic).strip()
    return {
        "contract_name": "assignment_contract",
        "contract_version": "v2",
        "run_id": run_id,
        "topic": topic,
        "title": title,
        "platform": platform,
        "target_platforms": target_platforms,
        "audience": str(payload.get("audience") or context_pack.get("audience") or "").strip(),
        "user_goal": user_goal,
        "tone_target": tone_target,
        "constraints": [str(item) for item in payload.get("constraints") or context_pack.get("project_constraints") or [] if str(item).strip()],
        "must_cover_points": [str(item) for item in context_pack.get("must_cover_points") or [] if str(item).strip()],
        "evidence_needs": [str(item) for item in context_pack.get("evidence_needs") or [] if str(item).strip()],
        "style_rules": [str(item) for item in context_pack.get("style_rules") or [] if str(item).strip()],
        "platform_rules": [str(item) for item in context_pack.get("platform_rules") or [] if str(item).strip()],
        "prompt_assets": {
            "diagnose": "diagnose_article.md",
            "blueprint": "build_blueprint.md",
            "compose": "compose_article.md",
            "polish": "polish_voice.md",
        },
        "context_pack": context_pack,
    }


def build_research_pack(payload: dict[str, Any], *, context_pack: dict[str, Any], assignment: dict[str, Any]) -> dict[str, Any]:
    claim_items = []
    for item in context_pack.get("core_claims") or []:
        if not isinstance(item, dict):
            continue
        claim_items.append(
            {
                "title": str(item.get("title") or "").strip(),
                "summary": str(item.get("summary") or item.get("title") or "").strip(),
                "status": "verified",
                "source_type": "claim",
            }
        )
    evidence_items = [
        {
            "statement": need,
            "status": "needs_check",
            "source_type": "evidence_need",
        }
        for need in assignment.get("evidence_needs") or []
    ]
    memory_items = [
        {
            "statement": str(item.get("statement") or "").strip(),
            "entity_type": str(item.get("entity_type") or "memory").strip(),
            "status": "illustrative_only",
        }
        for item in context_pack.get("memory_knowledge") or []
        if isinstance(item, dict) and str(item.get("statement") or "").strip()
    ]
    return {
        "contract_name": "research_pack",
        "contract_version": "v2",
        "topic": assignment["topic"],
        "prompt_asset": load_prompt_asset("diagnose_article.md"),
        "claim_items": claim_items,
        "evidence_items": evidence_items,
        "memory_items": memory_items,
        "evidence_gap_count": len(evidence_items),
        "ready_for_blueprint": bool(claim_items or not evidence_items),
        "research_quality": "full" if bool(claim_items or not evidence_items) else "draft_quality",
    }


def build_article_diagnosis(payload: dict[str, Any], *, assignment: dict[str, Any], research_pack: dict[str, Any]) -> dict[str, Any]:
    from .quality_session import ARCHETYPES

    raw_text = " ".join(
        [
            str(assignment.get("topic") or ""),
            str(assignment.get("user_goal") or ""),
            str(payload.get("user_message") or ""),
        ]
    ).lower()
    scores = {
        "industry_analysis": sum(token in raw_text for token in ["为什么", "行业", "趋势", "危险", "判断", "平台", "格局"]),
        "operator_retrospective": sum(token in raw_text for token in ["复盘", "踩坑", "经历", "项目", "操盘", "案例"]),
        "method_breakdown": sum(token in raw_text for token in ["方法", "步骤", "怎么做", "清单", "流程", "指南"]),
    }
    primary = max(ARCHETYPES, key=lambda name: (scores[name], -ARCHETYPES.index(name)))
    secondary = sorted((name for name in ARCHETYPES if name != primary), key=lambda name: scores[name], reverse=True)[0]
    risks: list[str] = []
    if research_pack.get("evidence_gap_count"):
        risks.append("关键证据仍有缺口，最终稿可能只能达到可讨论水平，达不到可发表水平。")
    if not assignment.get("must_cover_points"):
        risks.append("当前缺少明确 must_cover_points，容易写成方向正确但不够锋利的稿。")
    return {
        "contract_name": "article_diagnosis",
        "contract_version": "v2",
        "primary_archetype": primary,
        "secondary_archetype": secondary,
        "prompt_asset": load_prompt_asset("diagnose_article.md"),
        "risks": risks,
        "ready_for_compose": not (
            research_pack.get("evidence_items")
            and not research_pack.get("claim_items")
        ),
        "recommendation": _diagnosis_recommendation(primary),
    }


def build_article_blueprint(
    payload: dict[str, Any],
    *,
    assignment: dict[str, Any],
    research_pack: dict[str, Any],
    diagnosis: dict[str, Any],
) -> dict[str, Any]:
    main_claim = _derive_main_claim(assignment, research_pack)
    evidence_plan = [item["statement"] for item in research_pack.get("evidence_items") or []][:3]
    if diagnosis["primary_archetype"] == "operator_retrospective":
        sections = [
            _section("How the problem surfaced", "Lead with the judgment, then describe the triggering scene and dissonance."),
            _section("Why it went wrong", "Break down key misjudgments, organizational constraints, and execution paths."),
            _section("What was truly learned", "Provide transferable lessons and boundaries."),
            _section("How to apply it next", "Translate lessons into actionable steps for the reader."),
        ]
    elif diagnosis["primary_archetype"] == "method_breakdown":
        sections = [
            _section("Lead with the method judgment", "Tell the reader what problem this method solves."),
            _section("Why the method works", "Explain the underlying mechanism, not just list steps."),
            _section("How to do it concretely", "Provide steps, actions, and verification methods."),
            _section("Where mistakes are most likely", "Provide boundaries, counterexamples, and failure signals."),
        ]
    else:
        sections = [
            _section("State the core judgment clearly", "Open with the opinion directly, do not set up background first."),
            _section("Why it holds now", "Explain timing, environmental changes, and causal chains."),
            _section("Where the real impact lands", "Analyze organizational, product, or industry consequences."),
            _section("How the reader should understand and act", "Provide boundaries, actions, and closure."),
        ]
    return {
        "contract_name": "article_blueprint",
        "contract_version": "v2",
        "prompt_asset": load_prompt_asset("build_blueprint.md"),
        "main_claim": main_claim,
        "compose_brief": compact_whitespace(
            f"Compose around '{main_claim}', maintain judgment-first continuous argumentation, key coverage: "
            + "; ".join(section["heading"] for section in sections)
        ),
        "must_cover_points": list(dict.fromkeys([*(assignment.get("must_cover_points") or []), main_claim])),
        "evidence_plan": evidence_plan,
        "sections": sections,
        "closing_goal": "Leave the reader with a clearer judgment and actionable next steps, not just a slogan.",
    }


def build_quality_evaluation(
    payload: dict[str, Any],
    *,
    assignment: dict[str, Any],
    research_pack: dict[str, Any],
    diagnosis: dict[str, Any],
    blueprint: dict[str, Any],
    article_markdown: str,
    article_source: str = "",
    context_pack: dict[str, Any],
) -> dict[str, Any]:
    review_report = build_review_report(
        {
            **payload,
            "run_id": assignment.get("run_id", ""),
            "draft_text": article_markdown,
            "context_pack": {
                **context_pack,
                "platform": assignment["platform"],
                "topic": assignment["topic"],
                "must_cover_points": blueprint["must_cover_points"],
            },
        }
    )
    paragraphs = split_paragraphs(article_markdown)
    evidence_hits = count_evidence_signals(article_markdown)
    filler_hits = filler_count(article_markdown)
    repeated = repeated_paragraph_count(article_markdown)
    template_hits = sum(article_markdown.count(token) for token in ["标题：", "导语：", "总结：", "首先", "其次", "最后"])
    blocked_dimensions: list[str] = []

    argument_gate = bool(review_report.get("expanded_checks", {}).get("argument", {}).get("ok"))
    if not argument_gate:
        blocked_dimensions.append("argument")
    voice_score = max(0, 100 - filler_hits * 18 - repeated * 20 - template_hits * 15)
    voice_gate = voice_score >= 70 and not article_markdown.strip().startswith("**标题")
    if not voice_gate:
        blocked_dimensions.append("voice")
    evidence_required = max(1, min(len(blueprint.get("must_cover_points") or []) or 1, len(research_pack.get("evidence_items") or []) or 1))
    evidence_gate = evidence_hits >= evidence_required
    if not evidence_gate:
        blocked_dimensions.append("evidence")
    platform_gate = bool(review_report.get("expanded_checks", {}).get("formatting", {}).get("ok"))
    if not platform_gate:
        blocked_dimensions.append("platform")
    editor_gate = bool(
        review_report.get("total_score", 0) >= 85
        and review_report.get("lazy_index", 10) <= 4
        and len(paragraphs) >= 4
        and diagnosis.get("ready_for_compose", False)
    )
    if not editor_gate:
        blocked_dimensions.append("editor")
    source_gate = article_source not in ("blueprint_fallback", "polished_fallback")
    if not source_gate:
        blocked_dimensions.append("source")

    can_continue = not blocked_dimensions
    decision = "pass" if can_continue else ("rewrite" if len(blocked_dimensions) >= 3 else "revise")
    repair_strategy = _repair_strategy(blocked_dimensions)
    return {
        "contract_name": "quality_evaluation",
        "contract_version": "v2",
        "decision": decision,
        "blocked_dimensions": blocked_dimensions,
        "repair_strategy": repair_strategy,
        "can_continue_to_images": can_continue,
        "can_continue_to_delivery": can_continue,
        "gates": {
            "argument": {"status": "pass" if argument_gate else "block", "prompt_asset": "review_argument.md"},
            "voice": {"status": "pass" if voice_gate else "block", "score": voice_score, "prompt_asset": "review_voice.md"},
            "evidence": {"status": "pass" if evidence_gate else "block", "signal_hits": evidence_hits, "prompt_asset": "review_evidence.md"},
            "platform": {"status": "pass" if platform_gate else "block"},
            "editor": {"status": "pass" if editor_gate else "block", "prompt_asset": "review_editor.md"},
            "source": {"status": "pass" if source_gate else "block", "article_source": article_source},
        },
        "legacy_review_report": review_report,
    }


def build_image_brief(
    *,
    assignment: dict[str, Any],
    diagnosis: dict[str, Any],
    blueprint: dict[str, Any],
    article_markdown: str,
) -> dict[str, Any]:
    paragraphs = [para for para in split_paragraphs(article_markdown) if not para.startswith("#")]
    anchors = paragraphs[:2] or [blueprint["main_claim"]]
    primary = diagnosis["primary_archetype"]
    slots = [
        {
            "slot_id": "img_cover",
            "filename": "封面",
            "role": "cover_opinion",
            "anchor": blueprint["main_claim"],
            "source_priority": ["generated", "public"],
            "allowed_source_types": ["generated", "public"],
            "disallowed_styles": ["poster-like", "cyberpunk", "robots", "obviously AI"],
        }
    ]
    second_role = "data_chart" if primary == "industry_analysis" else ("scene_photo" if primary == "operator_retrospective" else "product_screenshot")
    slots.append(
        {
            "slot_id": "img_support_1",
            "filename": "配图-01",
            "role": second_role,
            "anchor": anchors[0],
            "source_priority": ["public", "generated" if second_role == "data_chart" else "public"],
            "allowed_source_types": ["public"] if second_role in {"scene_photo", "product_screenshot"} else ["public", "generated"],
            "disallowed_styles": ["fake screenshots", "AI poster aesthetic", "over-decoration"],
        }
    )
    slots.append(
        {
            "slot_id": "img_support_2",
            "filename": "配图-02",
            "role": "concept_art" if primary != "operator_retrospective" else "scene_photo",
            "anchor": anchors[-1],
            "source_priority": ["public", "generated"],
            "allowed_source_types": ["public", "generated"] if primary != "operator_retrospective" else ["public"],
            "disallowed_styles": ["neon blue-purple", "3D rendering", "cheap sci-fi"],
        }
    )
    return {
        "contract_name": "image_brief",
        "contract_version": "v2",
        "prompt_asset": load_prompt_asset("build_image_brief.md"),
        "article_archetype": primary,
        "slots": slots,
    }


def _derive_main_claim(assignment: dict[str, Any], research_pack: dict[str, Any]) -> str:
    if assignment.get("must_cover_points"):
        return str(assignment["must_cover_points"][0])
    for item in research_pack.get("claim_items") or []:
        title = str(item.get("title") or "").strip()
        if title:
            return title
    topic = str(assignment.get("topic") or "").strip()
    if "不是" in topic and "而是" in topic:
        return topic
    return f"{topic}真正重要的，不是表面效率，而是背后的判断方式。"


def _diagnosis_recommendation(primary: str) -> str:
    mapping = {
        "industry_analysis": "Progress in the order: judgment → causes → impact → action. Do not write as a hot-take commentary.",
        "operator_retrospective": "Progress through scenes and decision chains. Do not write as an abstract lessons-learned summary.",
        "method_breakdown": "State applicability conditions first, then provide steps and failure boundaries.",
    }
    return mapping[primary]


def _section(heading: str, body_hint: str) -> dict[str, str]:
    return {
        "heading": heading,
        "body_hint": body_hint,
    }


def _repair_strategy(blocked_dimensions: list[str]) -> list[str]:
    messages: list[str] = []
    if "argument" in blocked_dimensions:
        messages.append("重写 blueprint 里的论证链，补齐判断与例子之间的推导。")
    if "voice" in blocked_dimensions:
        messages.append("单独执行 voice polish，去掉模板标签、空泛转折句和列表腔。")
    if "evidence" in blocked_dimensions:
        messages.append("先补 research_pack 里的关键证据，再继续成稿。")
    if "platform" in blocked_dimensions:
        messages.append("按平台规则重排版，不要把结构标签和演示文案带进正式正文。")
    if "editor" in blocked_dimensions:
        messages.append('以可发稿标准再过一轮，不要把「可读」误判成「可交付」。')
    if "source" in blocked_dimensions:
        messages.append("当前正文来自 fallback 骨架，需要重新走 compose 或提供人工母稿。")
    return messages or ["当前稿件需要人工复核。"]
