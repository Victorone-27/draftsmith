from __future__ import annotations

from typing import Any

from .config import AppConfig
from .review import build_article_review_vote, build_review_report
from .revision import (
    run_revision_round,
    write_artifact,
    write_revision_artifacts,
)


def run_rotation_governance_cycle(
    *,
    payload: dict[str, Any],
    config: AppConfig,
    run_id: str,
    context_pack: dict[str, Any],
    initial_text: str,
    initial_review_report: dict[str, Any],
) -> dict[str, Any]:
    reject_threshold = max(1, int(payload.get("claude_gate_reject_threshold") or 2))
    pair_pass_score = max(0, min(100, int(payload.get("pair_pass_score") or 85)))
    cycle_order = normalize_rotation_cycle(payload.get("governance_rotation_cycle"))
    current_text = initial_text
    current_review_report = initial_review_report
    revised_text = ""
    revised_source = "none"
    next_revision_owner = ""
    artifact_refs: list[str] = []
    revision_rounds: list[dict[str, Any]] = []
    stage_history: list[dict[str, Any]] = []
    final_review_report = initial_review_report

    for gate_index in range(reject_threshold):
        gemini_round = run_revision_round(
            payload=payload,
            config=config,
            run_id=run_id,
            context_pack=context_pack,
            current_text=current_text,
            current_review_report=current_review_report,
            revision_owner="gemini",
            manual_revision_texts=[],
        )
        gemini_round["revision_index"] = len(revision_rounds) + 1
        revision_rounds.append(gemini_round)

        round_text = str(gemini_round.get("text") or "").strip()
        round_source = str(gemini_round.get("source") or "none")
        if not round_text:
            next_revision_owner = "gemini"
            break

        revised_text = round_text
        revised_source = round_source
        artifact_refs.extend(
            write_revision_artifacts(
                config.sessions_dir,
                run_id,
                revision_index=gemini_round["revision_index"],
                revision_owner="gemini",
                body=round_text,
            )
        )
        current_text = round_text
        claude_vote = build_article_review_vote(
            {
                "run_id": run_id,
                "draft_text": current_text,
                "context_pack": context_pack,
                "platform": context_pack.get("platform"),
            },
            reviewer="claude",
        )
        gate_stage = build_rotation_gate_stage(
            writer_owner="gemini",
            claude_vote=claude_vote,
            reject_count=gate_index + 1,
            reject_threshold=reject_threshold,
        )
        gemini_round["review_governance"] = gate_stage
        stage_history.append(gate_stage)
        final_review_report = build_rotation_review_report(current_text, [claude_vote], pass_score=pair_pass_score)
        current_review_report = final_review_report
        if gate_stage["passed"]:
            final_governance = build_rotation_governance(
                policy=rotation_policy(reject_threshold=reject_threshold, pair_pass_score=pair_pass_score, cycle_order=cycle_order),
                stage_history=stage_history,
                final_stage=gate_stage,
                next_revision_owner="",
                exhausted=False,
            )
            return {
                "revision_rounds": revision_rounds,
                "revised_text": revised_text,
                "revised_source": revised_source,
                "final_review_report": final_review_report,
                "final_review_governance": final_governance,
                "next_revision_owner": "",
                "artifact_refs": artifact_refs,
            }

    pair_stage = run_rotation_pair_stage(
        run_id=run_id,
        current_text=current_text,
        context_pack=context_pack,
        writer_owner="gemini",
        reviewers=["claude", "gpt5_4"],
        pass_score=pair_pass_score,
    )
    if revision_rounds:
        revision_rounds[-1]["review_governance"] = pair_stage
    stage_history.append(pair_stage)
    final_review_report = build_rotation_review_report(current_text, pair_stage["votes"], pass_score=pair_pass_score)
    current_review_report = final_review_report
    if pair_stage["passed"]:
        final_governance = build_rotation_governance(
            policy=rotation_policy(reject_threshold=reject_threshold, pair_pass_score=pair_pass_score, cycle_order=cycle_order),
            stage_history=stage_history,
            final_stage=pair_stage,
            next_revision_owner="",
            exhausted=False,
        )
        return {
            "revision_rounds": revision_rounds,
            "revised_text": revised_text,
            "revised_source": revised_source,
            "final_review_report": final_review_report,
            "final_review_governance": final_governance,
            "next_revision_owner": "",
            "artifact_refs": artifact_refs,
        }

    for owner in cycle_order[1:]:
        revision_result = run_revision_round(
            payload=payload,
            config=config,
            run_id=run_id,
            context_pack=context_pack,
            current_text=current_text,
            current_review_report=current_review_report,
            revision_owner=owner,
            manual_revision_texts=[],
        )
        revision_result["revision_index"] = len(revision_rounds) + 1
        revision_rounds.append(revision_result)

        round_text = str(revision_result.get("text") or "").strip()
        round_source = str(revision_result.get("source") or "none")
        if not round_text:
            next_revision_owner = owner
            break

        revised_text = round_text
        revised_source = round_source
        artifact_refs.extend(
            write_revision_artifacts(
                config.sessions_dir,
                run_id,
                revision_index=revision_result["revision_index"],
                revision_owner=owner,
                body=round_text,
            )
        )
        current_text = round_text
        reviewers = [item for item in cycle_order if item != owner]
        pair_stage = run_rotation_pair_stage(
            run_id=run_id,
            current_text=current_text,
            context_pack=context_pack,
            writer_owner=owner,
            reviewers=reviewers,
            pass_score=pair_pass_score,
        )
        revision_result["review_governance"] = pair_stage
        stage_history.append(pair_stage)
        final_review_report = build_rotation_review_report(current_text, pair_stage["votes"], pass_score=pair_pass_score)
        current_review_report = final_review_report
        if pair_stage["passed"]:
            final_governance = build_rotation_governance(
                policy=rotation_policy(reject_threshold=reject_threshold, pair_pass_score=pair_pass_score, cycle_order=cycle_order),
                stage_history=stage_history,
                final_stage=pair_stage,
                next_revision_owner="",
                exhausted=False,
            )
            return {
                "revision_rounds": revision_rounds,
                "revised_text": revised_text,
                "revised_source": revised_source,
                "final_review_report": final_review_report,
                "final_review_governance": final_governance,
                "next_revision_owner": "",
                "artifact_refs": artifact_refs,
            }

    final_stage = stage_history[-1] if stage_history else run_rotation_pair_stage(
        run_id=run_id,
        current_text=current_text,
        context_pack=context_pack,
        writer_owner="gemini",
        reviewers=["claude", "gpt5_4"],
        pass_score=pair_pass_score,
    )
    final_governance = build_rotation_governance(
        policy=rotation_policy(reject_threshold=reject_threshold, pair_pass_score=pair_pass_score, cycle_order=cycle_order),
        stage_history=stage_history,
        final_stage=final_stage,
        next_revision_owner=next_revision_owner,
        exhausted=not bool(next_revision_owner),
    )
    return {
        "revision_rounds": revision_rounds,
        "revised_text": revised_text,
        "revised_source": revised_source,
        "final_review_report": final_review_report,
        "final_review_governance": final_governance,
        "next_revision_owner": next_revision_owner,
        "artifact_refs": artifact_refs,
    }


def rotation_policy(*, reject_threshold: int, pair_pass_score: int, cycle_order: list[str]) -> dict[str, Any]:
    return {
        "initial_writer": "gemini",
        "gate_reviewer": "claude",
        "claude_gate_reject_threshold": reject_threshold,
        "pair_pass_average_score": pair_pass_score,
        "cycle_order": cycle_order,
        "recusal_rule": "writer_recuses_from_voting",
        "codex_role": "chancellor_only",
    }


def run_rotation_pair_stage(
    *,
    run_id: str,
    current_text: str,
    context_pack: dict[str, Any],
    writer_owner: str,
    reviewers: list[str],
    pass_score: int,
) -> dict[str, Any]:
    votes = [
        build_article_review_vote(
            {
                "run_id": run_id,
                "draft_text": current_text,
                "context_pack": context_pack,
                "platform": context_pack.get("platform"),
            },
            reviewer=reviewer,
        )
        for reviewer in reviewers
    ]
    valid_votes = [vote for vote in votes if vote.get("valid_vote")]
    average_score = round(sum(int(vote["score"]) for vote in valid_votes) / len(valid_votes), 1) if len(valid_votes) == 2 else None
    has_rewrite = any(vote.get("decision") == "rewrite" for vote in valid_votes)
    passed = bool(len(valid_votes) == 2 and average_score is not None and average_score >= pass_score and not has_rewrite)
    decisions = {str(vote.get("decision") or "") for vote in valid_votes}
    consensus_decision = "pass" if passed else ("rewrite" if "rewrite" in decisions else "revise")
    return {
        "mode": "writer_recusal_rotation",
        "stage_type": "pair_review",
        "writer_owner": writer_owner,
        "writer_recused": True,
        "reviewers": reviewers,
        "votes": votes,
        "valid_vote_count": len(valid_votes),
        "average_score": average_score,
        "pass_score": pass_score,
        "passed": passed,
        "consensus_decision": consensus_decision,
    }


def build_rotation_gate_stage(
    *,
    writer_owner: str,
    claude_vote: dict[str, Any],
    reject_count: int,
    reject_threshold: int,
) -> dict[str, Any]:
    passed = bool(claude_vote.get("valid_vote") and claude_vote.get("decision") == "pass")
    return {
        "mode": "writer_recusal_rotation",
        "stage_type": "claude_gate",
        "writer_owner": writer_owner,
        "writer_recused": True,
        "reviewers": ["claude"],
        "votes": [claude_vote],
        "valid_vote_count": 1 if claude_vote.get("valid_vote") else 0,
        "average_score": claude_vote.get("score"),
        "pass_score": None,
        "passed": passed,
        "consensus_decision": "pass" if passed else str(claude_vote.get("decision") or "revise"),
        "reject_count": reject_count,
        "reject_threshold": reject_threshold,
    }


def build_rotation_review_report(current_text: str, votes: list[dict[str, Any]], *, pass_score: int) -> dict[str, Any]:
    valid_votes = [vote for vote in votes if vote.get("valid_vote")]
    average_score = round(sum(int(vote["score"]) for vote in valid_votes) / len(valid_votes), 1) if valid_votes else None
    rewrite_actions = [str(vote.get("action") or "") for vote in valid_votes if str(vote.get("action") or "").strip()]
    strengths = [str(vote.get("strength") or "") for vote in valid_votes if str(vote.get("strength") or "").strip()]
    top_issues = []
    for vote in valid_votes:
        issue = str(vote.get("issue") or "").strip()
        if issue:
            top_issues.append({"issue_type": "model_issue", "severity": "medium", "summary": issue, "evidence": ""})
    has_rewrite = any(vote.get("decision") == "rewrite" for vote in valid_votes)
    decision = "pass" if valid_votes and average_score is not None and average_score >= pass_score and not has_rewrite else ("rewrite" if has_rewrite else "revise")
    return {
        "contract_name": "review_report",
        "contract_version": "v2",
        "review_id": "rotation_review",
        "run_id": "",
        "decision": decision,
        "total_score": average_score if average_score is not None else 0,
        "lazy_index": 0,
        "score_breakdown": {},
        "top_issues": top_issues or [{"issue_type": "model_issue", "severity": "low", "summary": "等待下一轮复核", "evidence": ""}],
        "rewrite_actions": list(dict.fromkeys(rewrite_actions)) or ["按评审意见继续定向修稿"],
        "strengths": list(dict.fromkeys(strengths)),
        "draft_text": current_text,
    }


def build_rotation_governance(
    *,
    policy: dict[str, Any],
    stage_history: list[dict[str, Any]],
    final_stage: dict[str, Any],
    next_revision_owner: str,
    exhausted: bool,
) -> dict[str, Any]:
    votes = list(final_stage.get("votes") or [])
    valid_votes = [vote for vote in votes if vote.get("valid_vote")]
    average_score = final_stage.get("average_score")
    passed = bool(final_stage.get("passed"))
    consensus_decision = "pass" if passed else str(final_stage.get("consensus_decision") or "revise")
    return {
        "contract_name": "review_governance",
        "contract_version": "v2",
        "mode": "writer_recusal_rotation",
        "user_authority": "final_veto",
        "policy": policy,
        "stage_history": stage_history,
        "final_stage": final_stage,
        "writer_owner": str(final_stage.get("writer_owner") or ""),
        "writer_recused": bool(final_stage.get("writer_recused")),
        "eligible_reviewers": list(final_stage.get("reviewers") or []),
        "review_votes": votes,
        "valid_vote_count": len(valid_votes),
        "average_score": average_score,
        "pair_pass_score": policy["pair_pass_average_score"],
        "quorum_pass": passed,
        "consensus_decision": consensus_decision,
        "decision_ready": bool(valid_votes),
        "requires_user_arbitration": bool(exhausted and not passed),
        "next_writer_owner": next_revision_owner,
        "decision_options": (
            ["user_review_pass_quorum"]
            if passed
            else (["user_manual_arbitration_required"] if exhausted and not next_revision_owner else [f"rerun_{next_revision_owner or 'writer'}_revision"])
        ),
    }


def normalize_rotation_cycle(raw: Any) -> list[str]:
    items = raw if isinstance(raw, list) else ["gemini", "gpt5_4", "claude"]
    normalized = [str(item).strip().lower() for item in items if str(item).strip()]
    allowed = {"gemini", "gpt5_4", "gpt", "gpt-5.4", "claude"}
    cleaned: list[str] = []
    for item in normalized:
        if item not in allowed:
            continue
        canonical = "gpt5_4" if item in {"gpt", "gpt-5.4"} else item
        if canonical not in cleaned:
            cleaned.append(canonical)
    return cleaned or ["gemini", "gpt5_4", "claude"]


def governance_policy(governance: dict[str, Any] | None) -> dict[str, str]:
    if not governance:
        return {
            "mode": "legacy",
            "user_authority": "",
            "primary_reviewer": "claude",
            "secondary_reviewer": "",
            "procedural_reviewer": "",
            "codex_role": "chancellor_only",
        }
    if str(governance.get("mode") or "") == "writer_recusal_rotation":
        policy = dict(governance.get("policy") or {})
        return {
            "mode": "writer_recusal_rotation",
            "user_authority": str(governance.get("user_authority") or "final_veto"),
            "primary_reviewer": str(policy.get("gate_reviewer") or "claude"),
            "secondary_reviewer": "writer_recusal_pair",
            "procedural_reviewer": "",
            "codex_role": str(policy.get("codex_role") or "chancellor_only"),
        }
    policy = dict(governance.get("policy") or {})
    return {
        "mode": str(governance.get("mode") or "three_powers"),
        "user_authority": str(governance.get("user_authority") or "final_veto"),
        "primary_reviewer": str(policy.get("primary_reviewer") or "claude"),
        "secondary_reviewer": str(policy.get("secondary_reviewer") or "gemini"),
        "procedural_reviewer": str(policy.get("procedural_reviewer") or "gpt5_4_ppchat"),
        "codex_role": str(policy.get("codex_role") or "chancellor_only"),
    }


def governance_next_actions(governance: dict[str, Any] | None) -> list[str]:
    if not governance:
        return []
    if str(governance.get("mode") or "") == "writer_recusal_rotation":
        if governance.get("quorum_pass"):
            return ["当前轮值治理已通过，下一步应把完整议事记录汇报给用户。"]
        if governance.get("requires_user_arbitration"):
            return ["Gemini、gpt-5.4、Claude 一整轮轮值后仍未通过，必须交由用户人工裁决。"]
        next_writer = str(governance.get("next_writer_owner") or "")
        if next_writer:
            return [f"当前稿件未通过双评审，下一轮应由 {next_writer} 修稿，且该模型在该轮回避投票。"]
        return ["当前轮值治理尚未通过，应继续按双评审意见推进。"]
    claude_review = dict(governance.get("claude_review") or {})
    gemini_review = dict(governance.get("gemini_review") or {})
    gpt_review = dict(governance.get("gpt_review") or {})
    procedural_review = dict(governance.get("procedural_review") or {})
    if not claude_review.get("valid_vote"):
        return ["Claude 主审票据无效，需先重跑主审后再进入用户裁决。"]
    if not gemini_review.get("valid_vote"):
        return ["Gemini 独立评分无效，需重跑独立评审后再交给用户裁决。"]
    if not gpt_review.get("valid_vote"):
        return ["gpt-5.4 评分票据无效，需先重跑 gpt-5.4 议事层后再交给用户裁决。"]
    if not procedural_review.get("valid_vote"):
        return ["gpt-5.4 程序议事票据无效，需先重跑议事层后再交给用户裁决。"]
    if governance.get("quorum_pass"):
        return ["三方中已有至少两方判定通过，下一步应把完整议事记录汇报给用户。"]
    if governance.get("requires_user_arbitration"):
        return ["Claude、Gemini 与 gpt-5.4 三方议事已完成且存在分歧，下一步应把三方完整意见并排交给用户裁决。"]
    if governance.get("decision_ready"):
        return ["Claude、Gemini 与 gpt-5.4 三方议事票已齐，下一步应把完整记录提交给用户确认。"]
    return []


def build_governance_history(
    draft_review_governance: dict[str, Any] | None,
    revision_rounds: list[dict[str, Any]],
    final_review_governance: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    if final_review_governance and str(final_review_governance.get("mode") or "") == "writer_recusal_rotation":
        history: list[dict[str, Any]] = []
        for index, stage in enumerate(final_review_governance.get("stage_history") or [], 1):
            votes = list(stage.get("votes") or [])
            history.append(
                {
                    "stage": str(stage.get("stage_type") or "rotation"),
                    "revision_index": index,
                    "revision_owner": str(stage.get("writer_owner") or ""),
                    "decision_ready": bool(stage.get("valid_vote_count")),
                    "quorum_pass": bool(stage.get("passed")),
                    "consensus_decision": str(stage.get("consensus_decision") or ""),
                    "requires_user_arbitration": False,
                    "procedural_flags": [],
                    "claude_decision": _vote_decision_by_reviewer(votes, "claude"),
                    "gemini_decision": _vote_decision_by_reviewer(votes, "gemini"),
                    "gpt_decision": _vote_decision_by_reviewer(votes, "gpt5_4"),
                }
            )
        return history
    history: list[dict[str, Any]] = []
    if draft_review_governance:
        history.append(_governance_history_entry("draft", 0, "", draft_review_governance))
    for round_item in revision_rounds:
        governance = dict(round_item.get("review_governance") or {})
        if not governance:
            continue
        history.append(
            _governance_history_entry(
                "revision",
                int(round_item.get("revision_index") or 0),
                str(round_item.get("revision_owner") or ""),
                governance,
            )
        )
    return history


def build_governance_alert(history: list[dict[str, Any]]) -> dict[str, Any] | None:
    problematic = [
        item
        for item in history
        if (not item.get("decision_ready")) or item.get("requires_user_arbitration") or bool(item.get("procedural_flags"))
    ]
    if len(problematic) < 2:
        return None
    conflict_points: list[str] = []
    for item in problematic:
        if item.get("claude_decision") != item.get("gemini_decision"):
            conflict_points.append(f"第{item['revision_index'] or 'draft'}轮 Claude/Gemini 结论冲突")
        if item.get("claude_decision") != item.get("gpt_decision"):
            conflict_points.append(f"第{item['revision_index'] or 'draft'}轮 Claude/gpt-5.4 结论冲突")
        for flag in item.get("procedural_flags") or []:
            conflict_points.append(f"第{item['revision_index'] or 'draft'}轮 flag: {flag}")
    return {
        "escalate_to_user": True,
        "problem_round_count": len(problematic),
        "conflict_points": list(dict.fromkeys(conflict_points)),
        "history": problematic,
    }


def _vote_decision_by_reviewer(votes: list[dict[str, Any]], reviewer: str) -> str:
    for vote in votes:
        if str(vote.get("reviewer") or "") == reviewer:
            return str(vote.get("decision") or "")
    return ""


def _governance_history_entry(stage: str, revision_index: int, revision_owner: str, governance: dict[str, Any]) -> dict[str, Any]:
    return {
        "stage": stage,
        "revision_index": revision_index,
        "revision_owner": revision_owner,
        "decision_ready": bool(governance.get("decision_ready")),
        "quorum_pass": bool(governance.get("quorum_pass")),
        "consensus_decision": str(governance.get("consensus_decision") or ""),
        "requires_user_arbitration": bool(governance.get("requires_user_arbitration")),
        "procedural_flags": list(governance.get("procedural_flags") or []),
        "claude_decision": str((governance.get("claude_review") or {}).get("decision") or ""),
        "gemini_decision": str((governance.get("gemini_review") or {}).get("decision") or ""),
        "gpt_decision": str((governance.get("gpt_review") or {}).get("decision") or ""),
    }
