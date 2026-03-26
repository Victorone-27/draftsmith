from __future__ import annotations

import json
import os
import re
from typing import Any

from .llm import call_gemini_native_chat, call_packy_chat, call_ppchat_chat, is_env_enabled
from .text import (
    count_evidence_signals,
    extract_terms,
    filler_count,
    first_paragraph_contains,
    repeated_paragraph_count,
    split_paragraphs,
)


DEFAULT_REVIEWER_MODEL = "claude-opus-4-6"
DEFAULT_GEMINI_REVIEWER_MODEL = "gemini-3.1-pro-preview"
DEFAULT_PROCEDURAL_MODEL = "gpt-5.4"
DEFAULT_GEMINI_REVIEWER_ATTEMPTS = 2
DEFAULT_GEMINI_REVIEWER_MAX_TOKENS = 420
DEFAULT_PROCEDURAL_REVIEWER_MAX_TOKENS = 420


def build_review_report(payload: dict[str, Any]) -> dict[str, Any]:
    heuristic_report = _build_heuristic_review_report(payload)
    model_layer = _maybe_run_model_reviewer(payload, heuristic_report)

    if model_layer["mode"] != "model_output":
        heuristic_report["review_mode"] = "heuristic_only"
        heuristic_report["review_layers"] = {
            "heuristic": _heuristic_layer_summary(heuristic_report),
            "model": model_layer,
        }
        result = heuristic_report
    else:
        merged = _merge_review_reports(heuristic_report, model_layer)
        merged["review_mode"] = "hybrid"
        merged["review_layers"] = {
            "heuristic": _heuristic_layer_summary(heuristic_report),
            "model": model_layer,
        }
        result = merged

    if _should_use_governance_review(payload):
        claude_layer = model_layer
        if claude_layer["mode"] != "model_output":
            claude_layer = _maybe_run_model_reviewer({**payload, "use_model_reviewer": True}, heuristic_report)
        result["governance"] = _build_review_governance(
            payload,
            heuristic_report=heuristic_report,
            claude_layer=claude_layer,
        )
    return result


def build_article_review_vote(
    payload: dict[str, Any],
    *,
    reviewer: str,
    heuristic_report: dict[str, Any] | None = None,
) -> dict[str, Any]:
    heuristic = heuristic_report or _build_heuristic_review_report(payload)
    normalized_reviewer = reviewer.strip().lower()
    if normalized_reviewer == "claude":
        layer = _run_ppchat_article_reviewer_layer(
            payload,
            heuristic,
            default_model=DEFAULT_REVIEWER_MODEL,
            model_env_vars=[
                "PPCHAT_REVIEWER_MODEL",
                "WRITING_BRAIN_REVIEWER_MODEL",
                "OPENAI_MODEL",
                "ANTHROPIC_MODEL",
            ],
        )
        return _normalize_review_vote("claude", layer)
    if normalized_reviewer in {"gpt", "gpt5_4", "gpt-5.4"}:
        layer = _run_ppchat_article_reviewer_layer(
            payload,
            heuristic,
            default_model=DEFAULT_PROCEDURAL_MODEL,
            model_env_vars=[
                "PPCHAT_GPT_REVIEWER_MODEL",
                "WRITING_BRAIN_GPT_REVIEWER_MODEL",
                "PPCHAT_PROCEDURAL_MODEL",
                "WRITING_BRAIN_PROCEDURAL_MODEL",
            ],
        )
        return _normalize_review_vote("gpt5_4", layer)
    if normalized_reviewer == "gemini":
        layer = _maybe_run_gemini_reviewer({**payload, "use_independent_gemini_review": True}, heuristic)
        return _normalize_review_vote("gemini", layer)
    raise ValueError(f"unsupported reviewer: {reviewer}")


def _build_heuristic_review_report(payload: dict[str, Any]) -> dict[str, Any]:
    run_id = str(payload.get("run_id") or "")
    draft_text = str(payload.get("draft_text") or payload.get("draft_markdown") or "").strip()
    context_pack = dict(payload.get("context_pack") or {})
    platform = str(payload.get("platform") or context_pack.get("platform") or "wechat")
    topic = str(payload.get("topic") or context_pack.get("topic") or "").strip()

    must_cover = [str(item) for item in context_pack.get("must_cover_points") or [] if str(item).strip()]
    claim_titles = [str(item.get("title") or "") for item in context_pack.get("core_claims") or []]
    paragraphs = split_paragraphs(draft_text)
    opening_terms = list(
        dict.fromkeys(
            [
                *( [topic] if topic else [] ),
                *extract_terms(topic),
                *must_cover[:2],
                *claim_titles[:2],
            ]
        )
    )

    coverage_count = sum(1 for point in must_cover if _point_is_covered(draft_text, point))
    coverage_ratio = coverage_count / len(must_cover) if must_cover else 1.0
    filler_hits = filler_count(draft_text)
    repeated_count = repeated_paragraph_count(draft_text)
    evidence_hits = count_evidence_signals(draft_text)
    first_paragraph_has_claim = True if not opening_terms else first_paragraph_contains(draft_text, opening_terms)
    length = len(draft_text)

    alignment = _bounded_score(round(10 + coverage_ratio * 15 - max(0, len(must_cover) - coverage_count) * 2), 0, 25)
    completeness = _bounded_score(round(8 + min(len(paragraphs), 6) * 2 + coverage_ratio * 6 - repeated_count * 2), 0, 20)
    evidence = _bounded_score(round(4 + min(evidence_hits, 5) * 2 + coverage_ratio * 2), 0, 15)
    structure = _bounded_score(round(8 + min(len(paragraphs), 6) - repeated_count * 2 - (0 if first_paragraph_has_claim else 3)), 0, 15)
    style_fit = _bounded_score(round(12 - filler_hits * 2 - repeated_count), 0, 15)
    platform_fit = _platform_score(platform, first_paragraph_has_claim, len(paragraphs), length)

    total_score = alignment + completeness + evidence + structure + style_fit + platform_fit
    lazy_index = _bounded_score(
        round(
            (1 - coverage_ratio) * 4
            + filler_hits * 1.5
            + repeated_count * 2
            + (0 if evidence_hits >= 2 else 2)
            + (0 if length >= 500 else 1)
        ),
        0,
        10,
    )

    issues = _build_issues(
        must_cover=must_cover,
        coverage_count=coverage_count,
        paragraphs=paragraphs,
        first_paragraph_has_claim=first_paragraph_has_claim,
        evidence_hits=evidence_hits,
        filler_hits=filler_hits,
        repeated_count=repeated_count,
    )
    rewrite_actions = _rewrite_actions(issues)
    strengths = _strengths(total_score, coverage_ratio, evidence_hits, first_paragraph_has_claim)
    decision = _decision(total_score, lazy_index)

    return {
        "contract_name": "review_report",
        "contract_version": "v1",
        "review_id": str(payload.get("review_id") or f"review_{run_id or 'manual'}"),
        "run_id": run_id,
        "decision": decision,
        "total_score": total_score,
        "lazy_index": lazy_index,
        "score_breakdown": {
            "alignment": alignment,
            "completeness": completeness,
            "evidence": evidence,
            "structure": structure,
            "style_fit": style_fit,
            "platform_fit": platform_fit,
        },
        "top_issues": issues[:5] or [
            {
                "issue_type": "no_major_issue",
                "severity": "low",
                "summary": "未发现显著问题",
                "evidence": "当前稿件整体完成度较高",
            }
        ],
        "rewrite_actions": rewrite_actions,
        "strengths": strengths,
    }


def _maybe_run_model_reviewer(payload: dict[str, Any], heuristic_report: dict[str, Any]) -> dict[str, Any]:
    if not _should_use_model_reviewer(payload):
        return {
            "mode": "disabled",
            "provider": "ppchat",
            "model": _resolve_reviewer_model_name(),
            "semantic_score": None,
            "decision": None,
            "top_issues": [],
            "rewrite_actions": [],
            "strengths": [],
            "confidence": None,
            "error": "",
            "raw_output": "",
        }

    return _run_ppchat_article_reviewer_layer(
        payload,
        heuristic_report,
        default_model=DEFAULT_REVIEWER_MODEL,
        model_env_vars=["PPCHAT_REVIEWER_MODEL", "WRITING_BRAIN_REVIEWER_MODEL", "OPENAI_MODEL", "ANTHROPIC_MODEL"],
    )


def _run_ppchat_article_reviewer_layer(
    payload: dict[str, Any],
    heuristic_report: dict[str, Any],
    *,
    default_model: str,
    model_env_vars: list[str],
) -> dict[str, Any]:
    prompt = _build_model_reviewer_prompt(payload, heuristic_report)
    model_result = call_ppchat_chat(
        prompt=prompt,
        system_prompt="你是严格的中文文章 reviewer。你不会给面子分，只输出结构化 JSON 审稿结果。",
        default_model=default_model,
        model_env_vars=model_env_vars,
        temperature=0.1,
        max_tokens=1400,
    )
    if model_result["mode"] != "model_output":
        return {
            "mode": "prompt_only",
            "provider": str(model_result.get("provider") or "ppchat"),
            "model": str(model_result.get("model") or default_model),
            "semantic_score": None,
            "decision": None,
            "top_issues": [],
            "rewrite_actions": [],
            "strengths": [],
            "confidence": None,
            "error": str(model_result.get("reply_text") or ""),
            "raw_output": str(model_result.get("reply_text") or ""),
        }

    raw_output = str(model_result.get("reply_text") or "")
    parsed = _parse_model_review_json(raw_output)
    if not parsed:
        return {
            "mode": "prompt_only",
            "provider": str(model_result.get("provider") or "ppchat"),
            "model": str(model_result.get("model") or default_model),
            "semantic_score": None,
            "decision": None,
            "top_issues": [],
            "rewrite_actions": [],
            "strengths": [],
            "confidence": None,
            "error": "reviewer 模型返回了非 JSON 或无法解析的结果",
            "raw_output": raw_output,
        }

    return {
        "mode": "model_output",
        "provider": str(model_result.get("provider") or "ppchat"),
        "model": str(model_result.get("model") or default_model),
        "semantic_score": _extract_model_score(parsed),
        "decision": _safe_decision(parsed.get("decision")),
        "top_issues": _normalize_model_issues(_extract_model_issue_payload(parsed)),
        "rewrite_actions": _normalize_text_list(_extract_model_text_payload(parsed, "rewrite_actions", "action"), limit=5),
        "strengths": _normalize_text_list(_extract_model_text_payload(parsed, "strengths", "strength"), limit=4),
        "confidence": _bounded_float(float(parsed.get("confidence") or 0.0), 0.0, 1.0),
        "error": "",
        "raw_output": raw_output,
    }


def _build_review_governance(
    payload: dict[str, Any],
    *,
    heuristic_report: dict[str, Any],
    claude_layer: dict[str, Any],
) -> dict[str, Any]:
    gemini_layer = _maybe_run_gemini_reviewer(payload, heuristic_report)
    claude_vote = _normalize_review_vote("claude", claude_layer)
    gemini_vote = _normalize_review_vote("gemini", gemini_layer)
    baseline_flags = _build_procedural_flags(heuristic_report, claude_vote, gemini_vote, None)
    procedural_layer = _maybe_run_procedural_reviewer(
        payload,
        heuristic_report=heuristic_report,
        claude_vote=claude_vote,
        gemini_vote=gemini_vote,
        baseline_flags=baseline_flags,
    )
    procedural_review = _normalize_procedural_review(procedural_layer, baseline_flags=baseline_flags)
    gpt_vote = _normalize_gpt_review_vote(procedural_review)
    flags = _merge_texts(
        _build_procedural_flags(heuristic_report, claude_vote, gemini_vote, gpt_vote),
        procedural_review.get("flags") or [],
        limit=12,
    )
    votes = [claude_vote, gemini_vote, gpt_vote]
    valid_votes = [vote for vote in votes if vote.get("valid_vote")]
    pass_votes = [vote for vote in valid_votes if vote.get("decision") == "pass"]
    revise_votes = [vote for vote in valid_votes if vote.get("decision") == "revise"]
    rewrite_votes = [vote for vote in valid_votes if vote.get("decision") == "rewrite"]
    score_gap = _max_score_gap(valid_votes)
    average_score = round(sum(int(vote["score"]) for vote in valid_votes) / len(valid_votes), 1) if valid_votes else None
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

    decision_options = _build_governance_options(claude_vote, gemini_vote, gpt_vote, quorum_pass=quorum_pass)
    if not procedural_review["valid_vote"]:
        decision_options = ["rerun_procedural_review", *decision_options]
    return {
        "contract_name": "review_governance",
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
        "procedural_evidence": _heuristic_layer_summary(heuristic_report),
        "procedural_flags": flags,
        "decision_ready": decision_ready,
        "quorum_threshold": quorum_threshold,
        "valid_vote_count": len(valid_votes),
        "pass_vote_count": len(pass_votes),
        "quorum_pass": quorum_pass,
        "consensus_decision": consensus_decision,
        "requires_user_arbitration": bool(
            decision_ready
            and (
                len({str(vote.get("decision") or "") for vote in valid_votes}) > 1
                or bool(procedural_review.get("abnormal_review"))
                or "multi_party_score_gap_10_plus" in flags
                or "claude_decision_score_mismatch" in flags
                or "gemini_decision_score_mismatch" in flags
                or "gpt5_4_decision_score_mismatch" in flags
            )
        ),
        "panel_average_score": average_score,
        "score_gap": score_gap,
        "decision_options": list(dict.fromkeys(decision_options)),
    }


def _maybe_run_gemini_reviewer(payload: dict[str, Any], heuristic_report: dict[str, Any]) -> dict[str, Any]:
    if not _should_run_independent_gemini_review(payload):
        return {
            "mode": "disabled",
            "provider": "packyapi",
            "model": _resolve_gemini_reviewer_model_name(),
            "semantic_score": None,
            "decision": None,
            "top_issues": [],
            "rewrite_actions": [],
            "strengths": [],
            "confidence": None,
            "error": "",
            "raw_output": "",
        }

    route = _resolve_gemini_reviewer_route(payload)
    if route == "native":
        return _maybe_run_native_gemini_reviewer(payload, heuristic_report)
    return _maybe_run_packy_gemini_reviewer(payload, heuristic_report)


def _maybe_run_native_gemini_reviewer(payload: dict[str, Any], heuristic_report: dict[str, Any]) -> dict[str, Any]:
    prompt = _build_independent_gemini_structured_prompt(payload, heuristic_report)
    model_result = call_gemini_native_chat(
        prompt=prompt,
        system_prompt="你是独立中文文章 reviewer。你只做独立评分，不跟随 Claude，也不输出 schema 外字段。",
        default_model=DEFAULT_GEMINI_REVIEWER_MODEL,
        model_env_vars=[
            "WRITING_BRAIN_GEMINI_REVIEWER_MODEL",
            "GEMINI_MODEL",
            "GOOGLE_MODEL",
            "PACKYAPI_REVIEWER_MODEL",
            "PACKYAPI_MODEL",
            "WRITING_BRAIN_WRITER_MODEL",
        ],
        temperature=0.1,
        max_tokens=DEFAULT_GEMINI_REVIEWER_MAX_TOKENS,
        response_json_schema=_build_gemini_review_json_schema(),
    )
    if model_result["mode"] != "model_output":
        return {
            "mode": "prompt_only",
            "provider": str(model_result.get("provider") or "google_genai"),
            "model": str(model_result.get("model") or _resolve_gemini_reviewer_model_name()),
            "semantic_score": None,
            "decision": None,
            "top_issues": [],
            "rewrite_actions": [],
            "strengths": [],
            "confidence": None,
            "error": str(model_result.get("reply_text") or ""),
            "raw_output": str(model_result.get("reply_text") or ""),
        }

    raw_output = str(model_result.get("reply_text") or "")
    parsed = model_result.get("parsed_response")
    if not isinstance(parsed, dict):
        parsed = _parse_model_review_json(raw_output)
    if not isinstance(parsed, dict):
        return {
            "mode": "prompt_only",
            "provider": str(model_result.get("provider") or "google_genai"),
            "model": str(model_result.get("model") or _resolve_gemini_reviewer_model_name()),
            "semantic_score": None,
            "decision": None,
            "top_issues": [],
            "rewrite_actions": [],
            "strengths": [],
            "confidence": None,
            "error": "Gemini native 返回了非 JSON 或无法解析的结果",
            "raw_output": raw_output,
        }
    return _build_gemini_review_layer(
        provider=str(model_result.get("provider") or "google_genai"),
        model=str(model_result.get("model") or _resolve_gemini_reviewer_model_name()),
        parsed=parsed,
        raw_output=raw_output or json.dumps(parsed, ensure_ascii=False),
        confidence=0.8,
    )


def _maybe_run_packy_gemini_reviewer(payload: dict[str, Any], heuristic_report: dict[str, Any]) -> dict[str, Any]:
    schema_result = _maybe_run_packy_gemini_schema_reviewer(payload, heuristic_report)
    if schema_result["mode"] == "model_output":
        return schema_result

    last_error = ""
    last_raw_output = ""
    for _ in range(DEFAULT_GEMINI_REVIEWER_ATTEMPTS):
        prompt = _build_independent_gemini_plaintext_prompt(payload, heuristic_report)
        model_result = call_packy_chat(
            prompt=prompt,
            system_prompt="你是独立中文文章 reviewer。你只做独立评分，不跟随 Claude，也不输出解释文字。",
            default_model=DEFAULT_GEMINI_REVIEWER_MODEL,
            model_env_vars=[
                "PACKYAPI_REVIEWER_MODEL",
                "WRITING_BRAIN_GEMINI_REVIEWER_MODEL",
                "PACKYAPI_MODEL",
                "WRITING_BRAIN_WRITER_MODEL",
            ],
            temperature=0.1,
            max_tokens=DEFAULT_GEMINI_REVIEWER_MAX_TOKENS,
            api_key_env_vars=_gemini_reviewer_api_key_env_vars(),
            base_url_env_vars=_gemini_reviewer_base_url_env_vars(),
            provider=_gemini_reviewer_provider_name(),
        )
        if model_result["mode"] != "model_output":
            last_error = str(model_result.get("reply_text") or "")
            last_raw_output = last_error
            continue

        raw_output = str(model_result.get("reply_text") or "")
        parsed = _parse_gemini_plaintext_review(raw_output)
        if parsed is not None:
            return _build_gemini_review_layer(
                provider=str(model_result.get("provider") or "packyapi"),
                model=str(model_result.get("model") or _resolve_gemini_reviewer_model_name()),
                parsed=parsed,
                raw_output=raw_output,
                confidence=0.75,
            )
        last_error = "independent reviewer 返回了非合同文本或无法解析的结果"
        last_raw_output = raw_output

    return {
        "mode": "prompt_only",
        "provider": "packyapi",
        "model": _resolve_gemini_reviewer_model_name(),
        "semantic_score": None,
        "decision": None,
        "top_issues": [],
        "rewrite_actions": [],
        "strengths": [],
        "confidence": None,
        "error": " | ".join(item for item in [str(schema_result.get("error") or ""), last_error or "independent reviewer 调用失败"] if item),
        "raw_output": last_raw_output or str(schema_result.get("raw_output") or ""),
    }


def _maybe_run_packy_gemini_schema_reviewer(payload: dict[str, Any], heuristic_report: dict[str, Any]) -> dict[str, Any]:
    prompt = _build_independent_gemini_structured_prompt(payload, heuristic_report)
    model_result = call_packy_chat(
        prompt=prompt,
        system_prompt="你是独立中文文章 reviewer。你只做独立评分，不跟随 Claude，只输出合法 JSON。",
        default_model=DEFAULT_GEMINI_REVIEWER_MODEL,
        model_env_vars=[
            "PACKYAPI_REVIEWER_MODEL",
            "WRITING_BRAIN_GEMINI_REVIEWER_MODEL",
            "PACKYAPI_MODEL",
            "WRITING_BRAIN_WRITER_MODEL",
        ],
        temperature=0.0,
        max_tokens=DEFAULT_GEMINI_REVIEWER_MAX_TOKENS,
        api_key_env_vars=_gemini_reviewer_api_key_env_vars(),
        base_url_env_vars=_gemini_reviewer_base_url_env_vars(),
        provider=_gemini_reviewer_provider_name(),
        response_format={
            "type": "json_schema",
            "json_schema": {
                "name": "independent_review_vote",
                "strict": True,
                "schema": _build_gemini_review_json_schema(),
            },
        },
    )
    if model_result["mode"] != "model_output":
        return {
            "mode": "prompt_only",
            "provider": str(model_result.get("provider") or "packyapi"),
            "model": str(model_result.get("model") or _resolve_gemini_reviewer_model_name()),
            "semantic_score": None,
            "decision": None,
            "top_issues": [],
            "rewrite_actions": [],
            "strengths": [],
            "confidence": None,
            "error": str(model_result.get("reply_text") or ""),
            "raw_output": str(model_result.get("reply_text") or ""),
        }
    raw_output = str(model_result.get("reply_text") or "")
    parsed = _parse_model_review_json(raw_output)
    if not isinstance(parsed, dict):
        return {
            "mode": "prompt_only",
            "provider": str(model_result.get("provider") or "packyapi"),
            "model": str(model_result.get("model") or _resolve_gemini_reviewer_model_name()),
            "semantic_score": None,
            "decision": None,
            "top_issues": [],
            "rewrite_actions": [],
            "strengths": [],
            "confidence": None,
            "error": "independent reviewer schema 输出无法解析",
            "raw_output": raw_output,
        }
    result = _build_gemini_review_layer(
        provider=str(model_result.get("provider") or "packyapi"),
        model=str(model_result.get("model") or _resolve_gemini_reviewer_model_name()),
        parsed=parsed,
        raw_output=raw_output,
        confidence=0.78,
    )
    if result["mode"] != "model_output":
        result["error"] = result["error"] or "independent reviewer schema 输出缺字段"
    return result


def _maybe_run_procedural_reviewer(
    payload: dict[str, Any],
    *,
    heuristic_report: dict[str, Any],
    claude_vote: dict[str, Any],
    gemini_vote: dict[str, Any],
    baseline_flags: list[str],
) -> dict[str, Any]:
    if not _should_use_governance_review(payload):
        return {
            "mode": "disabled",
            "provider": "ppchat",
            "model": _resolve_procedural_model_name(),
            "semantic_score": None,
            "decision": None,
            "abnormal_review": None,
            "flags": [],
            "reason": "",
            "recommendation": "",
            "error": "",
            "raw_output": "",
        }

    prompt = _build_procedural_reviewer_prompt(
        payload,
        heuristic_report=heuristic_report,
        claude_vote=claude_vote,
        gemini_vote=gemini_vote,
        baseline_flags=baseline_flags,
    )
    model_result = call_ppchat_chat(
        prompt=prompt,
        system_prompt="你是议事记录官，只负责判断评审流程是否异常，不负责裁决文章质量。只输出 JSON。",
        default_model=DEFAULT_PROCEDURAL_MODEL,
        model_env_vars=["PPCHAT_PROCEDURAL_MODEL", "WRITING_BRAIN_PROCEDURAL_MODEL"],
        temperature=0.1,
        max_tokens=DEFAULT_PROCEDURAL_REVIEWER_MAX_TOKENS,
    )
    if model_result["mode"] != "model_output":
        return {
            "mode": "prompt_only",
            "provider": str(model_result.get("provider") or "ppchat"),
            "model": str(model_result.get("model") or _resolve_procedural_model_name()),
            "semantic_score": None,
            "decision": None,
            "abnormal_review": None,
            "flags": [],
            "reason": "",
            "recommendation": "",
            "error": str(model_result.get("reply_text") or ""),
            "raw_output": str(model_result.get("reply_text") or ""),
        }

    raw_output = str(model_result.get("reply_text") or "")
    parsed = _parse_model_review_json(raw_output)
    if not parsed:
        return _build_procedural_fallback_review(
            provider=str(model_result.get("provider") or "ppchat"),
            model=str(model_result.get("model") or _resolve_procedural_model_name()),
            raw_output=raw_output,
            heuristic_report=heuristic_report,
            claude_vote=claude_vote,
            gemini_vote=gemini_vote,
            baseline_flags=baseline_flags,
            error="procedural reviewer 返回了非 JSON 或无法解析的结果",
        )

    return {
        "mode": "model_output",
        "provider": str(model_result.get("provider") or "ppchat"),
        "model": str(model_result.get("model") or _resolve_procedural_model_name()),
        "semantic_score": _extract_model_score(parsed),
        "decision": _safe_decision(parsed.get("decision")),
        "abnormal_review": _extract_bool(parsed.get("abnormal_review")),
        "flags": _normalize_text_list(parsed.get("flags") or [], limit=6),
        "reason": str(parsed.get("reason") or "").strip(),
        "recommendation": str(parsed.get("recommendation") or "").strip(),
        "error": "",
        "raw_output": raw_output,
    }


def _merge_review_reports(heuristic_report: dict[str, Any], model_layer: dict[str, Any]) -> dict[str, Any]:
    heuristic_score = int(heuristic_report["total_score"])
    semantic_score = int(model_layer["semantic_score"] or heuristic_score)
    fused_total = _bounded_score(round(heuristic_score * 0.7 + semantic_score * 0.3), 0, 100)
    lazy_index = int(heuristic_report["lazy_index"])
    fused_decision = _strictest_decision(
        heuristic_report["decision"],
        str(model_layer.get("decision") or ""),
        _decision(fused_total, lazy_index),
    )

    merged = dict(heuristic_report)
    merged["total_score"] = fused_total
    merged["decision"] = fused_decision
    merged["top_issues"] = _merge_issues(heuristic_report.get("top_issues") or [], model_layer.get("top_issues") or [])
    merged["rewrite_actions"] = _merge_texts(
        heuristic_report.get("rewrite_actions") or [],
        model_layer.get("rewrite_actions") or [],
        limit=6,
    ) or ["当前稿件可直接进入人工复核"]
    merged["strengths"] = _merge_texts(
        heuristic_report.get("strengths") or [],
        model_layer.get("strengths") or [],
        limit=5,
    )
    return merged


def _build_model_reviewer_prompt(payload: dict[str, Any], heuristic_report: dict[str, Any]) -> str:
    draft_text = str(payload.get("draft_text") or payload.get("draft_markdown") or "").strip()
    context_pack = dict(payload.get("context_pack") or {})
    must_cover = "\n".join(f"- {item}" for item in context_pack.get("must_cover_points", [])[:6]) or "- 暂无"
    claims = "\n".join(f"- {item.get('title')}" for item in context_pack.get("core_claims", [])[:5] if item.get("title")) or "- 暂无"
    platform_rules = "\n".join(f"- {item}" for item in context_pack.get("platform_rules", [])[:6]) or "- 暂无"
    style_rules = "\n".join(f"- {item}" for item in context_pack.get("style_rules", [])[:6]) or "- 暂无"
    project_constraints = "\n".join(f"- {item}" for item in context_pack.get("project_constraints", [])[:6]) or "- 暂无"
    evidence_needs = "\n".join(f"- {item}" for item in context_pack.get("evidence_needs", [])[:6]) or "- 暂无"
    heuristic_issues = "\n".join(f"- {item.get('summary')}" for item in heuristic_report.get("top_issues", [])[:5]) or "- 暂无"

    return f"""请对下面这篇中文文章做严格审稿，并只返回 JSON，不要写解释文字，不要加 markdown 代码块。

你要重点判断：
1. 有没有跑题或偷懒。
2. 判断是否清楚、是否有论据支撑。
3. 有没有模板味、空话、重复表达。
4. 是否真的符合平台写法。

文章主题：{context_pack.get("topic", "")}
平台：{context_pack.get("platform", "")}
目标：{context_pack.get("user_goal", "")}
受众：{context_pack.get("audience", "")}

必须覆盖：
{must_cover}

核心判断参考：
{claims}

平台规则：
{platform_rules}

风格规则：
{style_rules}

项目硬约束：
{project_constraints}

待补论据：
{evidence_needs}

规则层初判问题：
{heuristic_issues}

待审稿件：
{draft_text or "无"}

请输出极短 JSON，尽量单行，不要超过 220 个字符，结构如下：
{{
  "semantic_score": 0-100 的整数,
  "decision": "pass" | "revise" | "rewrite",
  "confidence": 0-1 的小数,
  "issue": "最关键的一个问题，15字内",
  "action": "最关键的一个修改动作，18字内",
  "strength": "最关键的一个优点，15字内"
}}
"""


def _build_independent_gemini_plaintext_prompt(payload: dict[str, Any], heuristic_report: dict[str, Any]) -> str:
    draft_text = str(payload.get("draft_text") or payload.get("draft_markdown") or "").strip()
    context_pack = dict(payload.get("context_pack") or {})
    must_cover = "\n".join(f"- {item}" for item in context_pack.get("must_cover_points", [])[:6]) or "- 暂无"
    claims = "\n".join(f"- {item.get('title')}" for item in context_pack.get("core_claims", [])[:5] if item.get("title")) or "- 暂无"
    platform_rules = "\n".join(f"- {item}" for item in context_pack.get("platform_rules", [])[:6]) or "- 暂无"
    style_rules = "\n".join(f"- {item}" for item in context_pack.get("style_rules", [])[:6]) or "- 暂无"
    project_constraints = "\n".join(f"- {item}" for item in context_pack.get("project_constraints", [])[:6]) or "- 暂无"
    evidence_needs = "\n".join(f"- {item}" for item in context_pack.get("evidence_needs", [])[:6]) or "- 暂无"
    heuristic_issues = "\n".join(f"- {item.get('summary')}" for item in heuristic_report.get("top_issues", [])[:5]) or "- 暂无"

    return f"""请独立审查下面这篇中文文章，并只返回固定五行纯文本，不要 JSON，不要解释，不要说 Here，不要加标题。

你不是改稿人，只负责独立评分。
你需要判断：
1. 核心判断是否清楚。
2. 论据是否扎实。
3. 是否存在重复、空话、模板味。
4. 是否真的符合平台写法。

文章主题：{context_pack.get("topic", "")}
平台：{context_pack.get("platform", "")}
目标：{context_pack.get("user_goal", "")}
受众：{context_pack.get("audience", "")}

必须覆盖：
{must_cover}

核心判断参考：
{claims}

平台规则：
{platform_rules}

风格规则：
{style_rules}

项目硬约束：
{project_constraints}

待补论据：
{evidence_needs}

程序层提示问题：
{heuristic_issues}

待审稿件：
{draft_text or "无"}

只输出这五行，字段名必须完全一致：
SCORE: 0-100整数
DECISION: pass|revise|rewrite
ISSUE: 最关键的一个问题，15字内
ACTION: 最关键的一个修改动作，18字内
STRENGTH: 最关键的一个优点，15字内
"""


def _build_independent_gemini_structured_prompt(payload: dict[str, Any], heuristic_report: dict[str, Any]) -> str:
    draft_text = str(payload.get("draft_text") or payload.get("draft_markdown") or "").strip()
    context_pack = dict(payload.get("context_pack") or {})
    must_cover = "\n".join(f"- {item}" for item in context_pack.get("must_cover_points", [])[:6]) or "- 暂无"
    claims = "\n".join(f"- {item.get('title')}" for item in context_pack.get("core_claims", [])[:5] if item.get("title")) or "- 暂无"
    platform_rules = "\n".join(f"- {item}" for item in context_pack.get("platform_rules", [])[:6]) or "- 暂无"
    style_rules = "\n".join(f"- {item}" for item in context_pack.get("style_rules", [])[:6]) or "- 暂无"
    project_constraints = "\n".join(f"- {item}" for item in context_pack.get("project_constraints", [])[:6]) or "- 暂无"
    evidence_needs = "\n".join(f"- {item}" for item in context_pack.get("evidence_needs", [])[:6]) or "- 暂无"
    heuristic_issues = "\n".join(f"- {item.get('summary')}" for item in heuristic_report.get("top_issues", [])[:5]) or "- 暂无"

    return f"""请独立审查下面这篇中文文章。

你不是改稿人，只负责独立评分，不参考 Claude 立场。
你需要判断：
1. 核心判断是否清楚。
2. 论据是否扎实。
3. 是否存在重复、空话、模板味。
4. 是否真的符合平台写法。

文章主题：{context_pack.get("topic", "")}
平台：{context_pack.get("platform", "")}
目标：{context_pack.get("user_goal", "")}
受众：{context_pack.get("audience", "")}

必须覆盖：
{must_cover}

核心判断参考：
{claims}

平台规则：
{platform_rules}

风格规则：
{style_rules}

项目硬约束：
{project_constraints}

待补论据：
{evidence_needs}

程序层提示问题：
{heuristic_issues}

待审稿件：
{draft_text or "无"}

请按给定 schema 返回，不要输出 schema 外字段。"""


def _build_gemini_review_json_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["score", "decision", "issue", "action", "strength"],
        "properties": {
            "score": {
                "type": "integer",
                "minimum": 0,
                "maximum": 100,
                "description": "独立审稿总分",
            },
            "decision": {
                "type": "string",
                "enum": ["pass", "revise", "rewrite"],
                "description": "独立审稿结论",
            },
            "issue": {
                "type": "string",
                "description": "最关键的一个问题，尽量简短",
            },
            "action": {
                "type": "string",
                "description": "最关键的一个修改动作，尽量简短",
            },
            "strength": {
                "type": "string",
                "description": "最关键的一个优点，尽量简短",
            },
        },
    }


def _build_procedural_reviewer_prompt(
    payload: dict[str, Any],
    *,
    heuristic_report: dict[str, Any],
    claude_vote: dict[str, Any],
    gemini_vote: dict[str, Any],
    baseline_flags: list[str],
) -> str:
    context_pack = dict(payload.get("context_pack") or {})
    baseline_flags_text = "\n".join(f"- {item}" for item in baseline_flags) or "- 无"
    return f"""请只判断这次审稿流程本身是否异常，不要评价文章质量高低，不要替用户做最终裁决。

主题：{context_pack.get("topic", "")}
平台：{context_pack.get("platform", "")}

Claude 主审票：
- valid_vote: {claude_vote.get("valid_vote")}
- score: {claude_vote.get("score")}
- decision: {claude_vote.get("decision")}
- issue: {claude_vote.get("issue")}

Gemini 独立票：
- valid_vote: {gemini_vote.get("valid_vote")}
- score: {gemini_vote.get("score")}
- decision: {gemini_vote.get("decision")}
- issue: {gemini_vote.get("issue")}

启发式证据：
- total_score: {heuristic_report.get("total_score")}
- decision: {heuristic_report.get("decision")}
- top_issue: {((heuristic_report.get("top_issues") or [{}])[0].get("summary") or "")}

程序层基础 flags：
{baseline_flags_text}

只返回 JSON：
{{
  "score": 0-100 的整数,
  "decision": "pass" | "revise" | "rewrite",
  "abnormal_review": true 或 false,
  "flags": ["最多3个短 flag"],
  "reason": "一句话说明异常或不异常",
  "recommendation": "一句话建议下一步"
}}
"""


def _should_use_model_reviewer(payload: dict[str, Any]) -> bool:
    explicit = payload.get("use_model_reviewer")
    if explicit is not None:
        return bool(explicit)
    return is_env_enabled("WRITING_BRAIN_ENABLE_MODEL_REVIEWER", "PPCHAT_ENABLE_MODEL_REVIEWER")


def _should_use_governance_review(payload: dict[str, Any]) -> bool:
    explicit = payload.get("use_governance_review")
    if explicit is not None:
        return bool(explicit)
    if payload.get("use_model_reviewer") is not None:
        return bool(payload.get("use_model_reviewer"))
    return is_env_enabled("WRITING_BRAIN_ENABLE_GOVERNANCE_REVIEW")


def _should_run_independent_gemini_review(payload: dict[str, Any]) -> bool:
    explicit = payload.get("use_independent_gemini_review")
    if explicit is not None:
        return bool(explicit)
    return _should_use_governance_review(payload)


def _resolve_reviewer_model_name() -> str:
    for name in ["PPCHAT_REVIEWER_MODEL", "WRITING_BRAIN_REVIEWER_MODEL", "OPENAI_MODEL", "ANTHROPIC_MODEL"]:
        value = os.environ.get(name)
        if value and value.strip():
            return value.strip()
    return DEFAULT_REVIEWER_MODEL


def _resolve_gemini_reviewer_model_name() -> str:
    for name in ["PACKYAPI_REVIEWER_MODEL", "WRITING_BRAIN_GEMINI_REVIEWER_MODEL", "PACKYAPI_MODEL", "WRITING_BRAIN_WRITER_MODEL"]:
        value = os.environ.get(name)
        if value and value.strip():
            return value.strip()
    return DEFAULT_GEMINI_REVIEWER_MODEL


def _gemini_reviewer_api_key_env_vars() -> list[str]:
    return [
        "WRITING_BRAIN_GEMINI_REVIEWER_API_KEY",
        "IKUN_API_KEY",
        "PACKYAPI_REVIEWER_API_KEY",
        "PACKYAPI_API_KEY",
        "WRITING_BRAIN_API_KEY",
    ]


def _gemini_reviewer_base_url_env_vars() -> list[str]:
    return [
        "WRITING_BRAIN_GEMINI_REVIEWER_BASE_URL",
        "IKUN_BASE_URL",
        "PACKYAPI_REVIEWER_BASE_URL",
        "PACKYAPI_BASE_URL",
        "WRITING_BRAIN_BASE_URL",
    ]


def _gemini_reviewer_provider_name() -> str:
    base_urls = [
        os.environ.get("WRITING_BRAIN_GEMINI_REVIEWER_BASE_URL") or "",
        os.environ.get("IKUN_BASE_URL") or "",
        os.environ.get("PACKYAPI_REVIEWER_BASE_URL") or "",
        os.environ.get("PACKYAPI_BASE_URL") or "",
        os.environ.get("WRITING_BRAIN_BASE_URL") or "",
    ]
    if any("ikun" in value.lower() for value in base_urls if value):
        return "ikun"
    return "packyapi"


def _resolve_gemini_reviewer_route(payload: dict[str, Any]) -> str:
    candidate = str(
        payload.get("gemini_reviewer_route")
        or os.environ.get("WRITING_BRAIN_GEMINI_REVIEWER_ROUTE")
        or os.environ.get("WRITING_BRAIN_GEMINI_ROUTE")
        or "packy"
    ).strip().lower()
    if candidate in {"auto", "packy_schema", "packy", "native"}:
        if candidate in {"auto", "packy_schema"}:
            return "packy"
        return candidate
    return "packy"


def _resolve_procedural_model_name() -> str:
    for name in ["PPCHAT_PROCEDURAL_MODEL", "WRITING_BRAIN_PROCEDURAL_MODEL"]:
        value = os.environ.get(name)
        if value and value.strip():
            return value.strip()
    return DEFAULT_PROCEDURAL_MODEL


def _parse_model_review_json(raw: str) -> dict[str, Any] | None:
    candidate = raw.strip()
    if candidate.startswith("```"):
        lines = candidate.splitlines()
        if len(lines) >= 3:
            candidate = "\n".join(lines[1:-1]).strip()
    try:
        return json.loads(candidate)
    except Exception:
        pass

    start = candidate.find("{")
    end = candidate.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return _parse_partial_review_json(candidate)
    try:
        return json.loads(candidate[start : end + 1])
    except Exception:
        return _parse_partial_review_json(candidate)


def _normalize_model_issues(items: list[Any]) -> list[dict[str, str]]:
    normalized: list[dict[str, str]] = []
    for item in items[:5]:
        if isinstance(item, str):
            summary = item.strip()
            if not summary:
                continue
            normalized.append(
                {
                    "issue_type": "model_issue",
                    "severity": "medium",
                    "summary": summary,
                    "evidence": "",
                }
            )
            continue
        if not isinstance(item, dict):
            continue
        severity = str(item.get("severity") or "medium").strip().lower()
        if severity not in {"low", "medium", "high"}:
            severity = "medium"
        summary = str(item.get("summary") or "").strip()
        if not summary:
            continue
        normalized.append(
            {
                "issue_type": str(item.get("issue_type") or "model_issue").strip() or "model_issue",
                "severity": severity,
                "summary": summary,
                "evidence": str(item.get("evidence") or "").strip(),
            }
        )
    return normalized


def _normalize_review_vote(reviewer: str, layer: dict[str, Any]) -> dict[str, Any]:
    score = layer.get("semantic_score")
    decision = str(layer.get("decision") or "").strip().lower()
    valid_vote = bool(layer.get("mode") == "model_output" and score is not None and decision in {"pass", "revise", "rewrite"})
    top_issue = _normalize_model_issues(layer.get("top_issues") or [])
    issue_summary = top_issue[0]["summary"] if top_issue else ""
    action = _normalize_text_list(layer.get("rewrite_actions") or [], limit=1)
    strength = _normalize_text_list(layer.get("strengths") or [], limit=1)
    return {
        "reviewer": reviewer,
        "provider": str(layer.get("provider") or ""),
        "model": str(layer.get("model") or ""),
        "mode": str(layer.get("mode") or ""),
        "valid_vote": valid_vote,
        "score": int(score) if valid_vote and score is not None else None,
        "decision": decision if valid_vote else "",
        "confidence": _bounded_float(float(layer.get("confidence") or 0.0), 0.0, 1.0) if valid_vote else None,
        "issue": issue_summary,
        "action": action[0] if action else "",
        "strength": strength[0] if strength else "",
        "error": str(layer.get("error") or ""),
        "raw_output": str(layer.get("raw_output") or layer.get("error") or ""),
    }


def _normalize_procedural_review(layer: dict[str, Any], *, baseline_flags: list[str]) -> dict[str, Any]:
    abnormal_review = layer.get("abnormal_review")
    score = layer.get("semantic_score")
    decision = str(layer.get("decision") or "").strip().lower()
    valid_vote = bool(
        layer.get("mode") in {"model_output", "fallback_output"}
        and isinstance(abnormal_review, bool)
        and score is not None
        and decision in {"pass", "revise", "rewrite"}
    )
    flags = _normalize_text_list(layer.get("flags") or [], limit=6)
    if (not valid_vote) and (not flags):
        flags = baseline_flags[:]
    return {
        "reviewer": "gpt5_4_procedural",
        "provider": str(layer.get("provider") or ""),
        "model": str(layer.get("model") or ""),
        "mode": str(layer.get("mode") or ""),
        "valid_vote": valid_vote,
        "score": int(score) if valid_vote and score is not None else None,
        "decision": decision if valid_vote else "",
        "abnormal_review": abnormal_review if valid_vote else None,
        "flags": flags,
        "reason": str(layer.get("reason") or "").strip(),
        "recommendation": str(layer.get("recommendation") or "").strip(),
        "error": str(layer.get("error") or ""),
        "raw_output": str(layer.get("raw_output") or layer.get("error") or ""),
    }


def _normalize_gpt_review_vote(procedural_review: dict[str, Any]) -> dict[str, Any]:
    return {
        "reviewer": "gpt5_4",
        "provider": str(procedural_review.get("provider") or ""),
        "model": str(procedural_review.get("model") or ""),
        "mode": str(procedural_review.get("mode") or ""),
        "valid_vote": bool(procedural_review.get("valid_vote")),
        "score": procedural_review.get("score"),
        "decision": str(procedural_review.get("decision") or ""),
        "confidence": 0.8 if procedural_review.get("valid_vote") else None,
        "issue": str(procedural_review.get("reason") or ""),
        "action": str(procedural_review.get("recommendation") or ""),
        "strength": "",
        "error": str(procedural_review.get("error") or ""),
        "raw_output": str(procedural_review.get("raw_output") or ""),
    }


def _extract_model_issue_payload(parsed: dict[str, Any]) -> list[Any]:
    if parsed.get("top_issues"):
        return list(parsed.get("top_issues") or [])
    issue = str(parsed.get("issue") or "").strip()
    if not issue:
        return []
    return [issue]


def _extract_model_score(parsed: dict[str, Any]) -> int | None:
    raw = parsed.get("semantic_score")
    if raw is None:
        raw = parsed.get("score")
    if raw is None or raw == "":
        return None
    try:
        return _bounded_score(int(raw), 0, 100)
    except Exception:
        return None


def _extract_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "yes", "1"}:
            return True
        if normalized in {"false", "no", "0"}:
            return False
    return None


def _parse_gemini_plaintext_review(raw: str) -> dict[str, Any] | None:
    text = str(raw or "").strip()
    if not text:
        return None
    score = _extract_tagged_value(text, "SCORE")
    decision = _extract_tagged_value(text, "DECISION").lower()
    issue = _extract_tagged_value(text, "ISSUE")
    action = _extract_tagged_value(text, "ACTION")
    strength = _extract_tagged_value(text, "STRENGTH")
    if not score:
        return None
    try:
        numeric_score = _bounded_score(int(re.search(r"\d{1,3}", score).group(0)), 0, 100)
    except Exception:
        return None
    if decision not in {"pass", "revise", "rewrite"}:
        decision = _infer_decision_from_score(numeric_score)
    return {
        "score": numeric_score,
        "decision": decision,
        "issue": issue[:40].strip(),
        "action": action[:40].strip(),
        "strength": strength[:40].strip(),
    }


def _build_gemini_review_layer(
    *,
    provider: str,
    model: str,
    parsed: dict[str, Any],
    raw_output: str,
    confidence: float,
) -> dict[str, Any]:
    decision = str(parsed.get("decision") or "").strip().lower()
    score = _extract_model_score(parsed)
    if score is None or decision not in {"pass", "revise", "rewrite"}:
        return {
            "mode": "prompt_only",
            "provider": provider,
            "model": model,
            "semantic_score": None,
            "decision": None,
            "top_issues": [],
            "rewrite_actions": [],
            "strengths": [],
            "confidence": None,
            "error": "independent reviewer 返回了缺字段或非法字段的结果",
            "raw_output": raw_output,
        }
    issue = str(parsed.get("issue") or "").strip()
    action = str(parsed.get("action") or "").strip()
    strength = str(parsed.get("strength") or "").strip()
    return {
        "mode": "model_output",
        "provider": provider,
        "model": model,
        "semantic_score": score,
        "decision": decision,
        "top_issues": _normalize_model_issues([issue] if issue else []),
        "rewrite_actions": [action] if action else [],
        "strengths": [strength] if strength else [],
        "confidence": confidence,
        "error": "",
        "raw_output": raw_output,
    }


def _extract_tagged_value(text: str, tag: str) -> str:
    pattern = rf"(?im)^{re.escape(tag)}\s*:\s*(.+)$"
    match = re.search(pattern, text)
    if not match:
        return ""
    return match.group(1).strip()


def _extract_model_text_payload(parsed: dict[str, Any], list_key: str, single_key: str) -> list[Any]:
    if parsed.get(list_key):
        return list(parsed.get(list_key) or [])
    single = str(parsed.get(single_key) or "").strip()
    if not single:
        return []
    return [single]


def _normalize_text_list(items: list[Any], *, limit: int) -> list[str]:
    normalized: list[str] = []
    seen: set[str] = set()
    for item in items:
        text = str(item).strip()
        if not text or text in seen:
            continue
        seen.add(text)
        normalized.append(text)
        if len(normalized) >= limit:
            break
    return normalized


def _merge_issues(primary: list[dict[str, str]], secondary: list[dict[str, str]]) -> list[dict[str, str]]:
    merged: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for item in [*primary, *secondary]:
        issue_type = str(item.get("issue_type") or "issue").strip()
        summary = str(item.get("summary") or "").strip()
        if not summary:
            continue
        key = (issue_type, summary)
        if key in seen:
            continue
        seen.add(key)
        merged.append(
            {
                "issue_type": issue_type,
                "severity": _normalize_severity(str(item.get("severity") or "medium")),
                "summary": summary,
                "evidence": str(item.get("evidence") or "").strip(),
            }
        )
        if len(merged) >= 5:
            break
    return merged or [
        {
            "issue_type": "no_major_issue",
            "severity": "low",
            "summary": "未发现显著问题",
            "evidence": "当前稿件整体完成度较高",
        }
    ]


def _merge_texts(primary: list[str], secondary: list[str], *, limit: int) -> list[str]:
    merged: list[str] = []
    seen: set[str] = set()
    for item in [*primary, *secondary]:
        text = str(item).strip()
        if not text or text in seen:
            continue
        seen.add(text)
        merged.append(text)
        if len(merged) >= limit:
            break
    return merged


def _heuristic_layer_summary(report: dict[str, Any]) -> dict[str, Any]:
    return {
        "mode": "heuristic",
        "total_score": int(report["total_score"]),
        "decision": str(report["decision"]),
        "lazy_index": int(report["lazy_index"]),
        "top_issues": list(report.get("top_issues") or []),
        "rewrite_actions": list(report.get("rewrite_actions") or []),
    }


def _build_procedural_flags(
    heuristic_report: dict[str, Any],
    claude_vote: dict[str, Any],
    gemini_vote: dict[str, Any],
    gpt_vote: dict[str, Any] | None,
) -> list[str]:
    flags: list[str] = []
    for reviewer, vote in [("claude", claude_vote), ("gemini", gemini_vote), ("gpt5_4", gpt_vote or {})]:
        if not vote.get("valid_vote"):
            flags.append(f"invalid_{reviewer}_vote")
            continue
        if vote["decision"] == "pass" and int(vote["score"]) < 90:
            flags.append(f"{reviewer}_decision_score_mismatch")
        if vote["decision"] == "rewrite" and int(vote["score"]) > 85:
            flags.append(f"{reviewer}_decision_score_mismatch")

    valid_votes = [vote for vote in [claude_vote, gemini_vote, gpt_vote or {}] if vote.get("valid_vote")]
    decisions = {str(vote.get("decision") or "") for vote in valid_votes}
    if len(decisions) > 1:
        flags.append("multi_party_decision_conflict")
    score_gap = _max_score_gap(valid_votes)
    if score_gap is not None and score_gap >= 10:
        flags.append("multi_party_score_gap_10_plus")

    heuristic_score = int(heuristic_report["total_score"])
    heuristic_decision = str(heuristic_report["decision"])
    for reviewer, vote in [("claude", claude_vote), ("gemini", gemini_vote), ("gpt5_4", gpt_vote or {})]:
        if not vote.get("valid_vote"):
            continue
        if heuristic_score >= 90 and heuristic_decision == "pass" and vote["decision"] != "pass":
            flags.append(f"{reviewer}_harsher_than_procedural_layer")
    return list(dict.fromkeys(flags))


def _build_governance_options(
    claude_vote: dict[str, Any],
    gemini_vote: dict[str, Any],
    gpt_vote: dict[str, Any],
    *,
    quorum_pass: bool,
) -> list[str]:
    options: list[str] = []
    if not claude_vote["valid_vote"]:
        options.append("rerun_claude_review")
    if not gemini_vote["valid_vote"]:
        options.append("rerun_gemini_review")
    if not gpt_vote["valid_vote"]:
        options.append("rerun_gpt5_4_review")
    if options:
        return options

    if quorum_pass:
        return ["user_review_pass_quorum", "user_request_light_polish", "user_request_claude_reconsideration"]
    decisions = {claude_vote["decision"], gemini_vote["decision"], gpt_vote["decision"]}
    if len(decisions) == 1:
        return ["user_accept_reviewer_actions", "user_request_claude_reconsideration"]
    return [
        "user_compare_three_reviews",
        "user_request_claude_reconsideration",
        "user_choose_manual_or_model_revision",
    ]


def _max_score_gap(votes: list[dict[str, Any]]) -> int | None:
    scores = [int(vote["score"]) for vote in votes if vote.get("score") is not None]
    if len(scores) < 2:
        return None
    return max(scores) - min(scores)


def _safe_decision(value: Any) -> str:
    decision = str(value or "").strip().lower()
    if decision in {"pass", "revise", "rewrite"}:
        return decision
    return "revise"


def _infer_decision_from_score(score: int) -> str:
    if score >= 90:
        return "pass"
    if score < 70:
        return "rewrite"
    return "revise"


def _strictest_decision(*values: str) -> str:
    order = {"pass": 0, "revise": 1, "rewrite": 2}
    best = "pass"
    for value in values:
        normalized = _safe_decision(value)
        if order[normalized] > order[best]:
            best = normalized
    return best


def _normalize_severity(value: str) -> str:
    normalized = value.strip().lower()
    if normalized in {"low", "medium", "high"}:
        return normalized
    return "medium"


def _build_issues(
    *,
    must_cover: list[str],
    coverage_count: int,
    paragraphs: list[str],
    first_paragraph_has_claim: bool,
    evidence_hits: int,
    filler_hits: int,
    repeated_count: int,
) -> list[dict[str, str]]:
    issues: list[dict[str, str]] = []
    if must_cover and coverage_count < len(must_cover):
        issues.append(
            {
                "issue_type": "missing_points",
                "severity": "high",
                "summary": f"要求覆盖的重点只写到了 {coverage_count}/{len(must_cover)} 个",
                "evidence": "必须覆盖点未完整展开",
            }
        )
    if not first_paragraph_has_claim:
        issues.append(
            {
                "issue_type": "late_thesis",
                "severity": "high",
                "summary": "核心判断出现过晚，开头还在铺垫",
                "evidence": "第一段没有直接给出核心判断",
            }
        )
    if evidence_hits < 2:
        issues.append(
            {
                "issue_type": "weak_support",
                "severity": "high",
                "summary": "判断展开偏抽象，例子或论据不够",
                "evidence": "文中证据信号过少",
            }
        )
    if filler_hits >= 2:
        issues.append(
            {
                "issue_type": "filler_heavy",
                "severity": "medium",
                "summary": "模板化提示语偏多，信息密度不够",
                "evidence": f"检测到 {filler_hits} 处常见空转折表达",
            }
        )
    if repeated_count >= 1:
        issues.append(
            {
                "issue_type": "repetition",
                "severity": "medium",
                "summary": "有重复表达或段落层面重复",
                "evidence": f"检测到 {repeated_count} 段高度重复内容",
            }
        )
    if len(paragraphs) < 3:
        issues.append(
            {
                "issue_type": "underdeveloped",
                "severity": "medium",
                "summary": "稿子展开不足，看起来更像提纲而不是成稿",
                "evidence": "段落数过少",
            }
        )
    return issues


def _rewrite_actions(issues: list[dict[str, str]]) -> list[str]:
    actions: list[str] = []
    for issue in issues:
        issue_type = issue["issue_type"]
        if issue_type == "missing_points":
            actions.append("把未覆盖的重点逐条补齐，不要只点到为止")
        elif issue_type == "late_thesis":
            actions.append("把核心判断提前到首段或前两段")
        elif issue_type == "weak_support":
            actions.append("至少补一个具体场景、例子或论据")
        elif issue_type == "filler_heavy":
            actions.append("删掉空泛转折词，压缩正确废话")
        elif issue_type == "repetition":
            actions.append("合并重复段落，同一层意思只说一次")
        elif issue_type == "underdeveloped":
            actions.append("把提纲式表达补成完整段落")
    return actions[:5] or ["当前稿件可直接进入人工复核"]


def _strengths(total_score: int, coverage_ratio: float, evidence_hits: int, first_paragraph_has_claim: bool) -> list[str]:
    strengths: list[str] = []
    if coverage_ratio >= 0.8:
        strengths.append("整体方向和任务目标基本对齐")
    if evidence_hits >= 2:
        strengths.append("文中已经有一定论据支撑")
    if first_paragraph_has_claim:
        strengths.append("开头判断相对明确")
    if total_score >= 85:
        strengths.append("整体已经接近可发布状态")
    return strengths[:4]


def _decision(total_score: int, lazy_index: int) -> str:
    if lazy_index >= 7 or total_score < 70:
        return "rewrite"
    if total_score >= 85 and lazy_index <= 4:
        return "pass"
    return "revise"


def _platform_score(platform: str, first_paragraph_has_claim: bool, paragraph_count: int, text_length: int) -> int:
    platform = platform.lower()
    if platform == "wechat":
        return _bounded_score(round(6 + (2 if first_paragraph_has_claim else 0) + (2 if paragraph_count >= 4 else 0)), 0, 10)
    if platform == "xiaohongshu":
        return _bounded_score(round(5 + (2 if paragraph_count <= 8 else 0) + (2 if text_length <= 900 else 0)), 0, 10)
    return _bounded_score(round(6 + (2 if paragraph_count >= 4 else 0) + (1 if text_length >= 900 else 0)), 0, 10)


def _bounded_score(value: int, minimum: int, maximum: int) -> int:
    return max(minimum, min(maximum, value))


def _bounded_float(value: float, minimum: float, maximum: float) -> float:
    return max(minimum, min(maximum, round(value, 2)))


def _normalize_match_text(text: str) -> str:
    return re.sub(r"[^0-9A-Za-z\u4e00-\u9fff]+", "", str(text or "").lower())


def _point_is_covered(text: str, point: str) -> bool:
    point_text = _normalize_match_text(point)
    draft_text = _normalize_match_text(text)
    if not point_text or not draft_text:
        return False
    if point_text in draft_text:
        return True
    if len(point_text) < 8:
        return False
    if len(point_text) >= 16:
        return _shingle_coverage_ratio(point_text, draft_text, width=4) >= 0.35 or _shingle_coverage_ratio(point_text, draft_text, width=3) >= 0.45
    if len(point_text) >= 10:
        return _shingle_coverage_ratio(point_text, draft_text, width=4) >= 0.45 or _shingle_coverage_ratio(point_text, draft_text, width=3) >= 0.55
    return _shingle_coverage_ratio(point_text, draft_text, width=3) >= 0.6


def _shingle_coverage_ratio(point_text: str, draft_text: str, *, width: int) -> float:
    shingles = {point_text[index : index + width] for index in range(max(1, len(point_text) - width + 1))}
    shingles = {item for item in shingles if len(item) == width}
    if not shingles:
        return 0.0
    hits = sum(1 for item in shingles if item in draft_text)
    return hits / len(shingles)


def _parse_partial_review_json(raw: str) -> dict[str, Any] | None:
    candidate = str(raw or "").strip()
    if not candidate:
        return None
    parsed: dict[str, Any] = {}
    for key in ["semantic_score", "score"]:
        value = _extract_jsonish_int(candidate, key)
        if value is not None:
            parsed[key] = value
    for key in ["decision", "issue", "action", "strength", "reason", "recommendation"]:
        value = _extract_jsonish_string(candidate, key)
        if value:
            parsed[key] = value
    confidence = _extract_jsonish_float(candidate, "confidence")
    if confidence is not None:
        parsed["confidence"] = confidence
    abnormal_review = _extract_jsonish_bool(candidate, "abnormal_review")
    if abnormal_review is not None:
        parsed["abnormal_review"] = abnormal_review
    flags = _extract_jsonish_string_list(candidate, "flags")
    if flags is not None:
        parsed["flags"] = flags
    return parsed or None


def _extract_jsonish_string(raw: str, key: str) -> str:
    pattern = rf'(?is)["\']?{re.escape(key)}["\']?\s*:\s*(?:"([^"]*)"|\'([^\']*)\'|([^\n,}}]+))'
    match = re.search(pattern, raw)
    if not match:
        return ""
    return next((item.strip() for item in match.groups() if item and item.strip()), "")


def _extract_jsonish_int(raw: str, key: str) -> int | None:
    match = re.search(rf'(?is)["\']?{re.escape(key)}["\']?\s*:\s*(-?\d+)', raw)
    if not match:
        return None
    return int(match.group(1))


def _extract_jsonish_float(raw: str, key: str) -> float | None:
    match = re.search(rf'(?is)["\']?{re.escape(key)}["\']?\s*:\s*(-?\d+(?:\.\d+)?)', raw)
    if not match:
        return None
    return float(match.group(1))


def _extract_jsonish_bool(raw: str, key: str) -> bool | None:
    value = _extract_jsonish_string(raw, key)
    if value:
        return _extract_bool(value)
    match = re.search(rf'(?is)["\']?{re.escape(key)}["\']?\s*:\s*(true|false)', raw)
    if not match:
        return None
    return _extract_bool(match.group(1))


def _extract_jsonish_string_list(raw: str, key: str) -> list[str] | None:
    match = re.search(rf'(?is)["\']?{re.escape(key)}["\']?\s*:\s*\[(.*?)\]', raw)
    if not match:
        return None
    body = match.group(1)
    items = re.findall(r'"([^"]+)"|\'([^\']+)\'', body)
    flattened = [item[0] or item[1] for item in items if item[0] or item[1]]
    return _normalize_text_list(flattened, limit=6)


def _build_procedural_fallback_review(
    *,
    provider: str,
    model: str,
    raw_output: str,
    heuristic_report: dict[str, Any],
    claude_vote: dict[str, Any],
    gemini_vote: dict[str, Any],
    baseline_flags: list[str],
    error: str,
) -> dict[str, Any]:
    valid_votes = [vote for vote in [claude_vote, gemini_vote] if vote.get("valid_vote")]
    valid_decisions = [str(vote.get("decision") or "") for vote in valid_votes if str(vote.get("decision") or "")]
    if valid_votes:
        score = round(sum(int(vote.get("score") or 0) for vote in valid_votes) / len(valid_votes))
    else:
        score = int(heuristic_report.get("total_score") or 0)
    abnormal_review = bool(baseline_flags)
    if abnormal_review:
        decision = _strictest_decision(*(valid_decisions or [str(heuristic_report.get("decision") or "revise")]), "revise")
    elif len(set(valid_decisions)) == 1 and valid_decisions:
        decision = valid_decisions[0]
    else:
        decision = _strictest_decision(*(valid_decisions or [str(heuristic_report.get("decision") or "revise")]))
    if decision not in {"pass", "revise", "rewrite"}:
        decision = _infer_decision_from_score(int(score))
    reason = "程序议事模型输出不可解析，已使用规则层兜底。"
    if baseline_flags:
        reason = "程序议事模型输出不可解析，且检测到流程异常。"
    recommendation = "优先按当前 flags 继续处理 workflow。"
    if "invalid_gemini_vote" in baseline_flags:
        recommendation = "先重跑 Gemini 独立审稿，再继续治理流程。"
    elif "multi_party_decision_conflict" in baseline_flags:
        recommendation = "保留三方分歧记录，并把冲突点交给用户裁决。"
    return {
        "mode": "fallback_output",
        "provider": provider,
        "model": model,
        "semantic_score": int(score),
        "decision": decision,
        "abnormal_review": abnormal_review,
        "flags": baseline_flags[:6],
        "reason": reason,
        "recommendation": recommendation,
        "error": error,
        "raw_output": raw_output,
    }
