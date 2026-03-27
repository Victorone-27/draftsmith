"""Tests for project brief parsing and listing."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from writing_brain.config import AppConfig
from writing_brain.project import (
    _extract_list_section,
    _find_project_dir,
    list_projects,
    list_templates,
    load_project_brief,
)


def _make_config(tmp: Path) -> AppConfig:
    projects_dir = tmp / "projects"
    templates_dir = tmp / "templates"
    projects_dir.mkdir(parents=True, exist_ok=True)
    templates_dir.mkdir(parents=True, exist_ok=True)
    return AppConfig(data_dir=tmp)


class TestExtractListSection(unittest.TestCase):
    def test_extract_bullet_items(self) -> None:
        body = """## 背景
一些背景描述

## 已有 claims
- 观点一
- 观点二
- 观点三

## 禁止写成什么样
- 不要写成列表文
"""
        claims = _extract_list_section(body, "已有 claims")
        self.assertEqual(claims, ["观点一", "观点二", "观点三"])

        constraints = _extract_list_section(body, "禁止写成什么样")
        self.assertEqual(constraints, ["不要写成列表文"])

    def test_missing_section_returns_empty(self) -> None:
        body = "## 背景\n一些内容"
        self.assertEqual(_extract_list_section(body, "不存在的段"), [])

    def test_empty_body(self) -> None:
        self.assertEqual(_extract_list_section("", "任意"), [])


class TestFindProjectDir(unittest.TestCase):
    def test_exact_match(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            projects = Path(tmp)
            (projects / "2026-03-24-ai-coding").mkdir()
            result = _find_project_dir(projects, "2026-03-24-ai-coding")
            self.assertEqual(result, projects / "2026-03-24-ai-coding")

    def test_suffix_match(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            projects = Path(tmp)
            (projects / "2026-03-24-ai-coding").mkdir()
            result = _find_project_dir(projects, "ai-coding")
            self.assertEqual(result, projects / "2026-03-24-ai-coding")

    def test_substring_match(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            projects = Path(tmp)
            (projects / "2026-03-24-ai-coding与业务理解").mkdir()
            result = _find_project_dir(projects, "ai-coding")
            self.assertEqual(result, projects / "2026-03-24-ai-coding与业务理解")

    def test_ambiguous_match_raises(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            projects = Path(tmp)
            (projects / "2026-03-24-ai-coding").mkdir()
            (projects / "2026-03-25-ai-coding-实战").mkdir()
            with self.assertRaises(ValueError):
                _find_project_dir(projects, "ai-coding")

    def test_no_match_returns_none(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            result = _find_project_dir(Path(tmp), "nonexistent")
            self.assertIsNone(result)

    def test_missing_dir_returns_none(self) -> None:
        result = _find_project_dir(Path("/tmp/nonexistent_dir_xyz"), "test")
        self.assertIsNone(result)


class TestLoadProjectBrief(unittest.TestCase):
    def test_loads_frontmatter_fields(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = _make_config(Path(tmp))
            project_dir = config.projects_dir / "2026-03-24-test-project"
            project_dir.mkdir(parents=True)
            brief = project_dir / "brief.md"
            brief.write_text(
                "---\ntitle: 测试主题\ntone: 冷峻\n---\n\n## 已有 claims\n- 观点A\n- 观点B\n\n## 禁止写成什么样\n- 不要水\n",
                encoding="utf-8",
            )
            result = load_project_brief(config, "test-project")
            self.assertEqual(result["topic"], "测试主题")
            self.assertEqual(result["tone_target"], "冷峻")
            self.assertEqual(result["project_claim_refs"], ["观点A", "观点B"])
            self.assertEqual(result["constraints"], ["不要水"])

    def test_missing_project_raises(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = _make_config(Path(tmp))
            with self.assertRaises(FileNotFoundError):
                load_project_brief(config, "nonexistent")

    def test_normalizes_target_platforms(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = _make_config(Path(tmp))
            project_dir = config.projects_dir / "2026-03-24-test-platforms"
            project_dir.mkdir(parents=True)
            brief = project_dir / "brief.md"
            brief.write_text(
                "---\ntitle: 测试主题\ntarget_platforms:\n  - 公众号\n  - 知乎\n---\n",
                encoding="utf-8",
            )
            result = load_project_brief(config, "test-platforms")
            self.assertEqual(result["platform"], "wechat")
            self.assertEqual(result["target_platforms"], ["wechat", "zhihu"])

    def test_missing_brief_raises(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = _make_config(Path(tmp))
            (config.projects_dir / "empty-project").mkdir()
            with self.assertRaises(FileNotFoundError):
                load_project_brief(config, "empty-project")


class TestListProjects(unittest.TestCase):
    def test_lists_project_dirs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = _make_config(Path(tmp))
            (config.projects_dir / "project-a").mkdir()
            (config.projects_dir / "project-b").mkdir()
            result = list_projects(config)
            slugs = [item["slug"] for item in result]
            self.assertIn("project-a", slugs)
            self.assertIn("project-b", slugs)

    def test_empty_projects_dir(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = _make_config(Path(tmp))
            self.assertEqual(list_projects(config), [])


class TestListTemplates(unittest.TestCase):
    def test_lists_md_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = _make_config(Path(tmp))
            (config.templates_dir / "template-a.md").write_text("# A", encoding="utf-8")
            result = list_templates(config)
            self.assertEqual(len(result), 1)
            self.assertEqual(result[0]["name"], "template-a")


if __name__ == "__main__":
    unittest.main()
