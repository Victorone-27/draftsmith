from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .config import AppConfig
from .memory import ingest_memory_record
from .pipelines.quality_session import (
    build_delivery as build_delivery_manifest,
    continue_session as continue_quality_session,
    maybe_accept_delivery,
    run_quality_session,
)


def start_session(payload: dict[str, Any], config: AppConfig) -> dict[str, Any]:
    normalized = {
        **payload,
        "enable_post_review_pipeline": bool(payload.get("enable_post_review_pipeline", True)),
        "post_review_profile": str(payload.get("post_review_profile") or "debug").strip().lower() or "debug",
    }
    result = run_quality_session(normalized, config)
    exception_report = build_exception_report(result)
    result["exception_report"] = exception_report
    result["artifact_refs"] = list(dict.fromkeys([*(result.get("artifact_refs") or []), *(exception_report.get("artifact_refs") or [])]))
    result["recommended_next_actions"] = _dedupe_texts(
        [
            *exception_report.get("recommended_user_actions", []),
            *result.get("recommended_next_actions", []),
        ]
    )
    result["status"] = "exception" if exception_report["has_exception"] else "awaiting_acceptance"
    return result


def continue_session(payload: dict[str, Any], config: AppConfig) -> dict[str, Any]:
    result = continue_quality_session(payload, config)
    exception_report = build_exception_report(result)
    result["exception_report"] = exception_report
    result["artifact_refs"] = list(dict.fromkeys([*(result.get("artifact_refs") or []), *(exception_report.get("artifact_refs") or [])]))
    result["recommended_next_actions"] = _dedupe_texts(
        [
            *exception_report.get("recommended_user_actions", []),
            *result.get("recommended_next_actions", []),
        ]
    )
    result["status"] = "exception" if exception_report["has_exception"] else "awaiting_acceptance"
    return result


def build_delivery(payload: dict[str, Any], config: AppConfig) -> dict[str, Any]:
    session_result = _load_session_result(payload, config)
    result = build_delivery_manifest(
        {
            **payload,
            "run_id": str(session_result.get("run_id") or ""),
            "assignment": dict(session_result.get("assignment") or {}),
            "context_pack": dict(session_result.get("assignment", {}).get("context_pack") or {}),
            "article_markdown": str(session_result.get("article_markdown") or session_result.get("final_article_markdown") or ""),
            "quality_evaluation": dict(session_result.get("quality_evaluation") or {}),
            "image_brief": dict(session_result.get("image_brief") or {}),
        },
        config,
    )
    return result


def resolve_exception(payload: dict[str, Any], config: AppConfig) -> dict[str, Any]:
    session_result = _load_session_result(payload, config)
    exception_report = build_exception_report(session_result)
    return {
        "contract_name": "session_exception_result",
        "contract_version": "v2",
        "run_id": str(session_result.get("run_id") or ""),
        "status": "exception" if exception_report["has_exception"] else "clear",
        "session_result": session_result,
        "exception_report": exception_report,
        "artifact_refs": list(dict.fromkeys([*(session_result.get("artifact_refs") or []), *(exception_report.get("artifact_refs") or [])])),
        "recommended_next_actions": list(exception_report.get("recommended_user_actions") or ["当前没有需要你介入的异常。"]),
    }


def accept_delivery(payload: dict[str, Any], config: AppConfig) -> dict[str, Any]:
    session_result = _load_session_result(payload, config)

    # Normalize v1 session shape to v2 if needed
    if "quality_evaluation" not in session_result and "delivery_manifest" not in session_result:
        session_result = _normalize_v1_to_v2_shape(session_result)

    return maybe_accept_delivery({**payload, "session_result": session_result}, config)


def build_exception_report(cycle_result: dict[str, Any]) -> dict[str, Any]:
    issues: list[dict[str, Any]] = []

    if "quality_evaluation" in cycle_result or "delivery_manifest" in cycle_result:
        quality = dict(cycle_result.get("quality_evaluation") or {})
        delivery_manifest = dict(cycle_result.get("delivery_manifest") or {})
        blocked_dimensions = [str(item) for item in quality.get("blocked_dimensions") or [] if str(item).strip()]
        if blocked_dimensions:
            issues.append(
                _issue(
                    stage="quality_gate",
                    severity="high",
                    summary="正文未通过质量闸门",
                    evidence="blocked_dimensions=" + ",".join(blocked_dimensions),
                    actions=list(quality.get("repair_strategy") or ["先修正文质量，再进入交付阶段。"]),
                    artifact_refs=[str(item) for item in cycle_result.get("artifact_refs") or [] if str(item).endswith(".json")],
                )
            )
        image_gate = dict(delivery_manifest.get("image_gate") or {})
        if image_gate and str(image_gate.get("status") or "").lower() == "blocked":
            report = dict(image_gate.get("report") or {})
            issues.append(
                _issue(
                    stage="image_review",
                    severity="high",
                    summary="图片交付未通过真实性或相关性闸门",
                    evidence="image_gate.blocked_reasons=" + ",".join(str(item) for item in image_gate.get("blocked_reasons") or []),
                    actions=list(report.get("required_actions") or delivery_manifest.get("recommended_next_actions") or ["先补图或替图，再重建交付物。"]),
                    artifact_refs=[str(item) for item in report.get("artifact_refs") or delivery_manifest.get("artifact_refs") or []],
                )
            )
        text_delivery = dict(delivery_manifest.get("text_delivery") or {})
        if text_delivery and str(text_delivery.get("status") or "").lower() != "passed":
            issues.append(
                _issue(
                    stage="delivery_artifacts",
                    severity="high",
                    summary="缺少纯文本可交付产物",
                    evidence=f"text_delivery.status={text_delivery.get('status')}",
                    actions=["重新构建 delivery manifest，直到纯文本交付包产出。"],
                    artifact_refs=[str(item) for item in delivery_manifest.get("artifact_refs") or []],
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
            "contract_version": "v2",
            "run_id": str(cycle_result.get("run_id") or ""),
            "has_exception": bool(issues),
            "issues": issues,
            "artifact_refs": list(dict.fromkeys([ref for issue in issues for ref in issue.get("artifact_refs", [])])),
            "recommended_user_actions": _dedupe_texts([action for issue in issues for action in issue.get("actions", [])]),
        }

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


def _load_session_result(payload: dict[str, Any], config: AppConfig) -> dict[str, Any]:
    direct = dict(payload.get("session_result") or payload.get("cycle_result") or {})
    if direct:
        return direct

    session_path_raw = str(payload.get("session_path") or "").strip()
    if session_path_raw:
        path = Path(session_path_raw).expanduser()
        return json.loads(path.read_text(encoding="utf-8"))

    run_id = str(payload.get("run_id") or "").strip()
    if run_id:
        path = config.sessions_dir / f"{run_id}.session.json"
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
        cycle_path = config.sessions_dir / f"{run_id}.cycle.json"
        if cycle_path.exists():
            return json.loads(cycle_path.read_text(encoding="utf-8"))
        raise FileNotFoundError(f"session result not found: {path}")
    raise ValueError("run_id、session_path 或 session_result 至少要提供一个")


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


def _normalize_v1_to_v2_shape(session_result: dict[str, Any]) -> dict[str, Any]:
    """Normalize old v1 session shape to v2 quality_session shape for unified acceptance path."""
    exception_report = build_exception_report(session_result)

    # Build a minimal v2-like shape with quality_evaluation and delivery_manifest
    normalized = {
        **session_result,
        "quality_evaluation": {
            "contract_name": "quality_evaluation",
            "contract_version": "v1_compat",
            "blocked_dimensions": [],
            "repair_strategy": [],
        },
        "delivery_manifest": {
            "contract_name": "delivery_manifest",
            "contract_version": "v1_compat",
            "status": "blocked" if exception_report["has_exception"] else "passed",
            "text_delivery": {
                "status": "passed" if not exception_report["has_exception"] else "blocked",
            },
            "rich_delivery": {
                "status": "unknown",
            },
            "image_gate": {
                "status": "unknown",
            },
            "artifact_refs": list(session_result.get("artifact_refs") or []),
        },
        "assignment": {
            "topic": str(session_result.get("context_pack", {}).get("topic") or ""),
            "platform": str(session_result.get("context_pack", {}).get("platform") or "wechat"),
            "user_goal": str(session_result.get("context_pack", {}).get("user_goal") or ""),
            "context_pack": dict(session_result.get("context_pack") or {}),
        },
        "article_markdown": str(session_result.get("final_article_markdown") or ""),
        "final_review_report": dict(session_result.get("final_review_report") or {}),
    }

    if exception_report["has_exception"]:
        normalized["status"] = "blocked"
        normalized["recommended_next_actions"] = list(exception_report.get("recommended_user_actions") or [])

    return normalized


def _build_delivery_summary(cycle_result: dict[str, Any]) -> dict[str, Any]:
    if "delivery_manifest" in cycle_result:
        delivery_manifest = dict(cycle_result.get("delivery_manifest") or {})
        return {
            "final_decision": str(cycle_result.get("final_decision") or ""),
            "run_id": str(cycle_result.get("run_id") or ""),
            "outputs": [
                {
                    "scope": "single_platform",
                    "platform": str(cycle_result.get("assignment", {}).get("platform") or ""),
                    "output_dir": str(((delivery_manifest.get("publish_result") or {}).get("output_dir") or "")),
                    "artifact_refs": [str(item) for item in delivery_manifest.get("artifact_refs") or []],
                }
            ],
        }
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
