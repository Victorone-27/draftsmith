from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def resolve_article_text(*, writer_turn: dict[str, Any] | None, manual_text: str) -> tuple[str, str]:
    if writer_turn and writer_turn.get("mode") == "model_output":
        text = str(writer_turn.get("reply_text") or "").strip()
        if text:
            return text, "model_output"
    if manual_text:
        return manual_text, "manual_input"
    return "", "none"


def write_artifact(root: Path, run_id: str, suffix: str, body: str, extension: str) -> str:
    path = root / f"{run_id}.{suffix}.{extension}"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body + ("\n" if body and not body.endswith("\n") else ""), encoding="utf-8")
    return path.name


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
