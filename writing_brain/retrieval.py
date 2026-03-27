from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .frontmatter import parse_markdown_file
from .hygiene import iter_data_files, should_ignore_name
from .platforms import normalize_platform
from .text import keyword_score


@dataclass(frozen=True)
class KnowledgeItem:
    item_id: str
    title: str
    path: Path
    meta: dict[str, object]
    body: str
    score: float


def load_markdown_items(root: Path, id_key: str, title_key: str) -> list[KnowledgeItem]:
    if not root.exists():
        return []
    items: list[KnowledgeItem] = []
    for path in iter_data_files(root, suffixes={".md"}):
        meta, body = parse_markdown_file(path)
        item_id = str(meta.get(id_key) or path.stem)
        title = str(meta.get(title_key) or meta.get("name") or path.stem)
        items.append(KnowledgeItem(item_id=item_id, title=title, path=path, meta=meta, body=body, score=0.0))
    return items


def rank_items(items: list[KnowledgeItem], query_terms: list[str], extra_text: str = "") -> list[KnowledgeItem]:
    ranked: list[KnowledgeItem] = []
    normalized_platform = normalize_platform(extra_text, default="") if extra_text else ""
    for item in items:
        haystack = " ".join(
            [
                item.title,
                str(item.meta),
                item.body,
            ]
        )
        score = keyword_score(haystack, query_terms)
        fit_platforms = [normalize_platform(value, default="") for value in item.meta.get("fit_platforms") or []]
        if normalized_platform and normalized_platform in fit_platforms:
            score += 3.0
        ranked.append(
            KnowledgeItem(
                item_id=item.item_id,
                title=item.title,
                path=item.path,
                meta=item.meta,
                body=item.body,
                score=score,
            )
        )
    ranked.sort(key=lambda item: item.score, reverse=True)
    return ranked


import re

_DATE_PREFIX_RE = re.compile(r"^\d{4}-\d{2}-\d{2}-")


def load_published_articles(root: Path) -> list[KnowledgeItem]:
    """Scan *root* for ``YYYY-MM-DD-slug/`` directories and read the main article."""
    if not root.exists():
        return []
    items: list[KnowledgeItem] = []
    for child in sorted(root.iterdir()):
        if should_ignore_name(child.name) or not child.is_dir() or not _DATE_PREFIX_RE.match(child.name):
            continue
        # Try common article filenames
        article_path = None
        for candidate in ("母稿.md", "文章.md"):
            p = child / candidate
            if p.exists():
                article_path = p
                break
        if article_path is None:
            continue
        meta, body = parse_markdown_file(article_path)
        slug = _DATE_PREFIX_RE.sub("", child.name)
        title = str(meta.get("title") or slug)
        items.append(
            KnowledgeItem(
                item_id=child.name,
                title=title,
                path=article_path,
                meta={**meta, "date": child.name[:10], "slug": slug},
                body=body,
                score=0.0,
            )
        )
    return items
