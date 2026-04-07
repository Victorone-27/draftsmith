from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from writing_brain.config import load_config
from writing_brain.release import run_release_cycle


def _pass_review_report() -> dict[str, object]:
    return {
        "contract_name": "review_report",
        "contract_version": "v1",
        "review_id": "review_release",
        "run_id": "release_cycle",
        "decision": "pass",
        "total_score": 90,
        "lazy_index": 1,
        "score_breakdown": {
            "alignment": 25,
            "completeness": 18,
            "evidence": 14,
            "structure": 13,
            "style_fit": 12,
            "platform_fit": 8,
        },
        "top_issues": [{"issue_type": "no_major_issue", "severity": "low", "summary": "未发现显著问题", "evidence": "ok"}],
        "rewrite_actions": ["当前稿件可直接进入人工复核"],
        "strengths": ["整体已经接近可发布状态"],
    }


def _expanded_article() -> str:
    paragraphs = []
    for index in range(1, 8):
        paragraphs.append(
            f"第{index}段先把判断立住：如果行业过早接受次优模型是够用的，那么组织会逐步围绕低能力上限重新设计流程。"
            f"因为一旦默认上限降低，团队会把精力放在修补局部效率，而不是追问下一代能力能否重写分工。"
            f"比如产品会把更多时间花在拆提示词、补人工兜底和做结果校验上，而不是重构信息流和决策流。"
            f"这会让业务对真正的能力跃迁越来越迟钝，最后连反例出现时也只会把它理解成短期波动。"
        )
    return "# 平台版标题\n\n" + "\n\n".join(paragraphs)


class ReleaseTests(unittest.TestCase):
    @patch("writing_brain.release.build_publish_pack")
    @patch("writing_brain.release.build_review_report")
    @patch("writing_brain.release.run_writer_turn")
    def test_release_cycle_expands_summary_like_platform_article(
        self,
        mock_writer_turn: object,
        mock_review: object,
        mock_publish_pack: object,
    ) -> None:
        mock_writer_turn.return_value = {
            "contract_name": "writer_response",
            "contract_version": "v1",
            "run_id": "release_expand_wechat",
            "task_mode": "revise",
            "mode": "model_output",
            "provider": "packyapi",
            "model": "gemini-3.1-pro-preview",
            "reply_text": _expanded_article(),
            "context_pack": {},
            "recommended_next_actions": [],
        }
        mock_review.side_effect = [_pass_review_report(), _pass_review_report()]
        mock_publish_pack.return_value = {
            "contract_name": "publish_pack_result",
            "contract_version": "v1",
            "output_dir": "",
            "image_review_report": {"decision": "pass"},
        }

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = load_config(str(root))
            result = run_release_cycle(
                {
                    "run_id": "release_expand",
                    "topic": "中国 AI 行业真正危险的不是模型暂时落后",
                    "platforms": ["zhihu"],
                    "source_platform": "wechat",
                    "article_markdown": (
                        "# 中国 AI 行业真正危险的不是模型暂时落后\n\n"
                        "先说结论，真正危险的不是短期差距，而是行业开始默认差一点也够用。\n\n"
                        "一旦默认这个前提，很多人就会围绕低上限重新理解机会。\n\n"
                        "最后大家会在错误的能力边界里优化自己。"
                    ),
                },
                config,
            )
            platform_result = next(r for r in result["platform_results"] if r["platform"] != "wechat")
            saved_text = Path(platform_result["article_path"]).read_text(encoding="utf-8")

        self.assertEqual(mock_writer_turn.call_count, 1)
        self.assertIn("Zhihu", mock_writer_turn.call_args.args[0]["user_message"])
        self.assertEqual(platform_result["final_decision"], "pass")
        self.assertTrue(platform_result["completeness_requirement_met"])
        self.assertGreaterEqual(platform_result["final_char_count"], 1200)
        self.assertEqual(platform_result["final_paragraph_count"], 7)
        self.assertGreaterEqual(platform_result["final_evidence_signals"], 2)
        self.assertEqual(platform_result["completeness_reasons"], [])
        self.assertIn("第1段先把判断立住", saved_text)

    @patch("writing_brain.release.build_publish_pack")
    @patch("writing_brain.release.build_review_report")
    def test_release_cycle_normalizes_chinese_platform_aliases(
        self,
        mock_review: object,
        mock_publish_pack: object,
    ) -> None:
        mock_review.return_value = _pass_review_report()
        mock_publish_pack.return_value = {
            "contract_name": "publish_pack_result",
            "contract_version": "v1",
            "output_dir": "",
            "artifact_refs": [
                "/tmp/平台版-可直接发布-纯文本可复制.docx",
                "/tmp/平台版-图文可发布.docx",
            ],
            "image_review_report": {"decision": "pass"},
        }

        article = "# 标题\n\n先说判断。\n\n再补原因。\n\n然后讲边界。\n\n最后收束。"
        with tempfile.TemporaryDirectory() as tmp:
            config = load_config(tmp)
            result = run_release_cycle(
                {
                    "run_id": "release_aliases",
                    "topic": "平台别名测试",
                    "platforms": ["公众号", "知乎"],
                    "source_platform": "公众号",
                    "article_markdown": article,
                    "manual_platform_articles": {
                        "公众号": article,
                        "知乎": article,
                    },
                    "auto_revise": False,
                },
                config,
            )

        self.assertEqual([item["platform"] for item in result["platform_results"]], ["wechat", "zhihu"])
        self.assertEqual([item["platform_name"] for item in result["platform_results"]], ["公众号", "知乎"])
        # source platform built once before loop + each platform rebuilt after image generation
        build_platforms = [call.args[0]["platform"] for call in mock_publish_pack.call_args_list]
        self.assertEqual(build_platforms[:2], ["wechat", "zhihu"])
        self.assertTrue(all(p in {"wechat", "zhihu"} for p in build_platforms))
