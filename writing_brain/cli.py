from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Optional

from .config import ensure_runtime_dirs, load_config
from .context_pack import build_context_pack
from .image_review import build_image_review_report
from .memory import build_daily_digest, ingest_memory_record
from .project import list_projects, list_templates, load_project_brief
from .public_images import collect_public_images
from .publish import build_publish_pack, render_packy_images
from .release import run_release_cycle
from .review import build_review_report
from .session_ops import accept_delivery, resolve_exception, start_session
from .workflow import run_draft_cycle
from .writer import run_writer_turn


INTERNAL_COMMANDS = [
    "build-context-pack",
    "review-draft",
    "memory-ingest",
    "daily-digest",
    "writer-chat",
    "draft-cycle",
    "release-cycle",
    "build-publish-pack",
    "review-image-pack",
    "collect-public-images",
    "render-packy-images",
    "list-projects",
    "list-templates",
]


class _VisibleSubParsersAction(argparse._SubParsersAction):
    def _get_subactions(self) -> list[argparse.Action]:
        return [action for action in super()._get_subactions() if action.help != argparse.SUPPRESS]


def main() -> int:
    parser = argparse.ArgumentParser(prog="writing-brain")
    parser.add_argument("--data-dir", help="Override WRITING_BRAIN_DATA_DIR")
    subparsers = parser.add_subparsers(
        dest="command",
        required=True,
        metavar="{start-session,resolve-exception,accept-delivery}",
        action=_VisibleSubParsersAction,
    )

    for name, help_text in [
        ("start-session", "启动一轮完整写作会话，只返回异常或待验收结果"),
        ("resolve-exception", "读取会话结果并汇总需要你裁决的异常"),
        ("accept-delivery", "确认最终交付物并触发 memory 入库"),
    ]:
        subparser = subparsers.add_parser(name, help=help_text)
        subparser.add_argument("--input", help="JSON string, @file path, or - for stdin")
        subparser.add_argument("--output", help="Write JSON result to a file")
        subparser.add_argument("--project", help="Project slug to load brief from")

    for name in INTERNAL_COMMANDS:
        subparser = subparsers.add_parser(name, help=argparse.SUPPRESS)
        if name in {"list-projects", "list-templates"}:
            continue
        subparser.add_argument("--input", help="JSON string, @file path, or - for stdin")
        subparser.add_argument("--output", help="Write JSON result to a file")
        subparser.add_argument("--project", help="Project slug to load brief from")

    args = parser.parse_args()
    config = load_config(args.data_dir)
    ensure_runtime_dirs(config)

    if args.command == "list-projects":
        result = list_projects(config)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0

    if args.command == "list-templates":
        result = list_templates(config)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0

    payload = _load_json(args.input)

    # Merge project brief into payload (payload values take precedence)
    project_slug = getattr(args, "project", None)
    if project_slug:
        project_data = load_project_brief(config, project_slug)
        payload = {**project_data, **payload}

    if args.command == "start-session":
        result = start_session(payload, config)
    elif args.command == "resolve-exception":
        result = resolve_exception(payload, config)
    elif args.command == "accept-delivery":
        result = accept_delivery(payload, config)
    elif args.command == "build-context-pack":
        result = build_context_pack(payload, config)
    elif args.command == "review-draft":
        result = build_review_report(payload)
    elif args.command == "memory-ingest":
        result = ingest_memory_record(payload, config)
    elif args.command == "writer-chat":
        result = run_writer_turn(payload, config)
    elif args.command == "draft-cycle":
        result = run_draft_cycle(payload, config)
    elif args.command == "release-cycle":
        result = run_release_cycle(payload, config)
    elif args.command == "build-publish-pack":
        result = build_publish_pack(payload, config)
    elif args.command == "review-image-pack":
        result = build_image_review_report(payload)
    elif args.command == "collect-public-images":
        result = collect_public_images(payload, config)
    elif args.command == "render-packy-images":
        result = render_packy_images(payload)
    else:
        result = build_daily_digest(payload, config)

    rendered = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        Path(args.output).write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    return 0


def _load_json(raw: Optional[str]) -> dict[str, Any]:
    if not raw:
        return {}
    if raw == "-":
        return json.loads(sys.stdin.read())
    if raw.startswith("@"):
        return json.loads(Path(raw[1:]).read_text(encoding="utf-8"))
    return json.loads(raw)


if __name__ == "__main__":
    raise SystemExit(main())
