from __future__ import annotations

from pathlib import Path
from typing import Any

from .config import AppConfig
from .frontmatter import parse_markdown_file


def load_project_brief(config: AppConfig, project_slug: str) -> dict[str, Any]:
    """Load a project brief and return payload-compatible fields."""
    project_dir = _find_project_dir(config.projects_dir, project_slug)
    if project_dir is None:
        raise FileNotFoundError(f"Project not found: {project_slug}")

    brief_path = project_dir / "brief.md"
    if not brief_path.exists():
        raise FileNotFoundError(f"No brief.md in project: {project_dir}")

    meta, body = parse_markdown_file(brief_path)

    result: dict[str, Any] = {}
    if meta.get("title"):
        result["topic"] = str(meta["title"])
    if meta.get("core_claim"):
        result["core_claim"] = str(meta["core_claim"])
    if meta.get("tone"):
        result["tone_target"] = str(meta["tone"])
    if meta.get("mode"):
        result["writing_mode"] = str(meta["mode"])

    platforms = meta.get("target_platforms")
    if isinstance(platforms, list) and platforms:
        result["target_platforms"] = [str(p) for p in platforms]

    # Extract claim refs from "已有 claims" section
    claim_refs = _extract_list_section(body, "已有 claims")
    if claim_refs:
        result["project_claim_refs"] = claim_refs

    # Extract constraints from "禁止写成什么样" section
    constraints = _extract_list_section(body, "禁止写成什么样")
    if constraints:
        result["constraints"] = constraints

    # Extract needed evidence from "需要补的外部论据" section
    evidence_needs = _extract_list_section(body, "需要补的外部论据")
    if evidence_needs:
        result["evidence_needs"] = evidence_needs

    result["project_slug"] = project_slug
    result["project_dir"] = str(project_dir)
    return result


def list_projects(config: AppConfig) -> list[dict[str, str]]:
    """List all projects under projects_dir."""
    if not config.projects_dir.exists():
        return []
    projects = []
    for child in sorted(config.projects_dir.iterdir()):
        if not child.is_dir():
            continue
        brief = child / "brief.md"
        title = child.name
        if brief.exists():
            meta, _ = parse_markdown_file(brief)
            title = str(meta.get("title") or child.name)
        projects.append({"slug": child.name, "title": title})
    return projects


def list_templates(config: AppConfig) -> list[dict[str, str]]:
    """List all templates under templates_dir."""
    if not config.templates_dir.exists():
        return []
    templates = []
    for path in sorted(config.templates_dir.glob("*.md")):
        templates.append({"name": path.stem, "path": str(path)})
    return templates


def _find_project_dir(projects_dir: Path, slug: str) -> Path | None:
    if not projects_dir.exists():
        return None
    # Exact match
    exact = projects_dir / slug
    if exact.is_dir():
        return exact
    # Suffix match (slug without date prefix)
    for child in projects_dir.iterdir():
        if child.is_dir() and child.name.endswith(slug):
            return child
    # Substring match
    for child in projects_dir.iterdir():
        if child.is_dir() and slug in child.name:
            return child
    return None


def _extract_list_section(body: str, heading: str) -> list[str]:
    """Extract bullet items from a markdown section by heading."""
    lines = body.splitlines()
    in_section = False
    items: list[str] = []
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("## ") and heading in stripped:
            in_section = True
            continue
        if in_section and stripped.startswith("## "):
            break
        if in_section and stripped.startswith("- "):
            item = stripped[2:].strip()
            if item:
                items.append(item)
    return items
