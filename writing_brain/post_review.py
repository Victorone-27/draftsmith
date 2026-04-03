from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from .config import AppConfig
from .image_supply import IMAGE_EXTENSIONS, find_slot_file
from .platforms import normalize_platform, normalize_platform_list, platform_display_name
from .public_images import collect_public_images
from .publish import PLATFORM_NAMES, build_publish_pack, render_packy_images
from .release import run_release_cycle


StageHandler = Callable[[dict[str, Any], AppConfig, dict[str, Any]], dict[str, Any]]


def run_post_review_pipeline(
    payload: dict[str, Any],
    config: AppConfig,
    *,
    run_id: str,
    final_article_markdown: str,
    final_review_report: dict[str, Any] | None,
    context_pack: dict[str, Any],
) -> dict[str, Any] | None:
    if not bool(payload.get("enable_post_review_pipeline", True)):
        return None

    article_markdown = str(final_article_markdown or "").strip()
    if not article_markdown:
        return None

    final_decision = str((final_review_report or {}).get("decision") or "").strip().lower()
    stages = _normalize_stages(payload.get("post_review_pipeline"), payload)
    if not stages:
        return None

    if final_decision != "pass":
        return {
            "contract_name": "post_review_pipeline_result",
            "contract_version": "v1",
            "run_id": run_id,
            "status": "skipped",
            "decision_gate": "pass",
            "final_decision": final_decision or "awaiting_review",
            "stages": [],
            "artifact_refs": [],
            "recommended_next_actions": ["审稿尚未通过，暂不执行发布后处理流水线。"],
        }

    base_stage_payload = {
        **payload,
        "run_id": run_id,
        "article_markdown": article_markdown,
        "final_article_markdown": article_markdown,
        "context_pack": context_pack,
        "topic": str(payload.get("topic") or context_pack.get("topic") or "").strip(),
        "platform": normalize_platform(payload.get("platform") or context_pack.get("platform") or "wechat"),
        "title": str(payload.get("title") or context_pack.get("title") or "").strip(),
    }

    stage_results: list[dict[str, Any]] = []
    artifact_refs: list[str] = []
    recommended_next_actions: list[str] = []
    shared_outputs: dict[str, Any] = {}

    for index, stage_spec in enumerate(stages, start=1):
        stage_name = str(stage_spec.get("stage") or "").strip().lower()
        handler = _stage_handlers().get(stage_name)
        if handler is None:
            stage_results.append(
                {
                    "stage": stage_name or f"unknown_{index}",
                    "status": "unsupported",
                    "artifact_refs": [],
                    "recommended_next_actions": [f"暂未实现 post-review stage: {stage_name or 'unknown'}"],
                }
            )
            continue

        stage_payload = {**base_stage_payload, **shared_outputs, **dict(stage_spec.get("input") or {})}
        try:
            stage_result = handler(stage_payload, config, stage_spec)
        except Exception as exc:
            stage_results.append(
                {
                    "stage": stage_name,
                    "status": "failed",
                    "artifact_refs": [],
                    "recommended_next_actions": [f"{stage_name} 执行失败，需要人工处理后重试。"],
                    "result": {
                        "error": str(exc),
                    },
                }
            )
            continue
        normalized = {
            "stage": stage_name,
            "status": str(stage_result.get("status") or "completed"),
            "artifact_refs": [str(item) for item in stage_result.get("artifact_refs") or []],
            "recommended_next_actions": [str(item) for item in stage_result.get("recommended_next_actions") or []],
            "result": stage_result,
        }
        stage_results.append(normalized)
        artifact_refs.extend(normalized["artifact_refs"])
        recommended_next_actions.extend(normalized["recommended_next_actions"])
        shared_outputs.update(dict(stage_result.get("shared_outputs") or {}))

    status = "completed"
    if any(item["status"] == "failed" for item in stage_results):
        status = "failed"
    elif any(item["status"] in {"partial", "unsupported"} for item in stage_results):
        status = "partial"

    return {
        "contract_name": "post_review_pipeline_result",
        "contract_version": "v1",
        "run_id": run_id,
        "status": status,
        "decision_gate": "pass",
        "final_decision": final_decision,
        "stages": stage_results,
        "artifact_refs": list(dict.fromkeys(artifact_refs)),
        "recommended_next_actions": list(dict.fromkeys(recommended_next_actions)),
    }


def _stage_handlers() -> dict[str, StageHandler]:
    return {
        "publish_pack": _run_publish_pack_stage,
        "release_cycle": _run_release_cycle_stage,
        "collect_public_images": _run_collect_public_images_stage,
        "render_images": _run_render_images_stage,
    }


def _normalize_stages(raw: Any, payload: dict[str, Any]) -> list[dict[str, Any]]:
    if raw is None:
        target_platforms = normalize_platform_list(payload.get("target_platforms"))
        profile = _post_review_profile(payload)
        if profile in {"off", "disabled", "none"}:
            return []
        if len(target_platforms) > 1:
            if profile in {"debug", "light", "minimal"}:
                return [
                    {"stage": "release_cycle", "input": {"platforms": target_platforms}},
                ]
            return [
                {"stage": "release_cycle", "input": {"platforms": target_platforms}},
                {"stage": "collect_public_images"},
                {"stage": "render_images"},
            ]
        if profile in {"debug", "light", "minimal"}:
            return [
                {"stage": "publish_pack"},
            ]
        return [
            {"stage": "publish_pack"},
            {"stage": "collect_public_images"},
            {"stage": "render_images"},
        ]

    stages: list[dict[str, Any]] = []
    for item in raw or []:
        if isinstance(item, str):
            stage_name = item.strip().lower()
            if stage_name:
                stages.append({"stage": stage_name})
            continue
        if isinstance(item, dict):
            stage_name = str(item.get("stage") or item.get("name") or "").strip().lower()
            if stage_name:
                stages.append(
                    {
                        "stage": stage_name,
                        "input": dict(item.get("input") or {}),
                    }
                )
    return stages


def _run_publish_pack_stage(payload: dict[str, Any], config: AppConfig, stage_spec: dict[str, Any]) -> dict[str, Any]:
    stage_input = dict(stage_spec.get("input") or {})
    resolved_output_dir = _resolve_debug_publish_output_dir(payload, config, stage_input=stage_input)
    publish_result = build_publish_pack(
        {
            **payload,
            **stage_input,
            "article_markdown": str(payload.get("article_markdown") or payload.get("final_article_markdown") or "").strip(),
            "platform": normalize_platform(stage_input.get("platform") or payload.get("platform") or "wechat"),
            "topic": str(stage_input.get("topic") or payload.get("topic") or "").strip(),
            "title": str(stage_input.get("title") or payload.get("title") or "").strip(),
            **({"output_dir": str(resolved_output_dir)} if resolved_output_dir is not None else {}),
        },
        config,
    )
    output_dir = str(publish_result.get("output_dir") or "").strip()
    image_review = dict(publish_result.get("image_review_report") or {})
    image_decision = str(image_review.get("decision") or "").strip().lower()
    stage_status = "completed" if image_decision == "pass" else "partial"
    next_actions = list(publish_result.get("recommended_next_actions") or [])
    if output_dir and image_decision == "pass":
        next_actions.insert(0, f"发布包已生成，可直接打开 {output_dir} 里的 Word 文档复制发布。")
    elif output_dir:
        next_actions.insert(0, f"纯文本发布包已生成在 {output_dir}，图片审核未通过，尚不可作为图文包发布。")
    return {
        **publish_result,
        "status": stage_status,
        "shared_outputs": {
            "publish_pack_output_dir": output_dir,
            "publish_pack_result": publish_result,
        },
        "recommended_next_actions": next_actions,
    }


def _run_release_cycle_stage(payload: dict[str, Any], config: AppConfig, stage_spec: dict[str, Any]) -> dict[str, Any]:
    stage_input = dict(stage_spec.get("input") or {})
    target_platforms = normalize_platform_list(stage_input.get("platforms") or payload.get("target_platforms"))
    resolved_output_root = _resolve_debug_release_output_root(payload, config, stage_input=stage_input)
    release_result = run_release_cycle(
        {
            **payload,
            **stage_input,
            "article_markdown": str(payload.get("article_markdown") or payload.get("final_article_markdown") or "").strip(),
            "source_platform": normalize_platform(stage_input.get("source_platform") or payload.get("platform") or "wechat"),
            "platforms": target_platforms,
            **({"output_root": str(resolved_output_root)} if resolved_output_root is not None else {}),
        },
        config,
    )
    output_root = str(release_result.get("output_root") or "").strip()
    next_actions = list(release_result.get("recommended_next_actions") or [])
    if output_root:
        next_actions.insert(0, f"多平台发布包已生成，可直接在 {output_root} 查看各平台 Word 与配图目录。")
    return {
        **release_result,
        "status": "completed",
        "artifact_refs": [str(release_result.get("output_root") or "")],
        "shared_outputs": {
            "release_cycle_output_root": output_root,
            "release_cycle_result": release_result,
        },
        "recommended_next_actions": next_actions,
    }


def _run_collect_public_images_stage(payload: dict[str, Any], config: AppConfig, stage_spec: dict[str, Any]) -> dict[str, Any]:
    stage_input = dict(stage_spec.get("input") or {})
    stage_payload = {**payload, **stage_input}
    if not stage_payload.get("output_dir") and payload.get("publish_pack_output_dir"):
        stage_payload["output_dir"] = payload["publish_pack_output_dir"]
    if not stage_payload.get("release_cycle_result") and payload.get("release_cycle_result"):
        stage_payload["release_cycle_result"] = payload["release_cycle_result"]
    if not stage_payload.get("output_root") and payload.get("release_cycle_output_root"):
        stage_payload["output_root"] = payload["release_cycle_output_root"]

    collect_result = collect_public_images(stage_payload, config)
    next_actions = list(collect_result.get("recommended_next_actions") or [])
    output_dir = str(collect_result.get("output_dir") or "").strip()
    output_root = str(collect_result.get("output_root") or "").strip()
    if output_root:
        next_actions.insert(0, f"公开图源检索完成，可在 {output_root} 复核各平台图文 Word。")
    elif output_dir:
        next_actions.insert(0, f"公开图源检索完成，可重新打开 {output_dir} 里的图文 Word 检查配图。")
    return {
        **collect_result,
        "shared_outputs": {
            "public_image_collect_result": collect_result,
        },
        "recommended_next_actions": next_actions,
    }


def _post_review_profile(payload: dict[str, Any]) -> str:
    return str(payload.get("post_review_profile") or "delivery").strip().lower() or "delivery"


def _run_render_images_stage(payload: dict[str, Any], config: AppConfig, stage_spec: dict[str, Any]) -> dict[str, Any]:
    stage_input = dict(stage_spec.get("input") or {})
    # Multi-platform: iterate each platform's publish pack
    release_result = payload.get("release_cycle_result")
    if release_result:
        return _render_images_for_release_cycle(payload, config, release_result, stage_input)
    # Single-platform
    output_dir = str(stage_input.get("output_dir") or payload.get("publish_pack_output_dir") or "").strip()
    if not output_dir:
        return {"status": "skipped", "artifact_refs": [], "recommended_next_actions": ["无发布包目录，跳过图片生成。"]}
    return _render_images_for_single_pack(payload, config, Path(output_dir))


def _render_images_for_single_pack(payload: dict[str, Any], config: AppConfig, output_dir: Path) -> dict[str, Any]:
    import json as _json
    image_dir = output_dir / "图片"
    tasks_path = image_dir / "生成任务.json"
    if not tasks_path.exists():
        return {"status": "skipped", "artifact_refs": [], "recommended_next_actions": []}
    try:
        tasks = _json.loads(tasks_path.read_text(encoding="utf-8"))
    except Exception:
        return {"status": "skipped", "artifact_refs": [], "recommended_next_actions": ["生成任务.json 解析失败。"]}
    if not tasks:
        return {"status": "skipped", "artifact_refs": [], "recommended_next_actions": []}

    generated_dir = image_dir / "已生成"
    public_dir = image_dir / "公开来源"
    missing_tasks = []
    for task in tasks:
        filename = str(task.get("filename") or "").strip()
        if not filename:
            continue
        if find_slot_file(generated_dir, filename) or find_slot_file(public_dir, filename):
            continue
        missing_tasks.append(task)

    if not missing_tasks:
        return {"status": "completed", "artifact_refs": [], "recommended_next_actions": []}

    # Write filtered tasks and render
    missing_tasks_path = image_dir / "待生成任务.json"
    missing_tasks_path.write_text(_json.dumps(missing_tasks, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    render_result = render_packy_images({**payload, "tasks_path": str(missing_tasks_path)})

    generated_count = sum(1 for r in render_result.get("results") or [] if r.get("status") == "ok")
    artifact_refs = [str(p) for p in render_result.get("artifact_refs") or []]
    next_actions = list(render_result.get("recommended_next_actions") or [])

    # Refresh publish pack if any images were generated
    if generated_count > 0:
        article_md = str(payload.get("article_markdown") or payload.get("final_article_markdown") or "").strip()
        if article_md:
            refresh = build_publish_pack(
                {**payload, "article_markdown": article_md, "output_dir": str(output_dir)},
                config,
            )
            artifact_refs.extend(str(p) for p in refresh.get("artifact_refs") or [])

    status = "completed" if generated_count == len(missing_tasks) else "partial"
    return {
        "status": status,
        "artifact_refs": list(dict.fromkeys(artifact_refs)),
        "recommended_next_actions": next_actions,
        "render_result": render_result,
    }


def _render_images_for_release_cycle(
    payload: dict[str, Any], config: AppConfig, release_result: dict[str, Any], stage_input: dict[str, Any]
) -> dict[str, Any]:
    output_root = str(release_result.get("output_root") or "").strip()
    if not output_root:
        return {"status": "skipped", "artifact_refs": [], "recommended_next_actions": []}
    all_artifacts: list[str] = []
    all_actions: list[str] = []
    any_partial = False
    for pr in release_result.get("platform_results") or []:
        platform_name = str(pr.get("platform_name") or "").strip()
        if not platform_name:
            continue
        pack_dir = Path(output_root) / "投稿包" / platform_name
        if not pack_dir.exists():
            continue
        sub = _render_images_for_single_pack(payload, config, pack_dir)
        all_artifacts.extend(sub.get("artifact_refs") or [])
        all_actions.extend(sub.get("recommended_next_actions") or [])
        if sub.get("status") == "partial":
            any_partial = True
    return {
        "status": "partial" if any_partial else "completed",
        "artifact_refs": list(dict.fromkeys(all_artifacts)),
        "recommended_next_actions": list(dict.fromkeys(all_actions)),
    }


def _resolve_debug_publish_output_dir(
    payload: dict[str, Any],
    config: AppConfig,
    *,
    stage_input: dict[str, Any],
) -> Path | None:
    explicit = str(stage_input.get("output_dir") or payload.get("output_dir") or "").strip()
    if explicit:
        return None
    if _post_review_profile(payload) not in {"debug", "light", "minimal"}:
        return None
    run_id = str(payload.get("run_id") or "").strip()
    if not run_id:
        return None
    platform = normalize_platform(stage_input.get("platform") or payload.get("platform") or "wechat")
    platform_name = platform_display_name(platform)
    path = config.sessions_dir / run_id / "post-review" / "投稿包" / platform_name
    path.mkdir(parents=True, exist_ok=True)
    return path


def _resolve_debug_release_output_root(
    payload: dict[str, Any],
    config: AppConfig,
    *,
    stage_input: dict[str, Any],
) -> Path | None:
    explicit = str(stage_input.get("output_root") or payload.get("output_root") or "").strip()
    if explicit:
        return None
    if _post_review_profile(payload) not in {"debug", "light", "minimal"}:
        return None
    run_id = str(payload.get("run_id") or "").strip()
    if not run_id:
        return None
    path = config.sessions_dir / run_id / "post-review" / "release-cycle"
    path.mkdir(parents=True, exist_ok=True)
    return path
