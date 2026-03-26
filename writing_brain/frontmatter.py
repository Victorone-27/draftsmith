from __future__ import annotations

from pathlib import Path
from typing import Optional


def parse_markdown_file(path: Path) -> tuple[dict[str, object], str]:
    text = path.read_text(encoding="utf-8")
    return parse_frontmatter(text)


def parse_frontmatter(text: str) -> tuple[dict[str, object], str]:
    if not text.startswith("---\n"):
        return {}, text
    lines = text.splitlines()
    end_index = None
    for index in range(1, len(lines)):
        if lines[index].strip() == "---":
            end_index = index
            break
    if end_index is None:
        return {}, text
    header_lines = lines[1:end_index]
    body = "\n".join(lines[end_index + 1 :]).strip()
    return _parse_header(header_lines), body


def _parse_header(lines: list[str]) -> dict[str, object]:
    data: dict[str, object] = {}
    current_key: Optional[str] = None
    current_list: Optional[list[str]] = None

    for raw_line in lines:
        line = raw_line.rstrip()
        if not line.strip():
            continue
        stripped = line.lstrip()
        if stripped.startswith("- "):
            item = stripped.split("- ", 1)[1].strip()
            if current_key is None:
                continue
            if current_list is None:
                current_list = []
                data[current_key] = current_list
            current_list.append(item)
            continue
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        current_key = key.strip()
        current_list = None
        normalized = value.strip()
        if normalized == "":
            current_list = []
            data[current_key] = current_list
        else:
            data[current_key] = _coerce_scalar(normalized)
    return data


def _coerce_scalar(value: str) -> object:
    if value.isdigit():
        return int(value)
    lowered = value.lower()
    if lowered == "true":
        return True
    if lowered == "false":
        return False
    return value
