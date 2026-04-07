from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from docx import Document

from writing_brain.config import AppConfig
from writing_brain.image_supply import GENERATED_INDEX_FILENAME, PUBLIC_INDEX_FILENAME
from writing_brain.image_review import build_image_review_report
from writing_brain.post_review import run_post_review_pipeline
from writing_brain.public_images import (
    _query_variants_for_commons,
    _search_commons,
    _search_pexels,
    _search_unsplash,
    collect_public_images,
)
from writing_brain.publish import _public_search_query, build_publish_pack


MINI_PNG = (
    b"\x89PNG\r\n\x1a\n"
    b"\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x02\x00\x00\x00\x90wS\xde"
    b"\x00\x00\x00\x0cIDAT\x08\x99c\xf8\xcf\xc0\x00\x00\x03\x01\x01\x00\xc9\xfe\x92\xef"
    b"\x00\x00\x00\x00IEND\xaeB`\x82"
)


ARTICLE = (
    "# 中国 AI 行业最危险的不是落后而是开始适应落后\n\n"
    "中国 AI 行业现在最危险的，不是模型暂时落后，而是开始适应落后。\n\n"
    "版本升级不是名字变化，而是能力边界变化。\n\n"
    "长期不用最强模型的人，会系统性误判未来。\n\n"
    "入口重要，但模型上限决定谁定义明天。"
)


def _write_generated_index(output_dir: Path) -> None:
    generated_dir = output_dir / "图片" / "已生成"
    (output_dir / "图片" / GENERATED_INDEX_FILENAME).write_text(
        "{\n"
        f'  "封面": {{"path": "{generated_dir / "封面.png"}", "provider": "packyapi", "model": "nano2", "publishable": true}},\n'
        f'  "配图-02": {{"path": "{generated_dir / "配图-02.png"}", "provider": "packyapi", "model": "nano2", "publishable": true}}\n'
        "}\n",
        encoding="utf-8",
    )


def _mock_search(query: str) -> list[dict[str, str]]:
    return [
        {
            "title": f"{query[:18]} pic-{i}",
            "page_url": f"https://commons.wikimedia.org/wiki/{abs(hash(query))}_{i}",
            "download_url": f"https://upload.wikimedia.org/mock-image-{abs(hash(query))}-{i}.png",
            "license": "CC BY-SA 4.0",
            "author": "Mock Author",
        }
        for i in range(3)
    ]


class PublicImageTests(unittest.TestCase):
    def test_image_review_flags_photo_style_prompts_under_generated_infographic_strategy(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            output_dir = Path(tmp) / "out" / "公众号"
            image_dir = output_dir / "图片"
            generated_dir = image_dir / "已生成"
            generated_dir.mkdir(parents=True, exist_ok=True)

            placements = {
                "cover": {"path": "已生成/封面.png", "caption": "封面图", "source_type": "generated"},
                "inline": [
                    {
                        "path": "已生成/配图-01.png",
                        "caption": "组织关系变化",
                        "after_contains": "真正变化的不是工具界面，而是组织里的责任关系。",
                        "source_type": "generated",
                    },
                    {
                        "path": "已生成/配图-02.png",
                        "caption": "预算路径变化",
                        "after_contains": "预算审批和执行路径会被重新切开。",
                        "source_type": "generated",
                    },
                ],
            }
            (image_dir / "插图位置.json").write_text(json.dumps(placements, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            tasks = [
                {
                    "filename": "封面",
                    "source_type": "generated",
                    "prompt": "中国语境，真实纪实摄影，新闻图片或杂志专题封面质感，真实人物或真实场景，自然光。",
                },
                {
                    "filename": "配图-01",
                    "source_type": "generated",
                    "prompt": "围绕组织责任变化生成配图，中国语境，真实纪实摄影，杂志专题图片质感，自然光。",
                },
                {
                    "filename": "配图-02",
                    "source_type": "generated",
                    "prompt": "围绕预算路径变化生成配图，中国语境，真实纪实摄影，真实场景，不要海报感。",
                },
            ]
            (image_dir / "生成任务.json").write_text(json.dumps(tasks, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            (image_dir / GENERATED_INDEX_FILENAME).write_text(
                '{\n'
                f'  "封面": {{"path": "{generated_dir / "封面.png"}", "provider": "packyapi", "model": "nano2", "prompt": "中国语境，真实纪实摄影，新闻图片或杂志专题封面质感，真实人物或真实场景，自然光。", "publishable": true}},\n'
                f'  "配图-01": {{"path": "{generated_dir / "配图-01.png"}", "provider": "packyapi", "model": "nano2", "prompt": "围绕组织责任变化生成配图，中国语境，真实纪实摄影，杂志专题图片质感，自然光。", "publishable": true}},\n'
                f'  "配图-02": {{"path": "{generated_dir / "配图-02.png"}", "provider": "packyapi", "model": "nano2", "prompt": "围绕预算路径变化生成配图，中国语境，真实纪实摄影，真实场景，不要海报感。", "publishable": true}}\n'
                '}\n',
                encoding="utf-8",
            )
            (image_dir / "图片与数据来源.md").write_text("# 图片与数据来源\n", encoding="utf-8")
            (output_dir / "公众号版-可直接发布-纯文本可复制.docx").write_bytes(b"")
            (output_dir / "公众号版-图文可发布.docx").write_bytes(b"")
            for name in ["封面", "配图-01", "配图-02"]:
                (generated_dir / f"{name}.png").write_bytes(MINI_PNG)

            report = build_image_review_report(
                {
                    "output_dir": str(output_dir),
                    "platform": "wechat",
                    "article_markdown": (
                        "# 组织接口正在被重写\n\n"
                        "真正变化的不是工具界面，而是组织里的责任关系。\n\n"
                        "预算审批和执行路径会被重新切开。\n\n"
                        "这篇文章讨论的是责任、路径和关系，不是现场照片。"
                    ),
                }
            )

            self.assertEqual(report["decision"], "revise")
            self.assertEqual(report["expanded_checks"]["content_alignment"]["generated_prompt_mismatch_count"], 3)
            self.assertTrue(any(item["issue_type"] == "generated_prompt_mismatch" for item in report["top_issues"]))

    def test_public_search_query_is_compact(self) -> None:
        query = _public_search_query(
            topic="AI漫剧量产工作流：如何用产品化思维对抗生成的不确定性",
            snippet="在 Prompt 撰写规范上，应要求团队剥离文学修辞。模型更偏爱清晰、短促、无感情色彩的物理指令。",
        )

        self.assertLessEqual(len(query), 72)
        self.assertIn("AI", query)
        self.assertTrue("AI" in query or "artificial intelligence" in query.lower())
        self.assertNotIn("在 Prompt 撰写规范上，应要求团队剥离文学修辞", query)

    def test_query_variants_for_commons_adds_fallbacks(self) -> None:
        variants = _query_variants_for_commons(
            "AI漫剧量产工作流：如何用产品化思维对抗生成的不确定性 在 Prompt 撰写规范上，应要求团队剥离文学修辞。模型更偏爱清晰、短促、无感情色彩的物理指令：推近、拉远、左移、跟拍。 真实新闻照片 公开素材 中国 现场 人物 场景"
        )

        self.assertGreaterEqual(len(variants), 2)
        self.assertNotEqual(variants[0], variants[1])
        self.assertLessEqual(len(variants[1]), 72)

    def test_search_commons_accepts_list_pages_payload(self) -> None:
        payload = {
            "query": {
                "pages": [
                    {
                        "title": "File:Robin Li (2020).png",
                        "fullurl": "https://commons.wikimedia.org/wiki/File:Robin_Li_(2020).png",
                        "imageinfo": [
                            {
                                "thumburl": "https://upload.wikimedia.org/mock-robin-li.png",
                                "descriptionurl": "https://commons.wikimedia.org/wiki/File:Robin_Li_(2020).png",
                                "extmetadata": {
                                    "LicenseShortName": {"value": "CC BY-SA 4.0"},
                                    "Artist": {"value": "Mock Author"},
                                },
                            }
                        ],
                    }
                ]
            }
        }

        with patch("writing_brain.public_images._request_json", return_value=payload):
            result = _search_commons("Robin Li")

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["title"], "File:Robin Li (2020).png")
        self.assertEqual(result[0]["license"], "CC BY-SA 4.0")
        self.assertEqual(result[0]["author"], "Mock Author")

    def test_collect_public_images_refreshes_publish_pack(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = AppConfig(data_dir=root)
            output_dir = root / "out" / "公众号"
            build_publish_pack(
                {
                    "title": "中国 AI 行业最危险的不是落后而是开始适应落后",
                    "topic": "中国 AI 行业最危险的不是落后而是开始适应落后",
                    "platform": "wechat",
                    "output_dir": str(output_dir),
                    "article_markdown": ARTICLE,
                },
                config,
            )
            generated_dir = output_dir / "图片" / "已生成"
            generated_dir.mkdir(parents=True, exist_ok=True)
            for name in ["封面", "配图-02"]:
                (generated_dir / f"{name}.png").write_bytes(MINI_PNG)
            _write_generated_index(output_dir)

            with patch("writing_brain.public_images._search_public_sources", side_effect=_mock_search), patch(
                "writing_brain.public_images._download_bytes",
                return_value=MINI_PNG,
            ):
                result = collect_public_images(
                    {
                        "output_dir": str(output_dir),
                        "article_markdown": ARTICLE,
                        "platform": "wechat",
                    },
                    config,
                )

            self.assertEqual(result["status"], "completed")
            self.assertEqual(result["refresh_result"]["status"], "completed")
            self.assertEqual(result["image_review_report"]["decision"], "pass")
            self.assertTrue((output_dir / "图片" / "公开来源" / "配图-01.png").exists())
            self.assertTrue((output_dir / "图片" / "公开来源" / "配图-03.png").exists())
            self.assertTrue((output_dir / "图片" / "公开图片索引.json").exists())

            rich_docx = output_dir / "公众号版-图文可发布.docx"
            rich = Document(rich_docx)
            self.assertEqual(len(rich.inline_shapes), 4)

            sources = (output_dir / "图片" / "图片与数据来源.md").read_text(encoding="utf-8")
            self.assertIn("## 自动检索公开图源", sources)
            self.assertIn("CC BY-SA 4.0", sources)
            self.assertIn("Mock Author", sources)

    def test_collect_public_images_removes_duplicate_public_variants_when_reusing_existing_asset(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = AppConfig(data_dir=root)
            output_dir = root / "out" / "公众号"
            build_publish_pack(
                {
                    "title": "中国 AI 行业最危险的不是落后而是开始适应落后",
                    "topic": "中国 AI 行业最危险的不是落后而是开始适应落后",
                    "platform": "wechat",
                    "output_dir": str(output_dir),
                    "article_markdown": ARTICLE,
                },
                config,
            )
            generated_dir = output_dir / "图片" / "已生成"
            public_dir = output_dir / "图片" / "公开来源"
            generated_dir.mkdir(parents=True, exist_ok=True)
            public_dir.mkdir(parents=True, exist_ok=True)
            for name in ["封面", "配图-02"]:
                (generated_dir / f"{name}.png").write_bytes(MINI_PNG)
            (public_dir / "配图-01.png").write_bytes(MINI_PNG)
            (public_dir / "配图-01.jpg").write_bytes(MINI_PNG)
            (public_dir / "配图-03.png").write_bytes(MINI_PNG)
            _write_generated_index(output_dir)

            with patch("writing_brain.public_images._search_public_sources", side_effect=_mock_search), patch(
                "writing_brain.public_images._download_bytes",
                return_value=MINI_PNG,
            ):
                result = collect_public_images(
                    {
                        "output_dir": str(output_dir),
                        "article_markdown": ARTICLE,
                        "platform": "wechat",
                    },
                    config,
                )

            self.assertEqual(result["status"], "completed")
            self.assertFalse((public_dir / "配图-01.jpg").exists())
            rich = Document(output_dir / "公众号版-图文可发布.docx")
            self.assertEqual(len(rich.inline_shapes), 4)

    def test_collect_public_images_supports_release_cycle_result(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = AppConfig(data_dir=root)
            release_root = root / "release"
            platform_results: list[dict[str, object]] = []

            for platform, name in [("wechat", "公众号"), ("zhihu", "知乎")]:
                output_dir = release_root / "投稿包" / name
                article_path = release_root / "平台稿" / f"{name}.md"
                article_path.parent.mkdir(parents=True, exist_ok=True)
                article_path.write_text(ARTICLE, encoding="utf-8")
                build_publish_pack(
                    {
                        "title": "中国 AI 行业最危险的不是落后而是开始适应落后",
                        "topic": "中国 AI 行业最危险的不是落后而是开始适应落后",
                        "platform": platform,
                        "output_dir": str(output_dir),
                        "article_markdown": ARTICLE,
                    },
                    config,
                )
                generated_dir = output_dir / "图片" / "已生成"
                generated_dir.mkdir(parents=True, exist_ok=True)
                for image_name in ["封面", "配图-02"]:
                    (generated_dir / f"{image_name}.png").write_bytes(MINI_PNG)
                _write_generated_index(output_dir)
                platform_results.append(
                    {
                        "platform": platform,
                        "platform_name": name,
                        "article_path": str(article_path),
                        "publish_result": {
                            "output_dir": str(output_dir),
                        },
                    }
                )

            with patch("writing_brain.public_images._search_public_sources", side_effect=_mock_search), patch(
                "writing_brain.public_images._download_bytes",
                return_value=MINI_PNG,
            ):
                result = collect_public_images(
                    {
                        "release_cycle_result": {
                            "output_root": str(release_root),
                            "platform_results": platform_results,
                        }
                    },
                    config,
                )

            self.assertEqual(result["scope"], "release_cycle")
            self.assertEqual(result["status"], "completed")
            self.assertEqual(len(result["platform_results"]), 2)
            self.assertTrue(all(item["status"] == "completed" for item in result["platform_results"]))
            self.assertTrue((release_root / "投稿包" / "公众号" / "图片" / "公开来源" / "配图-01.png").exists())
            self.assertTrue((release_root / "投稿包" / "知乎" / "图片" / "公开来源" / "配图-03.png").exists())

    def test_collect_public_images_retries_with_compact_query_when_original_is_too_long(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = AppConfig(data_dir=root)
            output_dir = root / "out" / "公众号"
            build_publish_pack(
                {
                    "title": "AI漫剧量产工作流：如何用产品化思维对抗生成的不确定性",
                    "topic": "AI漫剧量产工作流：如何用产品化思维对抗生成的不确定性",
                    "platform": "wechat",
                    "output_dir": str(output_dir),
                    "article_markdown": ARTICLE,
                },
                config,
            )
            generated_dir = output_dir / "图片" / "已生成"
            generated_dir.mkdir(parents=True, exist_ok=True)
            for name in ["封面", "配图-02"]:
                (generated_dir / f"{name}.png").write_bytes(MINI_PNG)
            _write_generated_index(output_dir)

            def search_side_effect(query: str) -> list[dict[str, str]]:
                if len(query) > 72:
                    return []
                return _mock_search(query)

            leads_path = output_dir / "图片" / "公开图片线索.md"
            leads_path.write_text(
                "# 公开图片线索\n\n"
                "## 配图-01\n"
                "- 用途：测试\n"
                "- 公开渠道搜索词：AI漫剧量产工作流：如何用产品化思维对抗生成的不确定性 在 Prompt 撰写规范上，应要求团队剥离文学修辞。模型更偏爱清晰、短促、无感情色彩的物理指令：推近、拉远、左移、跟拍。 真实新闻照片 公开素材 中国 现场 人物 场景\n\n"
                "## 配图-03\n"
                "- 用途：测试\n"
                "- 公开渠道搜索词：AI漫剧量产工作流：如何用产品化思维对抗生成的不确定性 在 Prompt 撰写规范上，应要求团队剥离文学修辞。模型更偏爱清晰、短促、无感情色彩的物理指令：推近、拉远、左移、跟拍。 真实新闻照片 公开素材 中国 现场 人物 场景\n",
                encoding="utf-8",
            )

            with patch("writing_brain.public_images._search_public_sources", side_effect=search_side_effect), patch(
                "writing_brain.public_images._download_bytes",
                return_value=MINI_PNG,
            ):
                result = collect_public_images(
                    {
                        "output_dir": str(output_dir),
                        "article_markdown": ARTICLE,
                        "platform": "wechat",
                    },
                    config,
                )

            self.assertEqual(result["status"], "completed")
            self.assertTrue((output_dir / "图片" / "公开来源" / "配图-01.png").exists())

    def test_post_review_pipeline_can_collect_public_images(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = AppConfig(data_dir=root)
            output_dir = root / "publish" / "公众号"

            with patch("writing_brain.public_images._search_public_sources", side_effect=_mock_search), patch(
                "writing_brain.public_images._download_bytes",
                return_value=MINI_PNG,
            ):
                result = run_post_review_pipeline(
                    {
                        "topic": "中国 AI 行业最危险的不是落后而是开始适应落后",
                        "platform": "wechat",
                        "post_review_pipeline": [
                            {"stage": "publish_pack", "input": {"output_dir": str(output_dir)}},
                            {"stage": "collect_public_images"},
                        ],
                    },
                    config,
                    run_id="post_review_public_images",
                    final_article_markdown=ARTICLE,
                    final_review_report={"decision": "pass"},
                    context_pack={"topic": "中国 AI 行业最危险的不是落后而是开始适应落后", "platform": "wechat"},
                )

            self.assertIsNotNone(result)
            assert result is not None
            self.assertIn(result["status"], {"completed", "partial"})  # partial if image review not pass
            self.assertEqual(result["stages"][1]["stage"], "collect_public_images")
            collect_stage = result["stages"][1]["result"]
            self.assertEqual(collect_stage["scope"], "publish_pack")
            self.assertEqual(collect_stage["status"], "completed")
            self.assertEqual(collect_stage["image_review_report"]["public_present"], 2)
            self.assertTrue((output_dir / "图片" / "公开来源" / "配图-01.png").exists())

    def test_image_review_blocks_unpublishable_public_images(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = AppConfig(data_dir=root)
            output_dir = root / "out" / "公众号"
            build_publish_pack(
                {
                    "title": "中国 AI 行业最危险的不是落后而是开始适应落后",
                    "topic": "中国 AI 行业最危险的不是落后而是开始适应落后",
                    "platform": "wechat",
                    "output_dir": str(output_dir),
                    "article_markdown": ARTICLE,
                },
                config,
            )
            generated_dir = output_dir / "图片" / "已生成"
            public_dir = output_dir / "图片" / "公开来源"
            generated_dir.mkdir(parents=True, exist_ok=True)
            public_dir.mkdir(parents=True, exist_ok=True)
            for name in ["封面", "配图-02"]:
                (generated_dir / f"{name}.png").write_bytes(MINI_PNG)
            for name in ["配图-01", "配图-03"]:
                (public_dir / f"{name}.png").write_bytes(MINI_PNG)
            _write_generated_index(output_dir)
            (output_dir / "图片" / PUBLIC_INDEX_FILENAME).write_text(
                '{\n'
                f'  "配图-01": {{"path": "{public_dir / "配图-01.png"}", "source_url": "", "license": "", "publishable": false}},\n'
                f'  "配图-03": {{"path": "{public_dir / "配图-03.png"}", "source_url": "https://commons.wikimedia.org/wiki/File:three", "license": "CC BY-SA 4.0", "publishable": true}}\n'
                '}\n',
                encoding="utf-8",
            )

            report = build_image_review_report({"output_dir": str(output_dir), "platform": "wechat"})

            self.assertEqual(report["decision"], "revise")
            self.assertTrue(any(item["issue_type"] == "public_publishability_blocked" for item in report["top_issues"]))

    @patch(
        "writing_brain.image_review.call_ppchat_chat",
        side_effect=[
            {
                "mode": "model_output",
                "provider": "ppchat",
                "model": "claude-opus-4-6",
                "reply_text": '{"semantic_score":92,"decision":"pass","confidence":0.9,"issue":"无显著问题","action":"可直接发布","strength":"气质克制"}',
            },
            {
                "mode": "model_output",
                "provider": "ppchat",
                "model": "gpt-5.4",
                "reply_text": '{"score":91,"decision":"pass","abnormal_review":false,"flags":[],"reason":"流程正常","recommendation":"可直接交付"}',
            },
        ],
    )
    @patch(
        "writing_brain.image_review.call_packy_chat",
        return_value={
            "mode": "model_output",
            "provider": "packyapi",
            "model": "gemini-3.1-pro-preview",
            "reply_text": '{"score":90,"decision":"pass","issue":"无显著问题","action":"可直接发布","strength":"图片与文章判断一致"}',
        },
    )
    def test_image_review_can_attach_governance_votes(self, _: object, __: object) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = AppConfig(data_dir=root)
            output_dir = root / "out" / "公众号"
            build_publish_pack(
                {
                    "title": "中国 AI 行业最危险的不是落后而是开始适应落后",
                    "topic": "中国 AI 行业最危险的不是落后而是开始适应落后",
                    "platform": "wechat",
                    "output_dir": str(output_dir),
                    "article_markdown": ARTICLE,
                },
                config,
            )
            generated_dir = output_dir / "图片" / "已生成"
            public_dir = output_dir / "图片" / "公开来源"
            generated_dir.mkdir(parents=True, exist_ok=True)
            public_dir.mkdir(parents=True, exist_ok=True)
            for name in ["封面", "配图-02"]:
                (generated_dir / f"{name}.png").write_bytes(MINI_PNG)
            for name in ["配图-01", "配图-03"]:
                (public_dir / f"{name}.png").write_bytes(MINI_PNG)
            (output_dir / "图片" / GENERATED_INDEX_FILENAME).write_text(
                '{\n'
                f'  "封面": {{"path": "{generated_dir / "封面.png"}", "provider": "packyapi", "model": "nano2", "prompt": "纪实封面图", "publishable": true}},\n'
                f'  "配图-02": {{"path": "{generated_dir / "配图-02.png"}", "provider": "packyapi", "model": "nano2", "prompt": "组织与模型关系图", "publishable": true}}\n'
                '}\n',
                encoding="utf-8",
            )
            (output_dir / "图片" / PUBLIC_INDEX_FILENAME).write_text(
                '{\n'
                f'  "配图-01": {{"path": "{public_dir / "配图-01.png"}", "title": "File:OpenAI office", "source_url": "https://commons.wikimedia.org/wiki/File:one", "license": "CC BY-SA 4.0", "author": "Author One", "publishable": true}},\n'
                f'  "配图-03": {{"path": "{public_dir / "配图-03.png"}", "title": "File:AI conference", "source_url": "https://commons.wikimedia.org/wiki/File:three", "license": "CC BY-SA 4.0", "author": "Author Three", "publishable": true}}\n'
                '}\n',
                encoding="utf-8",
            )

            report = build_image_review_report(
                {
                    "output_dir": str(output_dir),
                    "platform": "wechat",
                    "article_markdown": ARTICLE,
                    "use_image_governance_review": True,
                }
            )

            self.assertEqual(report["decision"], "pass")
            self.assertEqual(report["governance"]["mode"], "three_powers")
            self.assertTrue(report["governance"]["quorum_pass"])
            self.assertEqual(report["governance"]["decision_options"][0], "user_review_image_pass_quorum")
            self.assertIn("图片三模型审批已通过", report["required_actions"][0])

    @patch(
        "writing_brain.image_review.call_ppchat_chat",
        side_effect=[
            {
                "mode": "model_output",
                "provider": "ppchat",
                "model": "claude-opus-4-6",
                "reply_text": '{"semantic_score":62,"decision":"revise","confidence":0.9,"issue":"公开图像企业宣传照，削弱文章锋利感","action":"替换为更纪实的行业现场图","strength":"封面方向还行"}',
            },
            {
                "mode": "model_output",
                "provider": "ppchat",
                "model": "gpt-5.4",
                "reply_text": '{"score":64,"decision":"revise","abnormal_review":false,"flags":[],"reason":"两位 reviewer 都认为图片基调偏宣传","recommendation":"替换跑题图片后重跑"}',
            },
        ],
    )
    @patch(
        "writing_brain.image_review.call_packy_chat",
        return_value={
            "mode": "model_output",
            "provider": "packyapi",
            "model": "gemini-3.1-pro-preview",
            "reply_text": '{"score":60,"decision":"revise","issue":"生成图偏概念海报，不像纪实配图","action":"重写 prompt，压低海报感","strength":"结构位置合理"}',
        },
    )
    def test_image_review_governance_can_block_tone_mismatch(self, _: object, __: object) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = AppConfig(data_dir=root)
            output_dir = root / "out" / "公众号"
            build_publish_pack(
                {
                    "title": "中国 AI 行业最危险的不是落后而是开始适应落后",
                    "topic": "中国 AI 行业最危险的不是落后而是开始适应落后",
                    "platform": "wechat",
                    "output_dir": str(output_dir),
                    "article_markdown": ARTICLE,
                },
                config,
            )
            generated_dir = output_dir / "图片" / "已生成"
            public_dir = output_dir / "图片" / "公开来源"
            generated_dir.mkdir(parents=True, exist_ok=True)
            public_dir.mkdir(parents=True, exist_ok=True)
            for name in ["封面", "配图-02"]:
                (generated_dir / f"{name}.png").write_bytes(MINI_PNG)
            for name in ["配图-01", "配图-03"]:
                (public_dir / f"{name}.png").write_bytes(MINI_PNG)
            _write_generated_index(output_dir)
            (output_dir / "图片" / PUBLIC_INDEX_FILENAME).write_text(
                '{\n'
                f'  "配图-01": {{"path": "{public_dir / "配图-01.png"}", "title": "File:Brand campaign", "source_url": "https://commons.wikimedia.org/wiki/File:one", "license": "CC BY-SA 4.0", "author": "Author One", "publishable": true}},\n'
                f'  "配图-03": {{"path": "{public_dir / "配图-03.png"}", "title": "File:Product launch stage", "source_url": "https://commons.wikimedia.org/wiki/File:three", "license": "CC BY-SA 4.0", "author": "Author Three", "publishable": true}}\n'
                '}\n',
                encoding="utf-8",
            )

            report = build_image_review_report(
                {
                    "output_dir": str(output_dir),
                    "platform": "wechat",
                    "article_markdown": ARTICLE,
                    "use_image_governance_review": True,
                }
            )

            self.assertEqual(report["decision"], "revise")
            self.assertEqual(report["governance"]["consensus_decision"], "revise")
            self.assertTrue(any(item["issue_type"] == "image_tone_mismatch" for item in report["top_issues"]))
            self.assertIn("替换不贴题或过于海报化的素材", report["required_actions"][0])

    def test_search_unsplash_returns_normalized_candidates(self) -> None:
        api_response = {
            "results": [
                {
                    "alt_description": "business meeting in modern office",
                    "urls": {"regular": "https://images.unsplash.com/photo-123"},
                    "links": {"html": "https://unsplash.com/photos/123"},
                    "user": {"name": "Jane Doe"},
                }
            ]
        }
        with patch.dict("os.environ", {"UNSPLASH_ACCESS_KEY": "test-key"}), patch(
            "writing_brain.public_images._request_json", return_value=api_response
        ):
            results = _search_unsplash("business meeting")

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["title"], "business meeting in modern office")
        self.assertEqual(results[0]["license"], "Unsplash License")
        self.assertEqual(results[0]["author"], "Jane Doe")
        self.assertEqual(results[0]["page_url"], "https://unsplash.com/photos/123")
        self.assertEqual(results[0]["download_url"], "https://images.unsplash.com/photo-123")

    def test_search_unsplash_returns_empty_without_api_key(self) -> None:
        with patch.dict("os.environ", {}, clear=True):
            results = _search_unsplash("anything")
        self.assertEqual(results, [])

    def test_search_pexels_returns_normalized_candidates(self) -> None:
        api_response = {
            "photos": [
                {
                    "alt": "team collaboration",
                    "src": {"large": "https://images.pexels.com/photos/456/large.jpg"},
                    "url": "https://www.pexels.com/photo/456",
                    "photographer": "John Smith",
                }
            ]
        }
        with patch.dict("os.environ", {"PEXELS_API_KEY": "test-key"}), patch(
            "writing_brain.public_images._request_json", return_value=api_response
        ):
            results = _search_pexels("team collaboration")

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["title"], "team collaboration")
        self.assertEqual(results[0]["license"], "Pexels License")
        self.assertEqual(results[0]["author"], "John Smith")
        self.assertEqual(results[0]["page_url"], "https://www.pexels.com/photo/456")

    def test_search_pexels_returns_empty_without_api_key(self) -> None:
        with patch.dict("os.environ", {}, clear=True):
            results = _search_pexels("anything")
        self.assertEqual(results, [])

    def test_fallback_chain_stops_at_first_hit(self) -> None:
        unsplash_results = [
            {
                "title": "AI conference photo",
                "page_url": "https://unsplash.com/photos/abc",
                "download_url": "https://images.unsplash.com/photo-abc",
                "license": "Unsplash License",
                "author": "Unsplash Author",
            }
        ]
        with patch("writing_brain.public_images._search_unsplash", return_value=unsplash_results), patch(
            "writing_brain.public_images._search_pexels", return_value=[]
        ) as mock_pexels, patch(
            "writing_brain.public_images._search_commons", return_value=[]
        ) as mock_commons:
            from writing_brain.public_images import _search_public_sources

            results = _search_public_sources("AI meeting")

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["license"], "Unsplash License")
        mock_pexels.assert_not_called()
        mock_commons.assert_not_called()


if __name__ == "__main__":
    unittest.main()
