from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from writing_brain.config import load_config
from writing_brain.pipelines.quality_session import (
    build_article_diagnosis,
    build_delivery,
    build_quality_evaluation,
    build_research_pack,
    run_quality_session,
)


class QualitySessionTests(unittest.TestCase):
    @patch("writing_brain.pipelines.quality_session.run_post_review_pipeline")
    @patch("writing_brain.pipelines.contracts.build_review_report")
    def test_run_quality_session_persists_new_stage_artifacts(self, mock_review: object, mock_post_review: object) -> None:
        mock_review.return_value = {
            "contract_name": "review_report",
            "contract_version": "v1",
            "review_id": "review_quality_session",
            "run_id": "quality_session_demo",
            "decision": "pass",
            "total_score": 92,
            "lazy_index": 1,
            "expanded_checks": {
                "argument": {"ok": True},
                "formatting": {"ok": True},
            },
            "top_issues": [{"issue_type": "no_major_issue", "severity": "low", "summary": "未发现显著问题", "evidence": "ok"}],
            "rewrite_actions": ["当前稿件可直接进入人工复核"],
            "strengths": ["整体已经接近可发布状态"],
            "review_mode": "heuristic_only",
            "review_layers": {},
        }

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = load_config(tmp)
            mock_post_review.return_value = {
                "contract_name": "post_review_pipeline_result",
                "contract_version": "v1",
                "run_id": "quality_session_demo",
                "status": "completed",
                "stages": [],
                "artifact_refs": [str(root / "sessions" / "quality_session_demo.post-review.stage.json")],
                "recommended_next_actions": ["可进入最终验收。"],
            }
            result = run_quality_session(
                {
                    "run_id": "quality_session_demo",
                    "topic": "为什么老板总会被通用 Agent 打动",
                    "platform": "wechat",
                    "user_goal": "写一篇公众号行业分析稿",
                    "manual_draft_text": (
                        "# 为什么老板总会被通用 Agent 打动\n\n"
                        "老板们被通用 Agent 打动，不是因为它今天已经最好用，而是因为它把“少雇人、少协调、直接拿结果”的幻想做成了可演示的产品。\n\n"
                        "一个很直接的例子是，同样面对预算审批，通用 Agent 展示的是结果感，企业智能体展示的却常常是流程优化。\n\n"
                        "这意味着老板在第一眼里看到的，不是长期 ROI，而是组织摩擦是否可以被瞬间抹平。\n\n"
                        "如果不把这层心理预期看清，企业智能体团队就会一直用正确但不打动人的方式卖方案。"
                    ),
                },
                config,
            )

            self.assertEqual(result["contract_name"], "session_start_result")
            self.assertEqual(result["contract_version"], "v2")
            self.assertEqual(result["quality_evaluation"]["decision"], "pass")
            self.assertTrue((root / "sessions" / "quality_session_demo.assignment.json").exists())
            self.assertTrue((root / "sessions" / "quality_session_demo.research.json").exists())
            self.assertTrue((root / "sessions" / "quality_session_demo.diagnosis.json").exists())
            self.assertTrue((root / "sessions" / "quality_session_demo.blueprint.json").exists())
            self.assertTrue((root / "sessions" / "quality_session_demo.quality.json").exists())
            self.assertTrue((root / "sessions" / "quality_session_demo.image-brief.json").exists())
            self.assertTrue((root / "sessions" / "quality_session_demo.delivery.json").exists())
            self.assertTrue((root / "sessions" / "quality_session_demo.post-review.json").exists())
            saved = json.loads((root / "sessions" / "quality_session_demo.image-brief.json").read_text(encoding="utf-8"))
            self.assertEqual(saved["contract_name"], "image_brief")
            self.assertEqual(saved["slots"][0]["role"], "cover_opinion")
            self.assertEqual(result["post_review_result"]["status"], "completed")
            self.assertEqual(mock_post_review.call_args.kwargs["run_id"], "quality_session_demo")
            self.assertEqual(mock_post_review.call_args.args[0]["target_platforms"], ["wechat"])

    def test_build_delivery_blocks_rich_delivery_when_image_gate_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = load_config(tmp)
            result = build_delivery(
                {
                    "run_id": "delivery_gate_demo",
                    "assignment": {"topic": "AI 写作系统", "platform": "wechat", "title": "AI 写作系统"},
                    "article_markdown": "# AI 写作系统\n\n先给判断。\n\n因为这里有具体例子。\n\n所以先过正文质量门。",
                    "quality_evaluation": {
                        "can_continue_to_delivery": False,
                        "blocked_dimensions": ["voice"],
                    },
                    "image_brief": {"slots": []},
                },
                config,
            )

            self.assertEqual(result["status"], "blocked")
            self.assertEqual(result["text_delivery"]["status"], "blocked")
            self.assertEqual(result["rich_delivery"]["status"], "blocked")
            self.assertIn("voice", result["blocked_reasons"])

    def test_diagnosis_blocks_when_evidence_without_claims(self) -> None:
        assignment = {"topic": "test", "user_goal": "test", "must_cover_points": ["a"]}
        research_pack = {
            "claim_items": [],
            "evidence_items": [{"need": "some evidence"}],
            "evidence_gap_count": 1,
        }
        diagnosis = build_article_diagnosis({}, assignment=assignment, research_pack=research_pack)
        self.assertFalse(diagnosis["ready_for_compose"])

    def test_diagnosis_allows_when_no_evidence(self) -> None:
        assignment = {"topic": "test", "user_goal": "test", "must_cover_points": []}
        research_pack = {"claim_items": [], "evidence_items": [], "evidence_gap_count": 0}
        diagnosis = build_article_diagnosis({}, assignment=assignment, research_pack=research_pack)
        self.assertTrue(diagnosis["ready_for_compose"])

    def test_research_quality_draft_when_claims_missing(self) -> None:
        context_pack = {"core_claims": [], "evidence_needs": ["need1"]}
        assignment = {"topic": "test", "evidence_needs": ["need1"]}
        research_pack = build_research_pack({}, context_pack=context_pack, assignment=assignment)
        self.assertEqual(research_pack["research_quality"], "draft_quality")

    @patch("writing_brain.pipelines.contracts.build_review_report")
    def test_fallback_article_fails_source_gate(self, mock_review: object) -> None:
        mock_review.return_value = {
            "contract_name": "review_report",
            "contract_version": "v1",
            "decision": "pass",
            "total_score": 92,
            "lazy_index": 1,
            "expanded_checks": {"argument": {"ok": True}, "formatting": {"ok": True}},
            "top_issues": [],
            "rewrite_actions": [],
            "strengths": [],
            "review_mode": "heuristic_only",
            "review_layers": {},
        }
        result = build_quality_evaluation(
            {},
            assignment={"topic": "test", "platform": "wechat"},
            research_pack={"evidence_items": []},
            diagnosis={"ready_for_compose": True},
            blueprint={"must_cover_points": ["a"]},
            article_markdown="# Test\n\nPara 1.\n\nPara 2.\n\nPara 3.\n\nPara 4.",
            article_source="blueprint_fallback",
            context_pack={},
        )
        self.assertIn("source", result["blocked_dimensions"])
        self.assertNotEqual(result["decision"], "pass")

    @patch("writing_brain.pipelines.contracts.build_review_report")
    def test_manual_input_passes_source_gate(self, mock_review: object) -> None:
        mock_review.return_value = {
            "contract_name": "review_report",
            "contract_version": "v1",
            "decision": "pass",
            "total_score": 92,
            "lazy_index": 1,
            "expanded_checks": {"argument": {"ok": True}, "formatting": {"ok": True}},
            "top_issues": [],
            "rewrite_actions": [],
            "strengths": [],
            "review_mode": "heuristic_only",
            "review_layers": {},
        }
        result = build_quality_evaluation(
            {},
            assignment={"topic": "test", "platform": "wechat"},
            research_pack={"evidence_items": []},
            diagnosis={"ready_for_compose": True},
            blueprint={"must_cover_points": ["a"]},
            article_markdown="# Test\n\nPara 1.\n\nPara 2.\n\nPara 3.\n\nPara 4.",
            article_source="manual_input",
            context_pack={},
        )
        self.assertNotIn("source", result["blocked_dimensions"])

    @patch("writing_brain.pipelines.contracts.build_review_report")
    def test_evidence_gate_scales_with_must_cover(self, mock_review: object) -> None:
        mock_review.return_value = {
            "contract_name": "review_report",
            "contract_version": "v1",
            "decision": "pass",
            "total_score": 92,
            "lazy_index": 1,
            "expanded_checks": {"argument": {"ok": True}, "formatting": {"ok": True}},
            "top_issues": [],
            "rewrite_actions": [],
            "strengths": [],
            "review_mode": "heuristic_only",
            "review_layers": {},
        }
        # Article with only 1 evidence signal but 4 must_cover_points and 4 evidence_items
        result = build_quality_evaluation(
            {},
            assignment={"topic": "test", "platform": "wechat"},
            research_pack={"evidence_items": [{"n": 1}, {"n": 2}, {"n": 3}, {"n": 4}]},
            diagnosis={"ready_for_compose": True},
            blueprint={"must_cover_points": ["a", "b", "c", "d"]},
            article_markdown="# Test\n\nJust a claim.\n\nAnother claim.\n\nYet another.\n\nFinal claim.",
            article_source="model_output",
            context_pack={},
        )
        self.assertIn("evidence", result["blocked_dimensions"])


if __name__ == "__main__":
    unittest.main()
