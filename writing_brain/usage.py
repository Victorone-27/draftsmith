from __future__ import annotations

import json
import os
import threading
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator


_thread_local = threading.local()


@contextmanager
def usage_scope(data_dir: str | Path, run_id: str = "", caller_prefix: str = "") -> Iterator[None]:
    prev = getattr(_thread_local, "usage_ctx", None)
    _thread_local.usage_ctx = {
        "data_dir": str(data_dir),
        "run_id": run_id,
        "caller_prefix": caller_prefix,
    }
    try:
        yield
    finally:
        _thread_local.usage_ctx = prev


def get_current_context() -> dict[str, str] | None:
    return getattr(_thread_local, "usage_ctx", None)


def record(
    data_dir: str | Path,
    *,
    run_id: str = "",
    provider: str = "",
    model: str = "",
    caller: str = "",
    prompt_tokens: int = 0,
    completion_tokens: int = 0,
    total_tokens: int = 0,
) -> None:
    usage_dir = _usage_dir(data_dir)
    usage_dir.mkdir(parents=True, exist_ok=True)
    now = datetime.now()
    entry = {
        "timestamp": now.strftime("%Y-%m-%dT%H:%M:%S"),
        "run_id": run_id,
        "provider": provider,
        "model": model,
        "caller": caller,
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": total_tokens,
    }
    path = usage_dir / f"{now.strftime('%Y-%m-%d')}.jsonl"
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def record_from_response(
    data_dir: str | Path,
    *,
    run_id: str = "",
    provider: str = "",
    model: str = "",
    caller: str = "",
    response: dict[str, Any],
) -> None:
    usage = dict(response.get("usage") or {})
    if not usage:
        return
    record(
        data_dir,
        run_id=run_id,
        provider=provider,
        model=model,
        caller=caller,
        prompt_tokens=int(usage.get("prompt_tokens") or 0),
        completion_tokens=int(usage.get("completion_tokens") or 0),
        total_tokens=int(usage.get("total_tokens") or 0),
    )


def query(
    data_dir: str | Path,
    *,
    run_id: str = "",
    date: str = "",
    month: str = "",
    year: str = "",
) -> dict[str, Any]:
    usage_dir = _usage_dir(data_dir)
    records = _load_records(usage_dir, date=date, month=month, year=year)
    if run_id:
        records = [r for r in records if r.get("run_id") == run_id]
    return _aggregate(records, period=run_id or date or month or year or "all")


def _usage_dir(data_dir: str | Path) -> Path:
    return Path(str(data_dir)).expanduser() / "usage"


def _load_records(usage_dir: Path, *, date: str = "", month: str = "", year: str = "") -> list[dict[str, Any]]:
    if not usage_dir.exists():
        return []
    records: list[dict[str, Any]] = []
    for path in sorted(usage_dir.glob("*.jsonl")):
        stem = path.stem  # e.g. "2026-03-26"
        if date and stem != date:
            continue
        if month and not stem.startswith(month):
            continue
        if year and not stem.startswith(year):
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return records


def _aggregate(records: list[dict[str, Any]], period: str) -> dict[str, Any]:
    total_calls = len(records)
    total_tokens = sum(int(r.get("total_tokens") or 0) for r in records)
    by_provider: dict[str, dict[str, int]] = {}
    by_model: dict[str, dict[str, int]] = {}
    by_run_id: dict[str, dict[str, int]] = {}

    for r in records:
        provider = str(r.get("provider") or "unknown")
        model = str(r.get("model") or "unknown")
        rid = str(r.get("run_id") or "unknown")
        pt = int(r.get("prompt_tokens") or 0)
        ct = int(r.get("completion_tokens") or 0)
        tt = int(r.get("total_tokens") or 0)

        if provider not in by_provider:
            by_provider[provider] = {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
        by_provider[provider]["calls"] += 1
        by_provider[provider]["prompt_tokens"] += pt
        by_provider[provider]["completion_tokens"] += ct
        by_provider[provider]["total_tokens"] += tt

        if model not in by_model:
            by_model[model] = {"calls": 0, "total_tokens": 0}
        by_model[model]["calls"] += 1
        by_model[model]["total_tokens"] += tt

        if rid not in by_run_id:
            by_run_id[rid] = {"calls": 0, "total_tokens": 0}
        by_run_id[rid]["calls"] += 1
        by_run_id[rid]["total_tokens"] += tt

    return {
        "period": period,
        "total_calls": total_calls,
        "total_tokens": total_tokens,
        "by_provider": by_provider,
        "by_model": by_model,
        "by_run_id": by_run_id,
    }
