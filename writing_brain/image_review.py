from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

from .image_supply import GENERATED_INDEX_FILENAME, PUBLIC_INDEX_FILENAME, build_image_supply_bundle
from .llm import call_gemini_native_chat, call_packy_chat, call_ppchat_chat, is_env_enabled


def build_image_review_report(payload: dict[str, Any]) -> dict[str, Any]:
    output_dir = Path(str(payload.get("output_dir") or "")).expanduser()
    if not output_dir:
        raise ValueError("output_dir 不能为空")

    platform = str(payload.get("platform") or "").strip().lower()
    image_dir = output_dir / "图片"
    placements_path = image_dir / "插图位置.json"
    tasks_path = image_dir / "生成任务.json"
    sources_path = image_dir / "图片与数据来源.md"
    public_leads_path = image_dir / "公开图片线索.md"
    generated_index_path = image_dir / GENERATED_INDEX_FILENAME
    public_index_path = image_dir / PUBLIC_INDEX_FILENAME

    supply_bundle = build_image_supply_bundle(output_dir)
    slots = list(supply_bundle.get("slots") or [])
    summary = dict(supply_bundle.get("summary") or {})
    expected_slots = int(summary.get("expected_image_slots") or 0)
    generated_expected = int(summary.get("generated_expected") or 0)
    public_expected = int(summary.get("public_expected") or 0)
    generated_present = int(summary.get("generated_present") or 0)
    public_present = int(summary.get("public_present") or 0)
    images_present = int(summary.get("images_present") or 0)

    file_issues: list[dict[str, str]] = []
    actions: list[str] = []
    score = 100

    if expected_slots == 0:
        file_issues.append(_issue("missing_placements", "high", "缺少插图位置定义", "图片结构未生成"))
        actions.append("先重跑 build-publish-pack，生成插图位置和图片任务。")
        score -= 40

    if expected_slots and images_present < expected_slots:
        file_issues.append(
            _issue(
                "missing_images",
                "high",
                "图片素材未补齐",
                f"期望 {expected_slots} 张，当前仅检测到 {images_present} 张",
            )
        )
        actions.append("补齐缺失图片后，再重跑 review-image-pack 或 build-publish-pack。")
        score -= min(45, (expected_slots - images_present) * 12)

    if public_expected and public_present == 0:
        file_issues.append(
            _issue(
                "missing_public_images",
                "medium",
                "缺少公共来源图片",
                "当前图片全部来自生成图，缺少至少 1 张真实公开来源图片",
            )
        )
        actions.append("至少补 1 张公开来源真实图片，避免整包只有生成图。")
        score -= 18

    missing_public_metadata = [item for item in slots if item["expected_source_type"] == "public" and item["file_present"] and not item["metadata_present"]]
    if missing_public_metadata:
        file_issues.append(
            _issue(
                "public_metadata_missing",
                "high",
                "公共来源图片缺少来源元数据",
                f"缺少来源登记：{_slot_names(missing_public_metadata)}",
            )
        )
        actions.append("为公共来源图片补齐 source_url、license、author 和 publishable 标记。")
        score -= min(30, len(missing_public_metadata) * 10)

    unpublishable_public = [
        item
        for item in slots
        if item["expected_source_type"] == "public"
        and item["file_present"]
        and (not item["publishable"] or not item["source_url"] or not item["license"])
    ]
    if unpublishable_public:
        file_issues.append(
            _issue(
                "public_publishability_blocked",
                "high",
                "公共来源图片未满足发布条件",
                f"缺少许可或来源链接：{_slot_names(unpublishable_public)}",
            )
        )
        actions.append("替换或补证公共来源图片，确保存在来源链接、许可信息且 publishable=true。")
        score -= min(35, len(unpublishable_public) * 12)

    generated_without_metadata = [
        item for item in slots if item["expected_source_type"] == "generated" and item["file_present"] and not item["metadata_present"]
    ]
    if generated_without_metadata:
        file_issues.append(
            _issue(
                "generated_metadata_missing",
                "medium",
                "生成图缺少生成记录",
                f"缺少模型/提示词登记：{_slot_names(generated_without_metadata)}",
            )
        )
        actions.append("优先通过 render-packy-images 生成配图，或补录生成图的 provider、model、prompt 信息。")
        score -= min(12, len(generated_without_metadata) * 4)

    if not sources_path.exists():
        file_issues.append(_issue("missing_sources_note", "medium", "缺少图片来源说明", "未找到 图片与数据来源.md"))
        actions.append("补齐图片与数据来源说明。")
        score -= 12

    if public_expected and not public_leads_path.exists():
        file_issues.append(_issue("missing_public_leads", "low", "缺少公开图片线索", "未找到 公开图片线索.md"))
        actions.append("补充公开图片线索，方便后续检索真实图片。")
        score -= 8

    pure_docx = _find_docx(output_dir, "可直接发布-纯文本可复制.docx")
    rich_docx = _find_docx(output_dir, "图文可发布.docx")
    if pure_docx is None:
        file_issues.append(_issue("missing_pure_docx", "high", "缺少纯文本 Word", "未找到可复制发布 DOCX"))
        actions.append("重跑 build-publish-pack，生成纯文本发布 Word。")
        score -= 20
    if rich_docx is None:
        file_issues.append(_issue("missing_rich_docx", "high", "缺少图文 Word", "未找到图文发布 DOCX"))
        actions.append("重跑 build-publish-pack，生成图文发布 Word。")
        score -= 20

    task_count = len(_read_json(tasks_path)) if tasks_path.exists() else 0
    decision = "pass" if score >= 85 and not any(item["severity"] == "high" for item in file_issues) else "revise"

    if decision == "pass":
        actions.insert(0, "图片包已通过审核，可直接使用图文 Word 交付或上传。")
    elif not actions:
        actions.append("图片包仍需人工复核。")

    result = {
        "contract_name": "image_review_report",
        "contract_version": "v1",
        "platform": platform,
        "output_dir": str(output_dir),
        "decision": decision,
        "total_score": max(score, 0),
        "expected_image_slots": expected_slots,
        "generated_expected": generated_expected,
        "public_expected": public_expected,
        "generated_present": generated_present,
        "public_present": public_present,
        "images_present": images_present,
        "image_task_count": task_count,
        "image_supply_bundle": supply_bundle,
        "top_issues": file_issues[:5] or [
            _issue("no_major_issue", "low", "图片包已满足交付条件", "图片、来源说明和 Word 文件已齐备")
        ],
        "required_actions": list(dict.fromkeys(actions)),
        "artifact_refs": [
            str(path)
            for path in [placements_path, tasks_path, sources_path, public_leads_path, generated_index_path, public_index_path, pure_docx, rich_docx]
            if path is not None and path.exists()
        ],
    }
    governance = _maybe_run_image_governance(
        payload,
        base_report=result,
        output_dir=output_dir,
        slots=slots,
        supply_bundle=supply_bundle,
    )
    if governance:
        result["governance"] = governance
        result = _merge_governance_into_image_report(result, governance=governance)
    return result


def _read_json(path: Path) -> Any:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _expected_slots(placements: dict[str, Any]) -> int:
    if not placements:
        return 0
    cover = 1 if placements.get("cover") else 0
    inline = len(list(placements.get("inline") or []))
    return cover + inline


def _expected_by_root(placements: dict[str, Any], root_name: str) -> int:
    paths: list[str] = []
    cover = placements.get("cover") or {}
    if cover.get("path"):
        paths.append(str(cover["path"]))
    for item in placements.get("inline") or []:
        if item.get("path"):
            paths.append(str(item["path"]))
    return sum(1 for path in paths if Path(path).parts and Path(path).parts[0] == root_name)


def _present_by_root(root: Path, expected_count: int) -> int:
    if not root.exists():
        return 0
    files = [path for path in root.iterdir() if path.is_file() and path.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"}]
    return min(len(files), expected_count)


def _find_docx(output_dir: Path, suffix: str) -> Path | None:
    for path in output_dir.glob(f"*{suffix}"):
        if path.is_file():
            return path
    return None


def _issue(issue_type: str, severity: str, summary: str, evidence: str) -> dict[str, str]:
    return {
        "issue_type": issue_type,
        "severity": severity,
        "summary": summary,
        "evidence": evidence,
    }


def _slot_names(items: list[dict[str, Any]]) -> str:
    return "、".join(str(item.get("filename") or "") for item in items[:5])


def _maybe_run_image_governance(
    payload: dict[str, Any],
    *,
    base_report: dict[str, Any],
    output_dir: Path,
    slots: list[dict[str, Any]],
    supply_bundle: dict[str, Any],
) -> dict[str, Any] | None:
    if not _should_use_image_governance(payload, base_report=base_report):
        return None

    article_markdown = str(payload.get("article_markdown") or payload.get("final_article_markdown") or "").strip()
    context_pack = dict(payload.get("context_pack") or {})
    prompt = _build_image_reviewer_prompt(
        payload=payload,
        base_report=base_report,
        output_dir=output_dir,
        slots=slots,
        article_markdown=article_markdown,
        context_pack=context_pack,
    )
    claude_vote = _run_claude_image_reviewer(prompt)
    gemini_vote = _run_gemini_image_reviewer(payload, prompt=prompt)
    baseline_flags = _build_image_procedural_flags(base_report, claude_vote, gemini_vote, None)
    procedural_review = _run_image_procedural_reviewer(
        payload,
        prompt=_build_image_procedural_prompt(
            payload=payload,
            base_report=base_report,
            claude_vote=claude_vote,
            gemini_vote=gemini_vote,
            supply_bundle=supply_bundle,
        ),
        baseline_flags=baseline_flags,
    )
    gpt_vote = _normalize_gpt_image_vote(procedural_review)
    flags = list(
        dict.fromkeys(
            [
                *_build_image_procedural_flags(base_report, claude_vote, gemini_vote, gpt_vote),
                *(procedural_review.get("flags") or []),
            ]
        )
    )
    votes = [claude_vote, gemini_vote, gpt_vote]
    valid_votes = [vote for vote in votes if vote.get("valid_vote")]
    pass_votes = [vote for vote in valid_votes if vote.get("decision") == "pass"]
    revise_votes = [vote for vote in valid_votes if vote.get("decision") == "revise"]
    rewrite_votes = [vote for vote in valid_votes if vote.get("decision") == "rewrite"]
    quorum_threshold = 2
    quorum_pass = len(pass_votes) >= quorum_threshold
    quorum_revise = len(revise_votes) >= quorum_threshold
    quorum_rewrite = len(rewrite_votes) >= quorum_threshold
    decision_ready = len(valid_votes) >= quorum_threshold
    consensus_decision = ""
    if quorum_pass:
        consensus_decision = "pass"
    elif quorum_rewrite:
        consensus_decision = "rewrite"
    elif quorum_revise:
        consensus_decision = "revise"
    score_gap = _max_vote_score_gap(valid_votes)
    average_score = round(sum(int(vote["score"]) for vote in valid_votes) / len(valid_votes), 1) if valid_votes else None
    requires_user_arbitration = bool(
        decision_ready
        and (
            len({str(vote.get("decision") or "") for vote in valid_votes}) > 1
            or bool(procedural_review.get("abnormal_review"))
            or "multi_party_score_gap_10_plus" in flags
        )
    )
    return {
        "contract_name": "image_review_governance",
        "contract_version": "v1",
        "mode": "three_powers",
        "user_authority": "final_veto",
        "policy": {
            "primary_reviewer": "claude",
            "secondary_reviewer": "gemini",
            "procedural_reviewer": "gpt5_4_ppchat",
            "codex_role": "chancellor_only",
        },
        "claude_review": claude_vote,
        "gemini_review": gemini_vote,
        "gpt_review": gpt_vote,
        "procedural_review": procedural_review,
        "procedural_flags": flags,
        "decision_ready": decision_ready,
        "quorum_threshold": quorum_threshold,
        "valid_vote_count": len(valid_votes),
        "pass_vote_count": len(pass_votes),
        "quorum_pass": quorum_pass,
        "consensus_decision": consensus_decision,
        "requires_user_arbitration": requires_user_arbitration,
        "panel_average_score": average_score,
        "score_gap": score_gap,
        "decision_options": _build_image_governance_options(claude_vote, gemini_vote, gpt_vote, quorum_pass=quorum_pass),
    }


def _merge_governance_into_image_report(
    report: dict[str, Any],
    *,
    governance: dict[str, Any],
) -> dict[str, Any]:
    next_report = dict(report)
    if not governance.get("decision_ready"):
        next_actions = [
            "图片三模型审批未完成，当前先按文件级审核结果处理；如要强制治理，请补齐 reviewer 配置后重跑。",
            *list(next_report.get("required_actions") or []),
        ]
        next_report["required_actions"] = list(dict.fromkeys(next_actions))
        return next_report

    if governance.get("quorum_pass") and not governance.get("requires_user_arbitration"):
        next_actions = [
            "图片三模型审批已通过，当前图片基调与文章主线基本一致。",
            *list(next_report.get("required_actions") or []),
        ]
        next_report["required_actions"] = list(dict.fromkeys(next_actions))
        return next_report

    issue = {
        "issue_type": "image_tone_mismatch",
        "severity": "high",
        "summary": "图片三模型审批未通过",
        "evidence": _image_governance_evidence(governance),
    }
    actions = [
        _image_governance_action(governance),
        *list(next_report.get("required_actions") or []),
    ]
    next_report["decision"] = "revise"
    next_report["total_score"] = min(int(next_report.get("total_score") or 0), int(governance.get("panel_average_score") or 70))
    next_report["top_issues"] = [issue, *list(next_report.get("top_issues") or [])][:5]
    next_report["required_actions"] = list(dict.fromkeys(actions))
    return next_report


def _should_use_image_governance(payload: dict[str, Any], *, base_report: dict[str, Any]) -> bool:
    explicit = payload.get("use_image_governance_review")
    if explicit is not None:
        return bool(explicit)
    if int(base_report.get("images_present") or 0) <= 0:
        return False
    return is_env_enabled("WRITING_BRAIN_USE_IMAGE_GOVERNANCE_REVIEW")


def _build_image_reviewer_prompt(
    *,
    payload: dict[str, Any],
    base_report: dict[str, Any],
    output_dir: Path,
    slots: list[dict[str, Any]],
    article_markdown: str,
    context_pack: dict[str, Any],
) -> str:
    title = str(payload.get("title") or context_pack.get("topic") or "").strip()
    tone_target = str(context_pack.get("tone_target") or payload.get("tone_target") or "锋利但克制，纪实，不要海报感").strip()
    article_excerpt = _article_excerpt(article_markdown)
    slot_lines = "\n".join(_format_slot_for_review(item) for item in slots[:6]) or "- 当前没有可评审图片槽位"
    top_issues = "\n".join(
        f"- {item.get('summary')}：{item.get('evidence')}"
        for item in list(base_report.get("top_issues") or [])[:4]
    ) or "- 当前文件级审核未发现显著问题"
    return (
        "请只评估图片方案，不要审文章本身。\n"
        f"文章标题：{title or '未提供'}\n"
        f"平台：{base_report.get('platform') or ''}\n"
        f"基调要求：{tone_target}\n"
        f"图片包目录：{output_dir}\n\n"
        "文章节选：\n"
        f"{article_excerpt}\n\n"
        "当前图片槽位与素材信息：\n"
        f"{slot_lines}\n\n"
        "文件级审核观察：\n"
        f"{top_issues}\n\n"
        "请重点判断：\n"
        "1. 图片是否贴合文章的核心判断，而不是泛泛配图。\n"
        "2. 生成图 prompt 是否过于海报化、概念化或 AI 味太重。\n"
        "3. 公开来源图是否跑题、过旧、人物/场景不对。\n"
        "4. 整体气质是否足够克制、真实、能支撑锋利文章，而不是削弱它。\n\n"
        "请只输出 JSON，字段为：score, decision, confidence, issue, action, strength。"
    )


def _build_image_procedural_prompt(
    *,
    payload: dict[str, Any],
    base_report: dict[str, Any],
    claude_vote: dict[str, Any],
    gemini_vote: dict[str, Any],
    supply_bundle: dict[str, Any],
) -> str:
    summary = dict(supply_bundle.get("summary") or {})
    return (
        "你是图片治理的程序性 reviewer，只判断评审流程是否可靠，并给出程序性结论。\n"
        f"平台：{base_report.get('platform') or ''}\n"
        f"图片槽位总数：{summary.get('expected_image_slots') or 0}\n"
        f"现有图片数：{summary.get('images_present') or 0}\n"
        f"Claude 票：score={claude_vote.get('score')}, decision={claude_vote.get('decision')}, valid={claude_vote.get('valid_vote')}, issue={claude_vote.get('issue')}\n"
        f"Gemini 票：score={gemini_vote.get('score')}, decision={gemini_vote.get('decision')}, valid={gemini_vote.get('valid_vote')}, issue={gemini_vote.get('issue')}\n"
        "请输出极短 JSON，字段：score, decision, abnormal_review, flags, reason, recommendation。"
    )


def _run_claude_image_reviewer(prompt: str) -> dict[str, Any]:
    result = call_ppchat_chat(
        prompt=prompt,
        system_prompt="你是独立图片 reviewer。你只根据文章基调和图片元数据判断是否适合发布，只输出 JSON。",
        default_model="claude-opus-4-6",
        model_env_vars=["PPCHAT_REVIEWER_MODEL", "WRITING_BRAIN_REVIEWER_MODEL", "OPENAI_MODEL", "ANTHROPIC_MODEL"],
        temperature=0.1,
        max_tokens=420,
    )
    return _normalize_image_vote("claude", result, score_key="semantic_score")


def _run_gemini_image_reviewer(payload: dict[str, Any], *, prompt: str) -> dict[str, Any]:
    route = _resolve_gemini_image_reviewer_route(payload)
    if route == "native":
        result = call_gemini_native_chat(
            prompt=prompt,
            system_prompt="你是独立图片 reviewer。你只根据文章基调和图片元数据判断是否适合发布，只输出合法 JSON。",
            default_model="gemini-3.1-pro-preview",
            model_env_vars=[
                "WRITING_BRAIN_GEMINI_REVIEWER_MODEL",
                "GEMINI_MODEL",
                "GOOGLE_MODEL",
                "PACKYAPI_REVIEWER_MODEL",
                "PACKYAPI_MODEL",
            ],
            temperature=0.1,
            max_tokens=420,
            response_json_schema=_image_vote_json_schema(),
        )
        return _normalize_image_vote("gemini", result, score_key="score")

    result = call_packy_chat(
        prompt=prompt,
        system_prompt="你是独立图片 reviewer。你只根据文章基调和图片元数据判断是否适合发布，只输出合法 JSON。",
        default_model="gemini-3.1-pro-preview",
        model_env_vars=[
            "WRITING_BRAIN_GEMINI_REVIEWER_MODEL",
            "PACKYAPI_REVIEWER_MODEL",
            "PACKYAPI_MODEL",
            "WRITING_BRAIN_WRITER_MODEL",
        ],
        temperature=0.1,
        max_tokens=420,
        response_format={
            "type": "json_schema",
            "json_schema": {
                "name": "image_review_vote",
                "strict": True,
                "schema": _image_vote_json_schema(),
            },
        },
        api_key_env_vars=_gemini_image_reviewer_api_key_env_vars(),
        base_url_env_vars=_gemini_image_reviewer_base_url_env_vars(),
        provider=_gemini_image_reviewer_provider_name(),
    )
    return _normalize_image_vote("gemini", result, score_key="score")


def _run_image_procedural_reviewer(
    payload: dict[str, Any],
    *,
    prompt: str,
    baseline_flags: list[str],
) -> dict[str, Any]:
    result = call_ppchat_chat(
        prompt=prompt,
        system_prompt="你是图片治理的程序性 reviewer。你只判断评审流程是否异常，只输出 JSON。",
        default_model="gpt-5.4",
        model_env_vars=[
            "PPCHAT_GPT_REVIEWER_MODEL",
            "WRITING_BRAIN_GPT_REVIEWER_MODEL",
            "PPCHAT_PROCEDURAL_MODEL",
            "WRITING_BRAIN_PROCEDURAL_MODEL",
        ],
        temperature=0.1,
        max_tokens=320,
    )
    if result.get("mode") != "model_output":
        return {
            "reviewer": "gpt5_4_procedural",
            "provider": str(result.get("provider") or "ppchat"),
            "model": str(result.get("model") or "gpt-5.4"),
            "valid_vote": False,
            "score": 0,
            "decision": "",
            "abnormal_review": True,
            "flags": list(baseline_flags),
            "reason": str(result.get("reply_text") or ""),
            "recommendation": "重跑图片程序性审查",
            "raw_output": str(result.get("reply_text") or ""),
        }
    parsed = _parse_structured_json(str(result.get("reply_text") or ""))
    if not parsed:
        return {
            "reviewer": "gpt5_4_procedural",
            "provider": str(result.get("provider") or "ppchat"),
            "model": str(result.get("model") or "gpt-5.4"),
            "valid_vote": False,
            "score": 0,
            "decision": "",
            "abnormal_review": True,
            "flags": list(dict.fromkeys([*baseline_flags, "invalid_gpt5_4_vote"])),
            "reason": "procedural reviewer 返回了非 JSON 或无法解析的结果",
            "recommendation": "重跑图片程序性审查",
            "raw_output": str(result.get("reply_text") or ""),
        }
    score = _coerce_int(parsed.get("score"))
    decision = _safe_decision(parsed.get("decision"))
    abnormal_review = bool(parsed.get("abnormal_review"))
    flags = list(dict.fromkeys([*baseline_flags, *_normalize_text_list(parsed.get("flags"), limit=8)]))
    valid_vote = bool(score and decision)
    if not valid_vote:
        flags = list(dict.fromkeys([*flags, "invalid_gpt5_4_vote"]))
        abnormal_review = True
    return {
        "reviewer": "gpt5_4_procedural",
        "provider": str(result.get("provider") or "ppchat"),
        "model": str(result.get("model") or "gpt-5.4"),
        "valid_vote": valid_vote,
        "score": score,
        "decision": decision,
        "abnormal_review": abnormal_review,
        "flags": flags,
        "reason": str(parsed.get("reason") or ""),
        "recommendation": str(parsed.get("recommendation") or ""),
        "raw_output": str(result.get("reply_text") or ""),
    }


def _normalize_gpt_image_vote(procedural_review: dict[str, Any]) -> dict[str, Any]:
    return {
        "reviewer": "gpt5_4",
        "provider": str(procedural_review.get("provider") or "ppchat"),
        "model": str(procedural_review.get("model") or "gpt-5.4"),
        "mode": "model_output" if procedural_review.get("valid_vote") else "prompt_only",
        "valid_vote": bool(procedural_review.get("valid_vote")),
        "score": int(procedural_review.get("score") or 0),
        "decision": str(procedural_review.get("decision") or ""),
        "confidence": 0.0,
        "issue": str(procedural_review.get("reason") or ""),
        "action": str(procedural_review.get("recommendation") or ""),
        "strength": "流程正常" if not procedural_review.get("abnormal_review") else "",
        "error": "" if procedural_review.get("valid_vote") else str(procedural_review.get("reason") or ""),
        "raw_output": str(procedural_review.get("raw_output") or ""),
    }


def _normalize_image_vote(reviewer: str, result: dict[str, Any], *, score_key: str) -> dict[str, Any]:
    raw_output = str(result.get("reply_text") or "")
    if result.get("mode") != "model_output":
        return {
            "reviewer": reviewer,
            "provider": str(result.get("provider") or ""),
            "model": str(result.get("model") or ""),
            "mode": str(result.get("mode") or "prompt_only"),
            "valid_vote": False,
            "score": 0,
            "decision": "",
            "confidence": 0.0,
            "issue": "",
            "action": "",
            "strength": "",
            "error": raw_output,
            "raw_output": raw_output,
        }
    parsed = result.get("parsed_response") if isinstance(result.get("parsed_response"), dict) else _parse_structured_json(raw_output)
    if not parsed:
        return {
            "reviewer": reviewer,
            "provider": str(result.get("provider") or ""),
            "model": str(result.get("model") or ""),
            "mode": "model_output",
            "valid_vote": False,
            "score": 0,
            "decision": "",
            "confidence": 0.0,
            "issue": "",
            "action": "",
            "strength": "",
            "error": "reviewer 输出无法解析",
            "raw_output": raw_output,
        }
    score = _coerce_int(parsed.get(score_key) if score_key in parsed else parsed.get("score"))
    decision = _safe_decision(parsed.get("decision"))
    confidence = _coerce_float(parsed.get("confidence"))
    vote = {
        "reviewer": reviewer,
        "provider": str(result.get("provider") or ""),
        "model": str(result.get("model") or ""),
        "mode": "model_output",
        "valid_vote": bool(score and decision),
        "score": score,
        "decision": decision,
        "confidence": confidence,
        "issue": str(parsed.get("issue") or ""),
        "action": str(parsed.get("action") or ""),
        "strength": str(parsed.get("strength") or ""),
        "error": "",
        "raw_output": raw_output,
    }
    if not vote["valid_vote"]:
        vote["error"] = "reviewer 返回了缺字段或非法字段的结果"
    return vote


def _parse_structured_json(raw_text: str) -> dict[str, Any] | None:
    text = str(raw_text or "").strip()
    if not text:
        return None
    fenced = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.S).strip()
    candidates = [fenced]
    if "{" in fenced and "}" in fenced:
        candidates.append(fenced[fenced.find("{") : fenced.rfind("}") + 1])
    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
        except Exception:
            continue
        if isinstance(parsed, dict):
            return parsed
    return _parse_key_value_contract(fenced)


def _parse_key_value_contract(text: str) -> dict[str, Any] | None:
    pairs: dict[str, Any] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or ":" not in line:
            continue
        key, value = line.split(":", 1)
        normalized_key = key.strip().lower()
        normalized_value = value.strip()
        if normalized_key in {"score", "semantic_score"}:
            pairs["score"] = normalized_value
        elif normalized_key == "decision":
            pairs["decision"] = normalized_value
        elif normalized_key == "confidence":
            pairs["confidence"] = normalized_value
        elif normalized_key == "issue":
            pairs["issue"] = normalized_value
        elif normalized_key == "action":
            pairs["action"] = normalized_value
        elif normalized_key == "strength":
            pairs["strength"] = normalized_value
        elif normalized_key == "abnormal_review":
            pairs["abnormal_review"] = normalized_value.lower() in {"true", "1", "yes"}
        elif normalized_key == "flags":
            pairs["flags"] = [item.strip() for item in re.split(r"[,\s]+", normalized_value) if item.strip()]
        elif normalized_key == "reason":
            pairs["reason"] = normalized_value
        elif normalized_key == "recommendation":
            pairs["recommendation"] = normalized_value
    return pairs or None


def _safe_decision(value: Any) -> str:
    normalized = str(value or "").strip().lower()
    return normalized if normalized in {"pass", "revise", "rewrite"} else ""


def _coerce_int(value: Any) -> int:
    try:
        parsed = int(float(value))
    except Exception:
        return 0
    return max(0, min(parsed, 100))


def _coerce_float(value: Any) -> float:
    try:
        parsed = float(value)
    except Exception:
        return 0.0
    return max(0.0, min(round(parsed, 2), 1.0))


def _article_excerpt(article_markdown: str) -> str:
    paragraphs = [item for item in re.split(r"\n\s*\n", article_markdown or "") if item.strip()]
    excerpt = []
    for item in paragraphs[:5]:
        excerpt.append(item.strip())
    return "\n\n".join(excerpt)[:1200] or "未提供文章正文。"


def _format_slot_for_review(slot: dict[str, Any]) -> str:
    metadata = dict(slot.get("metadata") or {})
    lines = [
        f"- {slot.get('filename')} | type={slot.get('expected_source_type')} | present={slot.get('file_present')}",
        f"  caption={slot.get('caption') or ''}",
    ]
    if slot.get("after_contains"):
        lines.append(f"  anchor={str(slot.get('after_contains') or '')[:120]}")
    if metadata.get("prompt"):
        lines.append(f"  prompt={metadata.get('prompt')}")
    if metadata.get("title"):
        lines.append(f"  source_title={metadata.get('title')}")
    if metadata.get("source_url"):
        lines.append(f"  source_url={metadata.get('source_url')}")
    if metadata.get("license"):
        lines.append(f"  license={metadata.get('license')}")
    if slot.get("provider") or slot.get("model"):
        lines.append(f"  provider={slot.get('provider') or ''}, model={slot.get('model') or ''}")
    return "\n".join(lines)


def _build_image_procedural_flags(
    base_report: dict[str, Any],
    claude_vote: dict[str, Any],
    gemini_vote: dict[str, Any],
    gpt_vote: dict[str, Any] | None,
) -> list[str]:
    flags: list[str] = []
    for reviewer, vote in [("claude", claude_vote), ("gemini", gemini_vote), ("gpt5_4", gpt_vote or {})]:
        if not vote.get("valid_vote"):
            flags.append(f"invalid_{reviewer}_vote")
    if int(base_report.get("images_present") or 0) < int(base_report.get("expected_image_slots") or 0):
        flags.append("image_assets_incomplete")
    valid_votes = [vote for vote in [claude_vote, gemini_vote, gpt_vote or {}] if vote.get("valid_vote")]
    if _max_vote_score_gap(valid_votes) >= 10:
        flags.append("multi_party_score_gap_10_plus")
    return list(dict.fromkeys(flags))


def _build_image_governance_options(
    claude_vote: dict[str, Any],
    gemini_vote: dict[str, Any],
    gpt_vote: dict[str, Any],
    *,
    quorum_pass: bool,
) -> list[str]:
    if not claude_vote.get("valid_vote"):
        return ["rerun_claude_image_review"]
    if not gemini_vote.get("valid_vote"):
        return ["rerun_gemini_image_review"]
    if not gpt_vote.get("valid_vote"):
        return ["rerun_image_procedural_review"]
    if quorum_pass:
        return ["user_review_image_pass_quorum"]
    return ["user_accept_image_reviewer_actions", "user_manual_image_arbitration_required"]


def _image_governance_evidence(governance: dict[str, Any]) -> str:
    votes = [
        governance.get("claude_review") or {},
        governance.get("gemini_review") or {},
        governance.get("gpt_review") or {},
    ]
    snippets = [
        f"{vote.get('reviewer') or 'reviewer'}:{vote.get('decision') or 'invalid'}:{vote.get('issue') or vote.get('action') or '无说明'}"
        for vote in votes
        if vote
    ]
    flags = list(governance.get("procedural_flags") or [])
    if flags:
        snippets.append("flags=" + ",".join(flags[:4]))
    return " | ".join(snippets[:4])


def _image_governance_action(governance: dict[str, Any]) -> str:
    if governance.get("requires_user_arbitration"):
        return "图片三模型审批结论分裂，请人工复核图片基调并决定保留、替换或重生。"
    consensus = str(governance.get("consensus_decision") or "")
    if consensus in {"revise", "rewrite"}:
        return "根据图片 reviewer 意见替换不贴题或过于海报化的素材，并重跑图片审批。"
    return "重跑图片三模型审批，确认图片与文章基调是否一致。"


def _max_vote_score_gap(votes: list[dict[str, Any]]) -> int:
    scores = [int(vote.get("score") or 0) for vote in votes if vote.get("valid_vote")]
    if len(scores) < 2:
        return 0
    return max(scores) - min(scores)


def _normalize_text_list(value: Any, *, limit: int) -> list[str]:
    if isinstance(value, list):
        items = [str(item).strip() for item in value if str(item).strip()]
    else:
        text = str(value or "").strip()
        items = [text] if text else []
    return items[:limit]


def _image_vote_json_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "score": {"type": "integer"},
            "decision": {"type": "string"},
            "confidence": {"type": "number"},
            "issue": {"type": "string"},
            "action": {"type": "string"},
            "strength": {"type": "string"},
        },
        "required": ["score", "decision", "issue", "action", "strength"],
    }


def _has_ppchat_credentials() -> bool:
    return any(
        str(os.environ.get(name) or "").strip()
        for name in ["PPCHAT_API_KEY", "OPENAI_API_KEY", "WRITING_BRAIN_REVISE_API_KEY"]
    )


def _has_gemini_image_reviewer_credentials() -> bool:
    route = _resolve_gemini_image_reviewer_route({})
    if route == "native":
        return any(str(os.environ.get(name) or "").strip() for name in ["GEMINI_API_KEY", "GOOGLE_API_KEY", "WRITING_BRAIN_GEMINI_API_KEY"])
    return any(str(os.environ.get(name) or "").strip() for name in _gemini_image_reviewer_api_key_env_vars())


def _resolve_gemini_image_reviewer_route(payload: dict[str, Any]) -> str:
    explicit = str(payload.get("gemini_image_reviewer_route") or "").strip().lower()
    if explicit in {"native", "packy"}:
        return explicit
    if is_env_enabled("WRITING_BRAIN_GEMINI_REVIEWER_NATIVE", "WRITING_BRAIN_GEMINI_IMAGE_REVIEWER_NATIVE"):
        return "native"
    return "packy"


def _gemini_image_reviewer_api_key_env_vars() -> list[str]:
    return [
        "WRITING_BRAIN_GEMINI_REVIEWER_API_KEY",
        "PACKYAPI_API_KEY",
        "WRITING_BRAIN_API_KEY",
    ]


def _gemini_image_reviewer_base_url_env_vars() -> list[str]:
    return [
        "WRITING_BRAIN_GEMINI_REVIEWER_BASE_URL",
        "PACKYAPI_BASE_URL",
        "WRITING_BRAIN_BASE_URL",
    ]


def _gemini_image_reviewer_provider_name() -> str:
    base_url = (
        str(os.environ.get("WRITING_BRAIN_GEMINI_REVIEWER_BASE_URL") or "").strip()
        or str(os.environ.get("PACKYAPI_BASE_URL") or "").strip()
        or str(os.environ.get("WRITING_BRAIN_BASE_URL") or "").strip()
    ).lower()
    if "ikuncode.cc" in base_url:
        return "ikun"
    return "packyapi"
