from __future__ import annotations

from typing import Any

from .config import AppConfig, ensure_runtime_dirs
from .context_pack import build_context_pack
from .governance import (
    build_governance_alert,
    build_governance_history,
    governance_next_actions,
    governance_policy,
    run_rotation_governance_cycle,
)
from .memory import ingest_memory_record
from .post_review import run_post_review_pipeline
from .review import build_article_review_vote, build_review_report
from .revision import (
    normalize_manual_revision_texts,
    normalize_revision_sequence,
    resolve_article_text,
    run_revision_round,
    write_artifact,
    write_json,
    write_revision_artifacts,
)
from .text import now_run_id
from .writer import run_writer_turn


def run_draft_cycle(payload: dict[str, Any], config: AppConfig) -> dict[str, Any]:
    ensure_runtime_dirs(config)

    run_id = str(payload.get("run_id") or now_run_id())
    context_pack = dict(payload.get("context_pack") or {})
    if not context_pack:
        context_pack = build_context_pack({**payload, "run_id": run_id}, config)
    skip_initial_draft_model = _should_skip_initial_draft_model(payload)

    draft_turn = run_writer_turn(
        {
            **payload,
            "run_id": run_id,
            "task_mode": "draft",
            "context_pack": context_pack,
            "user_message": str(payload.get("draft_message") or payload.get("user_message") or "").strip(),
            # When the user provides an existing mother draft, seed review from that
            # text instead of generating a fresh draft, while keeping later revise
            # rounds eligible for real model calls.
            "force_prompt_only": bool(payload.get("force_prompt_only", False)) or skip_initial_draft_model,
        },
        config,
    )
    artifact_refs: list[str] = []
    draft_text, draft_source = resolve_article_text(
        writer_turn=draft_turn,
        manual_text=str(payload.get("manual_draft_text") or payload.get("draft_text") or "").strip(),
    )
    if draft_text:
        artifact_refs.append(write_artifact(config.sessions_dir, run_id, "draft", draft_text, "md"))

    draft_review_report: dict[str, Any] | None = None
    draft_review_governance: dict[str, Any] | None = None
    revise_turn: dict[str, Any] | None = None
    revised_text = ""
    revised_source = "none"
    final_review_report: dict[str, Any] | None = None
    final_review_governance: dict[str, Any] | None = None
    revision_rounds: list[dict[str, Any]] = []
    next_revision_owner = ""

    if draft_text:
        draft_review_report = build_review_report(
            {
                "run_id": run_id,
                "review_id": f"review_{run_id}_draft",
                "draft_text": draft_text,
                "context_pack": context_pack,
                "platform": context_pack.get("platform"),
                "use_model_reviewer": bool(payload.get("use_model_reviewer", False)),
                "use_governance_review": False,
            }
        )
        final_review_report = draft_review_report
        draft_review_governance = dict(draft_review_report.get("governance") or {}) or None
        final_review_governance = draft_review_governance

        if _should_use_rotation_governance_workflow(payload):
            governed = run_rotation_governance_cycle(
                payload=payload,
                config=config,
                run_id=run_id,
                context_pack=context_pack,
                initial_text=draft_text,
                initial_review_report=draft_review_report,
            )
            revision_rounds = governed["revision_rounds"]
            revised_text = governed["revised_text"]
            revised_source = governed["revised_source"]
            final_review_report = governed["final_review_report"]
            final_review_governance = governed["final_review_governance"]
            next_revision_owner = governed["next_revision_owner"]
            artifact_refs.extend(governed["artifact_refs"])
            if revision_rounds:
                revise_turn = revision_rounds[0].get("turn")
        elif bool(payload.get("auto_revise", True)) and draft_review_report.get("decision") != "pass":
            revision_sequence = normalize_revision_sequence(payload.get("revision_sequence"))
            manual_revision_texts = normalize_manual_revision_texts(payload)
            current_text = draft_text
            current_review_report = draft_review_report
            max_revision_rounds = max(1, int(payload.get("max_revision_rounds") or len(revision_sequence)))

            for index in range(max_revision_rounds):
                owner = revision_sequence[index % len(revision_sequence)]
                revision_result = run_revision_round(
                    payload=payload,
                    config=config,
                    run_id=run_id,
                    context_pack=context_pack,
                    current_text=current_text,
                    current_review_report=current_review_report,
                    revision_owner=owner,
                    manual_revision_texts=manual_revision_texts,
                )
                revision_result["revision_index"] = index + 1
                revision_rounds.append(revision_result)
                if index == 0:
                    revise_turn = revision_result.get("turn")

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
                        revision_index=index + 1,
                        revision_owner=owner,
                        body=round_text,
                    )
                )
                current_text = round_text
                current_review_report = build_review_report(
                    {
                        "run_id": run_id,
                        "review_id": f"review_{run_id}_revised_{index + 1}",
                        "draft_text": round_text,
                        "context_pack": context_pack,
                        "platform": context_pack.get("platform"),
                        "use_model_reviewer": bool(payload.get("use_model_reviewer", False)),
                    }
                )
                revision_result["review_report"] = current_review_report
                revision_result["review_governance"] = dict(current_review_report.get("governance") or {}) or None
                final_review_report = current_review_report
                final_review_governance = revision_result["review_governance"]
                if current_review_report.get("decision") == "pass":
                    break
                next_revision_owner = revision_sequence[(index + 1) % len(revision_sequence)]

    final_article_markdown = revised_text or draft_text
    governance_history = build_governance_history(draft_review_governance, revision_rounds, final_review_governance)
    governance_alert = build_governance_alert(governance_history)
    post_review_result = run_post_review_pipeline(
        payload,
        config,
        run_id=run_id,
        final_article_markdown=final_article_markdown,
        final_review_report=final_review_report,
        context_pack=context_pack,
    )
    if post_review_result:
        artifact_refs.extend(str(item) for item in post_review_result.get("artifact_refs") or [])
    memory_record: dict[str, Any] | None = None
    should_persist_memory = bool(payload.get("finalize", False) or payload.get("published_confirmed", False))
    if should_persist_memory and final_article_markdown:
        memory_record = ingest_memory_record(
            {
                "run_id": run_id,
                "topic": str(payload.get("topic") or context_pack.get("topic") or "").strip(),
                "platform": str(payload.get("platform") or context_pack.get("platform") or "wechat").strip(),
                "final_article_markdown": final_article_markdown,
                "session_summary": str(payload.get("session_summary") or _default_session_summary(context_pack, final_review_report)).strip(),
                "review_report": final_review_report or {},
                "context_pack": context_pack,
                "human_feedback": dict(payload.get("human_feedback") or {}),
                "hotspot_candidates": [str(item) for item in payload.get("hotspot_candidates") or []],
            },
            config,
        )

    result = {
        "contract_name": "draft_cycle_result",
        "contract_version": "v1",
        "run_id": run_id,
        "status": _cycle_status(
            draft_text=draft_text,
            draft_review_report=draft_review_report,
            revised_text=revised_text,
            next_revision_owner=next_revision_owner,
            memory_record=memory_record,
        ),
        "final_decision": (final_review_report or {}).get("decision") or "awaiting_draft",
        "context_pack": context_pack,
        "draft_turn": draft_turn,
        "draft_text": draft_text,
        "draft_source": draft_source,
        "draft_review_report": draft_review_report,
        "draft_review_governance": draft_review_governance,
        "revise_turn": revise_turn,
        "revised_text": revised_text,
        "revised_source": revised_source,
        "revision_policy": {
            "review_owner": "claude",
            "revision_sequence": normalize_revision_sequence(payload.get("revision_sequence")),
        },
        "governance_policy": governance_policy(final_review_governance),
        "revision_rounds": revision_rounds,
        "next_revision_owner": next_revision_owner,
        "final_review_report": final_review_report,
        "final_review_governance": final_review_governance,
        "governance_history": governance_history,
        "governance_alert": governance_alert,
        "final_article_markdown": final_article_markdown,
        "post_review_result": post_review_result,
        "memory_record": memory_record,
        "artifact_refs": list(dict.fromkeys(artifact_refs)),
        "recommended_next_actions": _next_actions(
            draft_turn=draft_turn,
            draft_text=draft_text,
            final_review_report=final_review_report,
            final_review_governance=final_review_governance,
            governance_alert=governance_alert,
            revised_text=revised_text,
            revision_rounds=revision_rounds,
            next_revision_owner=next_revision_owner,
            post_review_result=post_review_result,
            memory_record=memory_record,
        ),
    }
    write_json(config.sessions_dir / f"{run_id}.cycle.json", result)
    return result


def _should_use_rotation_governance_workflow(payload: dict[str, Any]) -> bool:
    mode = str(payload.get("governance_workflow_mode") or "").strip().lower()
    return mode == "rotation_v2"


def _should_skip_initial_draft_model(payload: dict[str, Any]) -> bool:
    if bool(payload.get("prefer_model_draft_over_manual", False)):
        return False
    manual_text = str(payload.get("manual_draft_text") or payload.get("draft_text") or "").strip()
    return bool(manual_text)


def _cycle_status(
    *,
    draft_text: str,
    draft_review_report: dict[str, Any] | None,
    revised_text: str,
    next_revision_owner: str,
    memory_record: dict[str, Any] | None,
) -> str:
    if not draft_text:
        return "awaiting_draft"
    if memory_record:
        return "ingested"
    if next_revision_owner:
        return "awaiting_revision"
    if draft_review_report and draft_review_report.get("decision") != "pass" and not revised_text:
        return "awaiting_revision"
    if revised_text:
        return "revised"
    return "reviewed"


def _next_actions(
    *,
    draft_turn: dict[str, Any],
    draft_text: str,
    final_review_report: dict[str, Any] | None,
    final_review_governance: dict[str, Any] | None,
    governance_alert: dict[str, Any] | None,
    revised_text: str,
    revision_rounds: list[dict[str, Any]],
    next_revision_owner: str,
    post_review_result: dict[str, Any] | None,
    memory_record: dict[str, Any] | None,
) -> list[str]:
    actions: list[str] = []
    if not draft_text:
        actions.extend(draft_turn.get("recommended_next_actions") or [])
        actions.append("当前还没有可评分稿件。先修好模型通道，或手动提供初稿后再跑 draft-cycle。")
        return list(dict.fromkeys(actions))

    gov_actions = governance_next_actions(final_review_governance)
    if gov_actions:
        actions.extend(gov_actions)
    if governance_alert and governance_alert.get("escalate_to_user"):
        actions.append("两轮及以上议事仍存在问题，必须向用户汇报三方冲突点和历次议事结果。")

    decision = str((final_review_report or {}).get("decision") or "")
    if decision == "pass":
        if post_review_result:
            actions.extend(post_review_result.get("recommended_next_actions") or [])
        if memory_record:
            actions.append("本轮文章、审稿结果和知识体已入库。")
        else:
            actions.append("当前稿件已通过 reviewer；请在确认已发布后再执行 finalize / memory-ingest 入库。")
        return list(dict.fromkeys(actions))

    if revised_text:
        if next_revision_owner == "manual":
            actions.append("当前 workflow 等待外部人工修订稿；前台 CLI 只负责继续推进和监控流程，不直接代写正文。")
        elif next_revision_owner == "gemini":
            actions.append("外部人工修订稿仍未通过 reviewer，下一轮应切回 Gemini revise，避免连续沿同一路径打补丁。")
        elif next_revision_owner:
            actions.append(f"当前稿件未通过 reviewer，下一轮应切换到 {next_revision_owner} 修稿。")
        else:
            actions.append("修稿后仍未通过 reviewer，建议围绕 final_review_report.top_issues 再来一轮 revise。")
    else:
        actions.append("当前稿件未通过 reviewer，先按 draft_review_report.rewrite_actions 定向修稿。")
    return actions


def _default_session_summary(context_pack: dict[str, Any], review_report: dict[str, Any] | None) -> str:
    topic = str(context_pack.get("topic") or "本次主题")
    decision = str((review_report or {}).get("decision") or "reviewed")
    return "本次围绕\u201c" + topic + "\u201d完成了一轮写作闭环，当前 reviewer 结论为 " + decision + "。"
