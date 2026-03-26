from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .config import AppConfig
from .memory import ingest_memory_record
from .workflow import run_draft_cycle


def start_session(payload: dict[str, Any], config: AppConfig) -> dict[str, Any]:
    normalized = {
        **payload,
        "auto_revise": bool(payload.get("auto_revise", True)),
        "enable_post_review_pipeline": bool(payload.get("enable_post_review_pipeline", True)),
        "finalize": False,
        "published_confirmed": False,
    }
    result = run_draft_cycle(normalized, config)
    exception_report = build_exception_report(result)
    return {
        "contract_name": "session_start_result",
        "contract_version": "v1",
        "run_id": str(result.get("run_id") or ""),
        "status": "exception" if exception_report["has_exception"] else "awaiting_acceptance",
        "topic": str((result.get("context_pack") or {}).get("topic") or payload.get("topic") or ""),
        "cycle_result": result,
        "exception_report": exception_report,
        "artifact_refs": list(dict.fromkeys([*(result.get("artifact_refs") or []), *(exception_report.get("artifact_refs") or [])])),
        "recommended_next_actions": _dedupe_texts(
            [
                *exception_report.get("recommended_user_actions", []),
                *result.get("recommended_next_actions", []),
            ]
        ),
    }


def resolve_exception(payload: dict[str, Any], config: AppConfig) -> dict[str, Any]:
    cycle_result = _load_cycle_result(payload, config)
    exception_report = build_exception_report(cycle_result)
    return {
        "contract_name": "session_exception_result",
        "contract_version": "v1",
        "run_id": str(cycle_result.get("run_id") or ""),
        "status": "exception" if exception_report["has_exception"] else "clear",
        "cycle_result": cycle_result,
        "exception_report": exception_report,
        "artifact_refs": list(dict.fromkeys([*(cycle_result.get("artifact_refs") or []), *(exception_report.get("artifact_refs") or [])])),
        "recommended_next_actions": list(exception_report.get("recommended_user_actions") or ["当前没有需要你介入的异常。"]),
    }


def accept_delivery(payload: dict[str, Any], config: AppConfig) -> dict[str, Any]:
    cycle_result = _load_cycle_result(payload, config)
    exception_report = build_exception_report(cycle_result)
    if exception_report["has_exception"]:
        return {
            "contract_name": "delivery_acceptance_result",
            "contract_version": "v1",
            "run_id": str(cycle_result.get("run_id") or ""),
            "status": "blocked",
            "cycle_result": cycle_result,
            "exception_report": exception_report,
            "memory_record": None,
            "artifact_refs": list(dict.fromkeys([*(cycle_result.get("artifact_refs") or []), *(exception_report.get("artifact_refs") or [])])),
            "recommended_next_actions": list(exception_report.get("recommended_user_actions") or ["先处理阻塞异常，再做最终验收。"]),
        }

    memory_record = dict(cycle_result.get("memory_record") or {})
    if not memory_record:
        context_pack = dict(cycle_result.get("context_pack") or {})
        memory_record = ingest_memory_record(
            {
                "run_id": str(cycle_result.get("run_id") or ""),
                "topic": str(payload.get("topic") or context_pack.get("topic") or "").strip(),
                "platform": str(payload.get("platform") or context_pack.get("platform") or "wechat").strip(),
                "final_article_markdown": str(cycle_result.get("final_article_markdown") or "").strip(),
                "session_summary": str(payload.get("session_summary") or "").strip(),
                "review_report": dict(cycle_result.get("final_review_report") or {}),
                "context_pack": context_pack,
                "human_feedback": dict(payload.get("human_feedback") or {}),
                "hotspot_candidates": [str(item) for item in payload.get("hotspot_candidates") or []],
            },
            config,
        )

    result = {
        "contract_name": "delivery_acceptance_result",
        "contract_version": "v1",
        "run_id": str(cycle_result.get("run_id") or ""),
        "status": "accepted",
        "cycle_result": cycle_result,
        "delivery_summary": _build_delivery_summary(cycle_result),
        "memory_record": memory_record,
        "artifact_refs": list(dict.fromkeys([*(cycle_result.get("artifact_refs") or []), *(memory_record.get("archive_refs") or [])])),
        "recommended_next_actions": [
            "最终产出已验收，可以按发布计划分平台上传。",
            "本轮经验已写入 memory，下次会自动复用。",
        ],
    }
    _write_delivery_record(config, result)
    return result


def build_exception_report(cycle_result: dict[str, Any]) -> dict[str, Any]:
    issues: list[dict[str, Any]] = []

    final_decision = str(cycle_result.get("final_decision") or "").strip().lower()
    if final_decision and final_decision != "pass":
        issues.append(
            _issue(
                stage="article_review",
                severity="high",
                summary="文章审稿未通过",
                evidence=f"final_decision={final_decision}",
                actions=["检查审稿意见并决定是否允许系统继续修稿。"],
                artifact_refs=_collect_review_artifacts(cycle_result),
            )
        )

    governance_alert = dict(cycle_result.get("governance_alert") or {})
    if governance_alert.get("requires_user_arbitration"):
        issues.append(
            _issue(
                stage="governance",
                severity="high",
                summary="审稿治理需要用户裁决",
                evidence="多方审稿结论未形成可直接执行的共识",
                actions=["阅读 governance_history，决定采纳哪一轮稿件或是否继续修稿。"],
                artifact_refs=[str(item) for item in cycle_result.get("artifact_refs") or [] if str(item).endswith(".json")],
            )
        )

    post_review_result = dict(cycle_result.get("post_review_result") or {})
    post_review_status = str(post_review_result.get("status") or "").strip().lower()
    if post_review_result and post_review_status in {"failed", "partial"}:
        issues.append(
            _issue(
                stage="post_review_pipeline",
                severity="high" if post_review_status == "failed" else "medium",
                summary="发布后处理流水线未完整通过",
                evidence=f"post_review_result.status={post_review_status}",
                actions=["检查发布包、图片检索和多平台产物是否缺失，再决定是否重跑。"],
                artifact_refs=[str(item) for item in post_review_result.get("artifact_refs") or []],
            )
        )

    issues.extend(_collect_delivery_issues(cycle_result))
    issues = _dedupe_issues(issues)
    return {
        "contract_name": "session_exception_report",
        "contract_version": "v1",
        "run_id": str(cycle_result.get("run_id") or ""),
        "has_exception": bool(issues),
        "issues": issues,
        "artifact_refs": list(dict.fromkeys([ref for issue in issues for ref in issue.get("artifact_refs", [])])),
        "recommended_user_actions": _dedupe_texts([action for issue in issues for action in issue.get("actions", [])]),
    }


def _load_cycle_result(payload: dict[str, Any], config: AppConfig) -> dict[str, Any]:
    direct = dict(payload.get("cycle_result") or {})
    if direct:
        return direct

    session_path_raw = str(payload.get("session_path") or "").strip()
    if session_path_raw:
        path = Path(session_path_raw).expanduser()
        return json.loads(path.read_text(encoding="utf-8"))

    run_id = str(payload.get("run_id") or "").strip()
    if run_id:
        path = config.sessions_dir / f"{run_id}.cycle.json"
        if not path.exists():
            raise FileNotFoundError(f"cycle result not found: {path}")
        return json.loads(path.read_text(encoding="utf-8"))
    raise ValueError("run_id、session_path 或 cycle_result 至少要提供一个")


def _collect_delivery_issues(cycle_result: dict[str, Any]) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    post_review_result = dict(cycle_result.get("post_review_result") or {})
    for stage in post_review_result.get("stages") or []:
        stage_name = str(stage.get("stage") or "").strip().lower()
        result = dict(stage.get("result") or {})
        if stage_name == "publish_pack":
            issues.extend(_publish_pack_issues(result))
        elif stage_name == "release_cycle":
            for platform_result in result.get("platform_results") or []:
                issues.extend(_release_platform_issues(dict(platform_result)))
        elif stage_name == "collect_public_images":
            issues.extend(_public_image_collect_issues(result))
    return issues


def _publish_pack_issues(result: dict[str, Any]) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    image_review_report = dict(result.get("image_review_report") or {})
    if str(image_review_report.get("decision") or "").strip().lower() != "pass":
        issues.append(
            _issue(
                stage="image_review",
                severity="high",
                summary="图片包未通过审核",
                evidence=f"image_review_report.decision={image_review_report.get('decision')}",
                actions=list(image_review_report.get("required_actions") or ["处理图片审核问题后再验收。"]),
                artifact_refs=[str(item) for item in image_review_report.get("artifact_refs") or []],
            )
        )
    if not any(str(item).endswith("可直接发布-纯文本可复制.docx") for item in result.get("artifact_refs") or []):
        issues.append(
            _issue(
                stage="delivery_artifacts",
                severity="high",
                summary="缺少纯文本可复制 Word",
                evidence="publish_pack artifact_refs 中未检测到纯文本 Word",
                actions=["重跑发布包，直到产出可复制 Word。"],
                artifact_refs=[str(item) for item in result.get("artifact_refs") or []],
            )
        )
    return issues


def _release_platform_issues(platform_result: dict[str, Any]) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    platform = str(platform_result.get("platform") or "")
    if str(platform_result.get("final_decision") or "").strip().lower() != "pass":
        issues.append(
            _issue(
                stage="platform_review",
                severity="high",
                summary=f"{platform} 平台稿未通过审稿",
                evidence=f"final_decision={platform_result.get('final_decision')}",
                actions=[f"检查 {platform} 平台稿的审稿结果，决定是否继续修稿。"],
                artifact_refs=[str(platform_result.get("article_path") or "")],
            )
        )
    issues.extend(_publish_pack_issues(dict(platform_result.get("publish_result") or {})))
    return issues


def _public_image_collect_issues(result: dict[str, Any]) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    status = str(result.get("status") or "").strip().lower()
    if status in {"failed", "partial"}:
        issues.append(
            _issue(
                stage="public_image_collect",
                severity="medium" if status == "partial" else "high",
                summary="公开图片检索未完整通过",
                evidence=f"collect_public_images.status={status}",
                actions=list(result.get("recommended_next_actions") or ["检查公开图源检索失败原因。"]),
                artifact_refs=[str(item) for item in result.get("artifact_refs") or []],
            )
        )
    return issues


def _collect_review_artifacts(cycle_result: dict[str, Any]) -> list[str]:
    refs = [str(item) for item in cycle_result.get("artifact_refs") or []]
    return [item for item in refs if item.endswith(".md") or item.endswith(".json")]


def _build_delivery_summary(cycle_result: dict[str, Any]) -> dict[str, Any]:
    post_review_result = dict(cycle_result.get("post_review_result") or {})
    outputs: list[dict[str, Any]] = []
    for stage in post_review_result.get("stages") or []:
        stage_name = str(stage.get("stage") or "").strip().lower()
        result = dict(stage.get("result") or {})
        if stage_name == "publish_pack":
            outputs.append(
                {
                    "scope": "single_platform",
                    "platform": str(result.get("platform") or ""),
                    "output_dir": str(result.get("output_dir") or ""),
                    "artifact_refs": [str(item) for item in result.get("artifact_refs") or []],
                }
            )
        elif stage_name == "release_cycle":
            for platform_result in result.get("platform_results") or []:
                publish_result = dict(platform_result.get("publish_result") or {})
                outputs.append(
                    {
                        "scope": "multi_platform",
                        "platform": str(platform_result.get("platform") or ""),
                        "output_dir": str(publish_result.get("output_dir") or ""),
                        "artifact_refs": [str(item) for item in publish_result.get("artifact_refs") or []],
                    }
                )
    return {
        "final_decision": str(cycle_result.get("final_decision") or ""),
        "run_id": str(cycle_result.get("run_id") or ""),
        "outputs": outputs,
    }


def _write_delivery_record(config: AppConfig, result: dict[str, Any]) -> None:
    run_id = str(result.get("run_id") or "").strip()
    if not run_id:
        return
    path = config.sessions_dir / f"{run_id}.delivery.json"
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _issue(
    *,
    stage: str,
    severity: str,
    summary: str,
    evidence: str,
    actions: list[str],
    artifact_refs: list[str],
) -> dict[str, Any]:
    return {
        "stage": stage,
        "severity": severity,
        "summary": summary,
        "evidence": evidence,
        "actions": _dedupe_texts(actions),
        "artifact_refs": list(dict.fromkeys(ref for ref in artifact_refs if ref)),
    }


def _dedupe_texts(items: list[str]) -> list[str]:
    deduped: list[str] = []
    for item in items:
        text = str(item).strip()
        if text and text not in deduped:
            deduped.append(text)
    return deduped


def _dedupe_issues(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    deduped: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for item in items:
        key = (str(item.get("stage") or ""), str(item.get("summary") or ""), str(item.get("evidence") or ""))
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)
    return deduped
