from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any, Iterable

from .config import AppConfig


IGNORED_FILE_NAMES = {
    ".DS_Store",
}

IGNORED_DIR_NAMES = {
    ".git",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
}

TRANSIENT_RUNTIME_PREFIXES = (
    "已生成-",
    "公开来源-",
)

TRANSIENT_RUNTIME_KEYWORDS = (
    "runtime",
    "test",
)

CLEANUP_SCOPED_ROOTS = {
    "publish-packs",
    "release-cycles",
}


def should_ignore_name(name: str) -> bool:
    return not name or name in IGNORED_FILE_NAMES or name in IGNORED_DIR_NAMES


def should_ignore_path(path: Path, *, data_dir: Path | None = None) -> bool:
    if should_ignore_name(path.name):
        return True
    return is_transient_runtime_dir(path)


def iter_data_files(root: Path, *, suffixes: Iterable[str]) -> list[Path]:
    normalized_suffixes = {suffix.lower() for suffix in suffixes}
    if not root.exists():
        return []

    results: list[Path] = []
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        if path.suffix.lower() not in normalized_suffixes:
            continue
        if should_ignore_path(path, data_dir=root):
            continue
        if any(should_ignore_path(parent, data_dir=root) for parent in path.parents if parent != root and root in parent.parents):
            continue
        results.append(path)
    return sorted(results)


def sanitize_data_dir(payload: dict[str, Any], config: AppConfig) -> dict[str, Any]:
    apply = bool(payload.get("apply"))
    targets = collect_cleanup_targets(config.data_dir)
    removed: list[str] = []
    failed: list[dict[str, str]] = []

    if apply:
        for path in targets:
            try:
                if path.is_dir():
                    shutil.rmtree(path)
                elif path.exists():
                    path.unlink()
                removed.append(str(path))
            except Exception as exc:
                failed.append({"path": str(path), "error": str(exc)})

    return {
        "contract_name": "data_dir_sanitize_result",
        "contract_version": "v1",
        "data_dir": str(config.data_dir),
        "mode": "apply" if apply else "dry_run",
        "candidate_count": len(targets),
        "candidates": [str(path) for path in targets],
        "removed_count": len(removed),
        "removed": removed,
        "failed_count": len(failed),
        "failed": failed,
        "recommended_next_actions": _sanitize_actions(apply=apply, removed_count=len(removed), failed_count=len(failed)),
    }


def collect_cleanup_targets(data_dir: Path) -> list[Path]:
    targets: list[Path] = []
    if not data_dir.exists():
        return targets
    for path in data_dir.rglob("*"):
        if path.name in IGNORED_FILE_NAMES:
            targets.append(path)
            continue
        if is_cleanup_target(path, data_dir=data_dir):
            targets.append(path)
    return sorted(targets, key=lambda path: (-len(path.parts), str(path)))


def is_transient_runtime_dir(path: Path) -> bool:
    if not path.is_dir():
        return False
    name = path.name.lower()
    if not name.startswith(TRANSIENT_RUNTIME_PREFIXES):
        return False
    if not any(keyword in name for keyword in TRANSIENT_RUNTIME_KEYWORDS):
        return False
    return True


def is_cleanup_target(path: Path, *, data_dir: Path | None = None) -> bool:
    if not is_transient_runtime_dir(path):
        return False
    if data_dir is None:
        return True
    try:
        relative = path.relative_to(data_dir)
    except ValueError:
        return False
    return relative.parts[:1] and relative.parts[0] in CLEANUP_SCOPED_ROOTS


def _sanitize_actions(*, apply: bool, removed_count: int, failed_count: int) -> list[str]:
    if not apply:
        return ["如确认候选项可删，再用 sanitize-data-dir 并传入 {'apply': true} 执行清理。"]
    actions = []
    if removed_count:
        actions.append(f"已清理 {removed_count} 个数据目录污染项。")
    if failed_count:
        actions.append("有少量污染项清理失败，请检查权限或占用状态后重试。")
    if not actions:
        actions.append("当前没有需要清理的数据目录污染项。")
    return actions
