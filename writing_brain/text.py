from __future__ import annotations

import re
from datetime import datetime


FILLER_PHRASES = [
    "值得注意的是",
    "不难发现",
    "某种程度上",
    "在当下这个时代",
    "总的来说",
    "换句话说",
]


def split_paragraphs(text: str) -> list[str]:
    return [part.strip() for part in re.split(r"\n\s*\n", text or "") if part.strip()]


def compact_whitespace(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def extract_terms(*parts: str) -> list[str]:
    seen: dict[str, None] = {}
    for part in parts:
        for token in re.split(r"[，。；：、,\.\-\s/()\[\]【】\u201c\u201d\u2018\u2019!！?？与和的在是]+", part or ""):
            normalized = token.strip().lower()
            if len(normalized) < 2:
                continue
            seen.setdefault(normalized, None)
    return list(seen.keys())


def keyword_score(text: str, terms: list[str]) -> float:
    normalized = compact_whitespace(text).lower()
    score = 0.0
    for term in terms:
        candidate = term.strip().lower()
        if not candidate:
            continue
        if candidate in normalized:
            score += 1.0 + normalized.count(candidate) * 0.2
    return round(score, 4)


def first_paragraph_contains(text: str, terms: list[str]) -> bool:
    paragraphs = split_paragraphs(text)
    if not paragraphs:
        return False
    first = compact_whitespace(paragraphs[0]).lower()
    normalized_terms = [compact_whitespace(term).lower() for term in terms if compact_whitespace(term)]
    if any(term in first for term in normalized_terms):
        return True

    # Chinese opinion writing often states the thesis with a contrastive pattern
    # instead of repeating the exact topic words in the opening sentence.
    if "不是" in first and "而是" in first:
        return True
    if "最危险" in first and ("不是" in first or "而是" in first):
        return True
    return False


def repeated_paragraph_count(text: str) -> int:
    seen: set[str] = set()
    repeated = 0
    for paragraph in split_paragraphs(text):
        normalized = compact_whitespace(paragraph)
        if len(normalized) < 12:
            continue
        if normalized in seen:
            repeated += 1
        else:
            seen.add(normalized)
    return repeated


def filler_count(text: str) -> int:
    return sum(text.count(phrase) for phrase in FILLER_PHRASES)


def count_evidence_signals(text: str) -> int:
    signals = [
        "比如",
        "例如",
        "数据",
        "案例",
        "场景",
        "因为",
        "这意味着",
        "一个很直接的例子",
    ]
    return sum(text.count(signal) for signal in signals)


def starts_with_heading(text: str) -> bool:
    first = (text or "").lstrip()
    return first.startswith("#") or first.startswith("**")


def now_run_id(prefix: str = "run") -> str:
    return f"{prefix}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"


def slug_for_filename(text: str, fallback: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9\u4e00-\u9fff]+", "-", text.strip()).strip("-")
    if cleaned:
        return cleaned[:80]
    return fallback
