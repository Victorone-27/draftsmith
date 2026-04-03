from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..config import AppConfig, ensure_runtime_dirs
from ..context_pack import build_context_pack
from ..memory import ingest_memory_record
from ..platforms import normalize_platform, normalize_platform_list
from ..post_review import run_post_review_pipeline
from ..prompt_assets import load_prompt_asset
from ..publish import build_publish_pack
from ..review import build_review_report
from ..revision import resolve_article_text, write_artifact, write_json
from ..text import compact_whitespace, count_evidence_signals, filler_count, now_run_id, repeated_paragraph_count, split_paragraphs
from ..usage import usage_scope
from ..writer import run_writer_turn


ARCHETYPES = ("industry_analysis", "operator_retrospective", "method_breakdown")

# Canonical pipeline step order. Behavioral contract tests assert this constant.
# Do NOT reorder or remove steps without updating tests/test_behavioral_contracts.py.
PIPELINE_STEPS = (
    "assignment", "research", "diagnosis", "blueprint",
    "compose", "quality_evaluation", "image_brief",
    "delivery", "post_review",
)


def run_quality_session(payload: dict[str, Any], config: AppConfig) -> dict[str, Any]:
    ensure_runtime_dirs(config)
    run_id = str(payload.get("run_id") or now_run_id("session"))
    with usage_scope(config.data_dir, run_id=run_id, caller_prefix="quality_session"):
        return _run_quality_session_body(payload, config, run_id=run_id)


def _run_quality_session_body(payload: dict[str, Any], config: AppConfig, *, run_id: str) -> dict[str, Any]:
    context_pack = dict(payload.get("context_pack") or {})
    if not context_pack:
        context_pack = build_context_pack({**payload, "run_id": run_id}, config)

    assignment = build_assignment_contract(payload, context_pack=context_pack, run_id=run_id)
    research_pack = build_research_pack(payload, context_pack=context_pack, assignment=assignment)
    diagnosis = build_article_diagnosis(payload, assignment=assignment, research_pack=research_pack)
    blueprint = build_article_blueprint(
        payload,
        assignment=assignment,
        research_pack=research_pack,
        diagnosis=diagnosis,
    )
    draft_turn, draft_text, draft_source = _compose_article(
        payload,
        config,
        run_id=run_id,
        assignment=assignment,
        context_pack=context_pack,
        blueprint=blueprint,
    )
    article_markdown = _polish_article_text(draft_text or "", assignment=assignment, blueprint=blueprint)
    article_source = draft_source if article_markdown == draft_text else f"polished_{draft_source}"
    quality_evaluation = build_quality_evaluation(
        payload,
        assignment=assignment,
        research_pack=research_pack,
        diagnosis=diagnosis,
        blueprint=blueprint,
        article_markdown=article_markdown,
        article_source=article_source,
        context_pack=context_pack,
    )
    image_brief = build_image_brief(
        assignment=assignment,
        diagnosis=diagnosis,
        blueprint=blueprint,
        article_markdown=article_markdown,
    )
    delivery_manifest = build_delivery(
        {
            **payload,
            "run_id": run_id,
            "assignment": assignment,
            "context_pack": context_pack,
            "article_markdown": article_markdown,
            "quality_evaluation": quality_evaluation,
            "image_brief": image_brief,
        },
        config,
    )
    post_review_result = run_post_review_pipeline(
        {
            **payload,
            "run_id": run_id,
            "platform": assignment["platform"],
            "target_platforms": list(assignment.get("target_platforms") or []),
            "topic": assignment["topic"],
            "title": assignment["title"],
        },
        config,
        run_id=run_id,
        final_article_markdown=article_markdown,
        final_review_report=quality_evaluation["legacy_review_report"],
        context_pack={
            **context_pack,
            "platform": assignment["platform"],
            "target_platforms": list(assignment.get("target_platforms") or []),
            "title": assignment["title"],
        },
    )

    artifact_refs = _persist_session_artifacts(
        config,
        run_id=run_id,
        assignment=assignment,
        research_pack=research_pack,
        diagnosis=diagnosis,
        blueprint=blueprint,
        draft_text=draft_text,
        article_markdown=article_markdown,
        quality_evaluation=quality_evaluation,
        image_brief=image_brief,
        delivery_manifest=delivery_manifest,
        post_review_result=post_review_result,
    )
    artifact_refs.extend(str(item) for item in delivery_manifest.get("artifact_refs") or [])
    if post_review_result is not None:
        artifact_refs.extend(str(item) for item in post_review_result.get("artifact_refs") or [])

    result = {
        "contract_name": "session_start_result",
        "contract_version": "v2",
        "run_id": run_id,
        "status": _session_status(
            article_markdown=article_markdown,
            quality_evaluation=quality_evaluation,
            delivery_manifest=delivery_manifest,
            post_review_result=post_review_result,
        ),
        "session_path": str(config.sessions_dir / f"{run_id}.session.json"),
        "assignment": assignment,
        "research_pack": research_pack,
        "article_diagnosis": diagnosis,
        "article_blueprint": blueprint,
        "draft_turn": draft_turn,
        "draft_text": draft_text,
        "draft_source": draft_source,
        "article_markdown": article_markdown,
        "article_source": article_source,
        "research_quality": research_pack.get("research_quality", "full"),
        "quality_evaluation": quality_evaluation,
        "final_decision": quality_evaluation["decision"],
        "final_review_report": quality_evaluation["legacy_review_report"],
        "image_brief": image_brief,
        "delivery_manifest": delivery_manifest,
        "post_review_result": post_review_result,
        "artifact_refs": list(dict.fromkeys(artifact_refs)),
        "recommended_next_actions": _recommended_next_actions(
            article_markdown=article_markdown,
            quality_evaluation=quality_evaluation,
            delivery_manifest=delivery_manifest,
            post_review_result=post_review_result,
        ),
    }
    write_json(config.sessions_dir / f"{run_id}.session.json", result)
    return result


def continue_session(payload: dict[str, Any], config: AppConfig) -> dict[str, Any]:
    prior = _load_session_result(payload, config)
    recompose = bool(payload.get("recompose"))
    manual_draft = payload.get("manual_draft_text")
    if manual_draft is None and not recompose:
        manual_draft = str(payload.get("article_markdown") or prior.get("article_markdown") or "")
    merged = {
        **dict(prior.get("assignment") or {}),
        **dict(prior.get("research_pack") or {}),
        **payload,
        "run_id": str(prior.get("run_id") or payload.get("run_id") or ""),
        "context_pack": dict(payload.get("context_pack") or prior.get("assignment", {}).get("context_pack") or prior.get("context_pack") or {}),
    }
    if manual_draft:
        merged["manual_draft_text"] = manual_draft
    elif "manual_draft_text" in merged:
        del merged["manual_draft_text"]
    return run_quality_session(merged, config)


def build_delivery(payload: dict[str, Any], config: AppConfig) -> dict[str, Any]:
    quality_evaluation = dict(payload.get("quality_evaluation") or {})
    article_markdown = str(payload.get("article_markdown") or "").strip()
    assignment = dict(payload.get("assignment") or {})
    image_brief = dict(payload.get("image_brief") or {})

    if not article_markdown:
        return {
            "contract_name": "delivery_manifest",
            "contract_version": "v2",
            "run_id": str(payload.get("run_id") or ""),
            "status": "blocked",
            "blocked_reasons": ["article_missing"],
            "text_delivery": {"status": "blocked", "artifacts": []},
            "rich_delivery": {"status": "blocked", "artifacts": []},
            "image_gate": {"status": "blocked", "blocked_reasons": ["article_missing"]},
            "artifact_refs": [],
            "recommended_next_actions": ["当前还没有正文，先完成 compose。"],
        }

    if not bool(quality_evaluation.get("can_continue_to_delivery")):
        blocked = list(quality_evaluation.get("blocked_dimensions") or ["quality_gate"])
        return {
            "contract_name": "delivery_manifest",
            "contract_version": "v2",
            "run_id": str(payload.get("run_id") or ""),
            "status": "blocked",
            "blocked_reasons": blocked,
            "text_delivery": {"status": "blocked", "artifacts": []},
            "rich_delivery": {"status": "blocked", "artifacts": []},
            "image_gate": {"status": "blocked", "blocked_reasons": blocked},
            "artifact_refs": [],
            "recommended_next_actions": [f"先解决质量闸门问题：{', '.join(blocked)}。"],
        }

    publish_result = build_publish_pack(
        {
            **payload,
            "article_markdown": article_markdown,
            "platform": str(assignment.get("platform") or payload.get("platform") or "wechat"),
            "topic": str(assignment.get("topic") or payload.get("topic") or ""),
            "title": str(assignment.get("title") or payload.get("title") or ""),
            "context_pack": dict(payload.get("context_pack") or {}),
            "image_brief": image_brief,
        },
        config,
    )
    image_review = dict(publish_result.get("image_review_report") or {})
    image_gate_status = "passed" if str(image_review.get("decision") or "").strip().lower() == "pass" else "blocked"
    artifact_refs = [str(item) for item in publish_result.get("artifact_refs") or []]
    pure_refs = [ref for ref in artifact_refs if ref.endswith("可直接发布-纯文本可复制.docx")]
    rich_refs = [ref for ref in artifact_refs if ref.endswith("图文可发布.docx")]
    return {
        "contract_name": "delivery_manifest",
        "contract_version": "v2",
        "run_id": str(payload.get("run_id") or ""),
        "status": "completed" if image_gate_status == "passed" else "partial",
        "blocked_reasons": [] if image_gate_status == "passed" else ["image_gate"],
        "publish_result": publish_result,
        "text_delivery": {
            "status": "passed" if pure_refs else "blocked",
            "artifacts": pure_refs,
        },
        "rich_delivery": {
            "status": "passed" if image_gate_status == "passed" and rich_refs else "blocked",
            "artifacts": rich_refs if image_gate_status == "passed" else [],
        },
        "image_gate": {
            "status": image_gate_status,
            "blocked_reasons": list(image_review.get("blocked_dimensions") or ([] if image_gate_status == "passed" else ["image_review"])),
            "report": image_review,
        },
        "artifact_refs": artifact_refs,
        "recommended_next_actions": list(dict.fromkeys(publish_result.get("recommended_next_actions") or [])),
    }


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


def maybe_accept_delivery(payload: dict[str, Any], config: AppConfig) -> dict[str, Any]:
    session_result = _load_session_result(payload, config)
    delivery_manifest = dict(session_result.get("delivery_manifest") or {})
    post_review_result = dict(session_result.get("post_review_result") or {})
    post_review_status = str(post_review_result.get("status") or "").strip().lower()
    if (
        session_result.get("status") in {"blocked", "exception"}
        or delivery_manifest.get("status") in {"blocked", "partial"}
        or post_review_status in {"failed", "partial"}
    ):
        return {
            "contract_name": "delivery_acceptance_result",
            "contract_version": "v2",
            "run_id": str(session_result.get("run_id") or ""),
            "status": "blocked",
            "session_result": session_result,
            "memory_record": None,
            "artifact_refs": list(session_result.get("artifact_refs") or []),
            "recommended_next_actions": list(
                session_result.get("recommended_next_actions")
                or post_review_result.get("recommended_next_actions")
                or ["先处理质量或交付阻塞项。"]
            ),
        }

    memory_record = ingest_memory_record(
        {
            "run_id": str(session_result.get("run_id") or ""),
            "topic": str(session_result.get("assignment", {}).get("topic") or ""),
            "platform": str(session_result.get("assignment", {}).get("platform") or "wechat"),
            "final_article_markdown": str(session_result.get("article_markdown") or ""),
            "session_summary": str(payload.get("session_summary") or "").strip(),
            "review_report": dict(session_result.get("final_review_report") or {}),
            "context_pack": dict(session_result.get("assignment", {}).get("context_pack") or {}),
            "human_feedback": dict(payload.get("human_feedback") or {}),
            "hotspot_candidates": [str(item) for item in payload.get("hotspot_candidates") or []],
        },
        config,
    )
    result = {
        "contract_name": "delivery_acceptance_result",
        "contract_version": "v2",
        "run_id": str(session_result.get("run_id") or ""),
        "status": "accepted",
        "session_result": session_result,
        "delivery_summary": {
            "outputs": _delivery_outputs(session_result, delivery_manifest=delivery_manifest, post_review_result=post_review_result),
        },
        "memory_record": memory_record,
        "artifact_refs": list(
            dict.fromkeys([*(session_result.get("artifact_refs") or []), *(memory_record.get("archive_refs") or [])])
        ),
        "recommended_next_actions": [
            "正文与交付记录已验收。",
            "本轮反馈已写回 memory，后续会作为 blueprint 和质量门输入。",
        ],
    }
    write_json(config.sessions_dir / f"{session_result.get('run_id')}.delivery.json", result)
    session_path = config.sessions_dir / f"{session_result.get('run_id')}.session.json"
    if session_path.exists():
        session_data = json.loads(session_path.read_text(encoding="utf-8"))
        session_data["status"] = "accepted"
        write_json(session_path, session_data)
    return result


def _compose_article(
    payload: dict[str, Any],
    config: AppConfig,
    *,
    run_id: str,
    assignment: dict[str, Any],
    context_pack: dict[str, Any],
    blueprint: dict[str, Any],
) -> tuple[dict[str, Any] | None, str, str]:
    manual_text = str(payload.get("manual_draft_text") or payload.get("draft_text") or "").strip()
    if manual_text:
        return None, manual_text, "manual_input"

    draft_turn = run_writer_turn(
        {
            **payload,
            "run_id": run_id,
            "task_mode": "draft",
            "context_pack": {
                **context_pack,
                "topic": assignment["topic"],
                "platform": assignment["platform"],
                "user_goal": assignment["user_goal"],
                "tone_target": assignment["tone_target"],
                "must_cover_points": blueprint["must_cover_points"],
                "project_constraints": [*(assignment.get("constraints") or []), "Follow the article_blueprint argument chain for composition"],
                "evidence_needs": blueprint["evidence_plan"],
            },
            "user_message": str(payload.get("draft_message") or blueprint["compose_brief"]).strip(),
        },
        config,
    )
    draft_text, draft_source = resolve_article_text(writer_turn=draft_turn, manual_text="")
    if draft_text:
        return draft_turn, draft_text, draft_source
    fallback = _compose_fallback_article(assignment=assignment, blueprint=blueprint)
    return draft_turn, fallback, "blueprint_fallback"


def _compose_fallback_article(*, assignment: dict[str, Any], blueprint: dict[str, Any]) -> str:
    paragraphs = [f"# {assignment['title']}", "", blueprint["main_claim"]]
    for index, section in enumerate(blueprint["sections"], start=1):
        paragraphs.append("")
        paragraphs.append(section["body_hint"])
        if index == 1:
            paragraphs.append(f"这件事在今天成立，不是因为一个新名词出现了，而是因为环境已经变了。")
        elif index == len(blueprint["sections"]):
            paragraphs.append("真正重要的，不是记住一句口号，而是知道下一步应该如何判断和行动。")
        else:
            paragraphs.append(f"如果把这一层看清，再回头看前面的判断，因果链就会更完整。")
    return "\n\n".join(paragraph for paragraph in paragraphs if paragraph is not None)


def _polish_article_text(text: str, *, assignment: dict[str, Any], blueprint: dict[str, Any]) -> str:
    if not text.strip():
        return ""
    polished = text.replace("标题：", "").replace("导语：", "").replace("正文：", "")
    paragraphs = split_paragraphs(polished)
    if paragraphs and not paragraphs[0].startswith("#"):
        paragraphs[0] = f"# {assignment['title']}"
    if len(paragraphs) >= 2 and blueprint["main_claim"] not in paragraphs[1]:
        paragraphs.insert(1, blueprint["main_claim"])
    return "\n\n".join(paragraphs).strip()


def _persist_session_artifacts(
    config: AppConfig,
    *,
    run_id: str,
    assignment: dict[str, Any],
    research_pack: dict[str, Any],
    diagnosis: dict[str, Any],
    blueprint: dict[str, Any],
    draft_text: str,
    article_markdown: str,
    quality_evaluation: dict[str, Any],
    image_brief: dict[str, Any],
    delivery_manifest: dict[str, Any],
    post_review_result: dict[str, Any] | None,
) -> list[str]:
    refs: list[str] = []
    for suffix, payload in [
        ("assignment", assignment),
        ("research", research_pack),
        ("diagnosis", diagnosis),
        ("blueprint", blueprint),
        ("quality", quality_evaluation),
        ("image-brief", image_brief),
        ("delivery", delivery_manifest),
    ]:
        path = config.sessions_dir / f"{run_id}.{suffix}.json"
        write_json(path, payload)
        refs.append(str(path))
    if post_review_result is not None:
        path = config.sessions_dir / f"{run_id}.post-review.json"
        write_json(path, post_review_result)
        refs.append(str(path))
    if draft_text:
        refs.append(str(config.sessions_dir / f"{run_id}.draft.md"))
        write_artifact(config.sessions_dir, run_id, "draft", draft_text, "md")
    if article_markdown:
        refs.append(str(config.sessions_dir / f"{run_id}.polished.md"))
        write_artifact(config.sessions_dir, run_id, "polished", article_markdown, "md")
    return refs


def _load_session_result(payload: dict[str, Any], config: AppConfig) -> dict[str, Any]:
    direct = dict(payload.get("session_result") or payload.get("cycle_result") or {})
    if direct:
        return direct
    session_path_raw = str(payload.get("session_path") or "").strip()
    if session_path_raw:
        return json.loads(Path(session_path_raw).expanduser().read_text(encoding="utf-8"))
    run_id = str(payload.get("run_id") or "").strip()
    if run_id:
        session_path = config.sessions_dir / f"{run_id}.session.json"
        if session_path.exists():
            return json.loads(session_path.read_text(encoding="utf-8"))
        cycle_path = config.sessions_dir / f"{run_id}.cycle.json"
        if cycle_path.exists():
            return json.loads(cycle_path.read_text(encoding="utf-8"))
    raise ValueError("run_id、session_path 或 session_result 至少要提供一个")


def _session_status(
    *,
    article_markdown: str,
    quality_evaluation: dict[str, Any],
    delivery_manifest: dict[str, Any],
    post_review_result: dict[str, Any] | None,
) -> str:
    if not article_markdown:
        return "blocked"
    if not quality_evaluation.get("can_continue_to_delivery"):
        return "exception"
    if delivery_manifest.get("status") in {"blocked", "partial"}:
        return "exception"
    if post_review_result and str(post_review_result.get("status") or "").strip().lower() in {"failed", "partial"}:
        return "exception"
    return "awaiting_acceptance"


def _recommended_next_actions(
    *,
    article_markdown: str,
    quality_evaluation: dict[str, Any],
    delivery_manifest: dict[str, Any],
    post_review_result: dict[str, Any] | None,
) -> list[str]:
    if not article_markdown:
        return ["当前还没有可交付正文，先补足 compose 输入或改用人工母稿继续推进。"]
    if not quality_evaluation.get("can_continue_to_delivery"):
        return list(dict.fromkeys(quality_evaluation.get("repair_strategy") or ["先修质量闸门问题。"]))
    if delivery_manifest.get("image_gate", {}).get("status") == "blocked":
        return list(dict.fromkeys(delivery_manifest.get("recommended_next_actions") or ["正文已过关，当前卡在图片闸门。"]))
    if post_review_result:
        status = str(post_review_result.get("status") or "").strip().lower()
        if status in {"failed", "partial"}:
            return list(dict.fromkeys(post_review_result.get("recommended_next_actions") or ["发布后处理流水线仍有阻塞项。"]))
        if post_review_result.get("recommended_next_actions"):
            return list(dict.fromkeys(post_review_result.get("recommended_next_actions") or []))
    return ["正文和纯文本交付已生成。若图文包通过审核，可直接进入最终验收。"]


def _delivery_outputs(
    session_result: dict[str, Any],
    *,
    delivery_manifest: dict[str, Any],
    post_review_result: dict[str, Any],
) -> list[dict[str, str]]:
    outputs: list[dict[str, str]] = []
    for stage in post_review_result.get("stages") or []:
        stage_name = str(stage.get("stage") or "").strip().lower()
        stage_result = dict(stage.get("result") or {})
        if stage_name == "release_cycle":
            for platform_result in stage_result.get("platform_results") or []:
                publish_result = dict(platform_result.get("publish_result") or {})
                artifact_refs = [str(item) for item in publish_result.get("artifact_refs") or []]
                image_review = dict(publish_result.get("image_review_report") or {})
                outputs.append(
                    {
                        "platform": str(platform_result.get("platform") or ""),
                        "text_status": "passed" if any(ref.endswith("可直接发布-纯文本可复制.docx") for ref in artifact_refs) else "blocked",
                        "rich_status": "passed"
                        if str(image_review.get("decision") or "").strip().lower() == "pass"
                        and any(ref.endswith("图文可发布.docx") for ref in artifact_refs)
                        else "blocked",
                    }
                )
        elif stage_name == "publish_pack":
            artifact_refs = [str(item) for item in stage_result.get("artifact_refs") or []]
            image_review = dict(stage_result.get("image_review_report") or {})
            outputs.append(
                {
                    "platform": normalize_platform(stage_result.get("platform") or session_result.get("assignment", {}).get("platform") or "", default=""),
                    "text_status": "passed" if any(ref.endswith("可直接发布-纯文本可复制.docx") for ref in artifact_refs) else "blocked",
                    "rich_status": "passed"
                    if str(image_review.get("decision") or "").strip().lower() == "pass"
                    and any(ref.endswith("图文可发布.docx") for ref in artifact_refs)
                    else "blocked",
                }
            )
    if outputs:
        return outputs
    return [
        {
            "platform": str(session_result.get("assignment", {}).get("platform") or ""),
            "text_status": str(delivery_manifest.get("text_delivery", {}).get("status") or ""),
            "rich_status": str(delivery_manifest.get("rich_delivery", {}).get("status") or ""),
        }
    ]


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
        messages.append('\u4ee5\u53ef\u53d1\u7a3f\u6807\u51c6\u518d\u8fc7\u4e00\u8f6e\uff0c\u4e0d\u8981\u628a\u300c\u53ef\u8bfb\u300d\u8bef\u5224\u6210\u300c\u53ef\u4ea4\u4ed8\u300d\u3002')
    if "source" in blocked_dimensions:
        messages.append("当前正文来自 fallback 骨架，需要重新走 compose 或提供人工母稿。")
    return messages or ["当前稿件需要人工复核。"]
