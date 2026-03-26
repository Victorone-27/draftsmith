from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .config import AppConfig
from .writer import run_writer_turn


def resolve_article_text(*, writer_turn: dict[str, Any] | None, manual_text: str) -> tuple[str, str]:
    if writer_turn and writer_turn.get("mode") == "model_output":
        text = str(writer_turn.get("reply_text") or "").strip()
        if text:
            return text, "model_output"
    if manual_text:
        return manual_text, "manual_input"
    return "", "none"


def run_revision_round(
    *,
    payload: dict[str, Any],
    config: AppConfig,
    run_id: str,
    context_pack: dict[str, Any],
    current_text: str,
    current_review_report: dict[str, Any],
    revision_owner: str,
    manual_revision_texts: list[str],
) -> dict[str, Any]:
    owner = revision_owner.strip().lower()
    if owner == "manual":
        manual_text = manual_revision_texts.pop(0) if manual_revision_texts else ""
        return {
            "revision_index": 0,
            "revision_owner": owner,
            "turn": None,
            "text": manual_text,
            "source": "manual_input" if manual_text else "none",
        }

    turn = run_writer_turn(
        {
            **payload,
            "run_id": run_id,
            "task_mode": "revise",
            "context_pack": context_pack,
            "current_draft": current_text,
            "review_report": current_review_report,
            "revision_owner": owner,
            "user_message": str(payload.get("revise_message") or "请根据 reviewer 意见定向修稿。").strip(),
        },
        config,
    )
    text, source = resolve_article_text(writer_turn=turn, manual_text="")
    return {
        "revision_index": 0,
        "revision_owner": owner,
        "turn": turn,
        "text": text,
        "source": source,
    }


def normalize_revision_sequence(raw: Any) -> list[str]:
    items = raw if isinstance(raw, list) else []
    normalized = [str(item).strip().lower() for item in items if str(item).strip()]
    if not normalized:
        normalized = ["gemini", "claude", "gpt5_4"]
    allowed = {"gemini", "manual", "claude", "gpt5_4", "gpt", "gpt-5.4"}
    filtered = [("gpt5_4" if item in {"gpt", "gpt-5.4"} else item) for item in normalized if item in allowed]
    return filtered or ["gemini", "claude", "gpt5_4"]


def normalize_manual_revision_texts(payload: dict[str, Any]) -> list[str]:
    items = [str(item).strip() for item in payload.get("manual_revision_texts") or [] if str(item).strip()]
    legacy = str(payload.get("manual_revised_text") or "").strip()
    if legacy:
        items.insert(0, legacy)
    return items


def write_artifact(root: Path, run_id: str, suffix: str, body: str, extension: str) -> str:
    path = root / f"{run_id}.{suffix}.{extension}"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body + ("\n" if body and not body.endswith("\n") else ""), encoding="utf-8")
    return path.name


def write_revision_artifacts(root: Path, run_id: str, *, revision_index: int, revision_owner: str, body: str) -> list[str]:
    refs = [
        write_artifact(root, run_id, "revised", body, "md"),
        write_artifact(root, run_id, f"revised.{revision_index}.{revision_owner}", body, "md"),
    ]
    return list(dict.fromkeys(refs))


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
