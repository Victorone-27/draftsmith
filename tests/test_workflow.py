from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from writing_brain.config import load_config
from writing_brain.workflow import run_draft_cycle


MINI_PNG = (
    b"\x89PNG\r\n\x1a\n"
    b"\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x02\x00\x00\x00\x90wS\xde"
    b"\x00\x00\x00\x0cIDAT\x08\x99c\xf8\xcf\xc0\x00\x00\x03\x01\x01\x00\xc9\xfe\x92\xef"
    b"\x00\x00\x00\x00IEND\xaeB`\x82"
)


def _mock_public_image_search(query: str) -> list[dict[str, str]]:
    return [
        {
            "title": f"{query[:18]} 图",
            "page_url": f"https://commons.wikimedia.org/wiki/{abs(hash(query))}",
            "download_url": "https://upload.wikimedia.org/mock-image.png",
            "license": "CC BY-SA 4.0",
            "author": "Workflow Test",
        }
    ]


class WorkflowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.search_patcher = patch(
            "writing_brain.public_images._search_commons",
            side_effect=_mock_public_image_search,
        )
        self.download_patcher = patch(
            "writing_brain.public_images._download_bytes",
            return_value=MINI_PNG,
        )
        self.search_patcher.start()
        self.download_patcher.start()
        self.addCleanup(self.search_patcher.stop)
        self.addCleanup(self.download_patcher.stop)

    @patch(
        "writing_brain.writer._call_writer_model",
        side_effect=[
            {
                "mode": "model_output",
                "reply_text": "先说一个大背景。\n\n值得注意的是，技术变化很快。\n\n总的来说，我们要拥抱变化。",
            },
            {
                "mode": "model_output",
                "reply_text": (
                    "业务理解会重新变得更稀缺，AI 先拉平执行层。\n\n"
                    "因为执行层会先被模型和流程工具标准化，一个很直接的例子是需求整理、信息归纳、初版方案这些通用动作先被压缩。\n\n"
                    "这不意味着业务不重要，反而意味着真正的差异回到场景判断、资源协调和取舍能力。\n\n"
                    "比如同样做 AI 项目，懂行业约束的人更容易把方案落到可执行的业务动作上。"
                ),
            },
        ],
    )
    def test_draft_cycle_reviews_revises_and_ingests(self, _: object) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = load_config(str(root))
            result = run_draft_cycle(
                {
                    "run_id": "cycle_demo",
                    "topic": "AI coding 与业务理解",
                    "platform": "wechat",
                    "user_goal": "写一篇公众号观点文",
                    "user_message": "帮我先出一版，再自己审一轮。",
                    "must_cover_points": ["业务理解会重新变得更稀缺", "AI 先拉平执行层"],
                    "max_revision_rounds": 1,
                    "finalize": True,
                    "human_feedback": {
                        "approved_points": ["这次开头判断够直接"],
                        "criticized_points": ["初版铺垫太多"],
                    },
                },
                config,
            )

            self.assertEqual(result["contract_name"], "draft_cycle_result")
            self.assertEqual(result["status"], "ingested")
            self.assertEqual(result["draft_source"], "model_output")
            self.assertEqual(result["revised_source"], "model_output")
            self.assertEqual(result["draft_review_report"]["decision"], "rewrite")
            self.assertIn(result["final_review_report"]["decision"], {"pass", "revise", "rewrite"})
            self.assertGreaterEqual(
                result["final_review_report"]["total_score"],
                result["draft_review_report"]["total_score"],
            )
            self.assertIn("业务理解会重新变得更稀缺", result["final_article_markdown"])
            self.assertTrue((root / "sessions" / "cycle_demo.cycle.json").exists())
            self.assertTrue((root / "sessions" / "cycle_demo.draft.md").exists())
            self.assertTrue((root / "sessions" / "cycle_demo.revised.md").exists())
            self.assertTrue((root / "sessions" / "cycle_demo.memory.json").exists())

    @patch("writing_brain.workflow.build_review_report")
    def test_draft_cycle_does_not_ingest_memory_before_publish_confirmation(self, mock_review: object) -> None:
        mock_review.return_value = {
            "contract_name": "review_report",
            "contract_version": "v1",
            "review_id": "review_cycle_no_ingest_draft",
            "run_id": "cycle_no_ingest",
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
            "review_mode": "hybrid",
            "review_layers": {},
        }
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = load_config(str(root))
            result = run_draft_cycle(
                {
                    "run_id": "cycle_no_ingest",
                    "topic": "AI coding 与业务理解",
                    "platform": "wechat",
                    "user_goal": "写一篇公众号观点文",
                    "force_prompt_only": True,
                    "manual_draft_text": "先说结论。\n\nAI 先拉平的是执行层。\n\n例如通用动作会被工具压缩。",
                    "human_feedback": {
                        "approved_points": ["这次开头判断够直接"],
                        "criticized_points": ["初版铺垫太多"],
                    },
                },
                config,
            )

            self.assertEqual(result["final_decision"], "pass")
            self.assertIsNone(result["memory_record"])
            self.assertFalse((root / "sessions" / "cycle_no_ingest.memory.json").exists())
            self.assertIn("确认已发布后", "".join(result["recommended_next_actions"]))

    @patch("writing_brain.workflow.build_review_report")
    def test_draft_cycle_forwards_model_reviewer_flag(self, mock_review: object) -> None:
        mock_review.side_effect = [
            {
                "contract_name": "review_report",
                "contract_version": "v1",
                "review_id": "review_cycle_flag_draft",
                "run_id": "cycle_flag",
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
                "review_mode": "hybrid",
                "review_layers": {},
            }
        ]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = load_config(str(root))
            result = run_draft_cycle(
                {
                    "run_id": "cycle_flag",
                    "topic": "AI coding 与业务理解",
                    "platform": "wechat",
                    "user_goal": "写一篇公众号观点文",
                    "force_prompt_only": True,
                    "manual_draft_text": "先说结论。\n\nAI 先拉平的是执行层。\n\n例如通用动作会被工具压缩。",
                    "use_model_reviewer": True,
                },
                config,
            )

            self.assertEqual(result["final_decision"], "pass")
            first_call_payload = mock_review.call_args_list[0].args[0]
            self.assertTrue(first_call_payload["use_model_reviewer"])

    @patch("writing_brain.workflow.build_review_report")
    def test_draft_cycle_runs_post_review_publish_pipeline_after_pass(self, mock_review: object) -> None:
        mock_review.return_value = {
            "contract_name": "review_report",
            "contract_version": "v1",
            "review_id": "review_cycle_publish_draft",
            "run_id": "cycle_publish",
            "decision": "pass",
            "total_score": 92,
            "lazy_index": 0,
            "score_breakdown": {
                "alignment": 25,
                "completeness": 18,
                "evidence": 14,
                "structure": 13,
                "style_fit": 12,
                "platform_fit": 10,
            },
            "top_issues": [{"issue_type": "no_major_issue", "severity": "low", "summary": "未发现显著问题", "evidence": "ok"}],
            "rewrite_actions": ["当前稿件可直接进入人工复核"],
            "strengths": ["整体已经接近可发布状态"],
        }
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = load_config(str(root))
            result = run_draft_cycle(
                {
                    "run_id": "cycle_publish",
                    "topic": "中国 AI 行业最危险的不是落后而是开始适应落后",
                    "platform": "wechat",
                    "user_goal": "写一篇公众号观点文",
                    "force_prompt_only": True,
                    "manual_draft_text": "# 中国 AI 行业最危险的不是落后而是开始适应落后\n\n先说结论。\n\n入口重要，但模型上限决定谁定义明天。\n\n长期不用最强模型的人，会误判未来。",
                },
                config,
            )

            self.assertEqual(result["final_decision"], "pass")
            self.assertEqual(result["post_review_result"]["status"], "completed")
            self.assertEqual(result["post_review_result"]["stages"][1]["stage"], "collect_public_images")
            publish_artifacts = result["post_review_result"]["artifact_refs"]
            self.assertTrue(any(path.endswith("可直接发布-纯文本可复制.docx") for path in publish_artifacts))
            pure_docx = next(path for path in publish_artifacts if path.endswith("可直接发布-纯文本可复制.docx"))
            self.assertTrue(Path(pure_docx).exists())
            self.assertEqual(
                result["post_review_result"]["stages"][0]["result"]["image_review_report"]["decision"],
                "revise",
            )
            self.assertIn("Word 文档复制发布", "".join(result["recommended_next_actions"]))

    @patch("writing_brain.workflow.build_review_report")
    def test_draft_cycle_debug_post_review_writes_into_session_dir_and_skips_public_image_collection(self, mock_review: object) -> None:
        mock_review.return_value = {
            "contract_name": "review_report",
            "contract_version": "v1",
            "review_id": "review_cycle_debug_publish",
            "run_id": "cycle_debug_publish",
            "decision": "pass",
            "total_score": 92,
            "lazy_index": 0,
            "score_breakdown": {
                "length": 12,
                "coverage": 20,
                "depth": 16,
                "argument": 16,
                "structure": 8,
                "style_fit": 5,
                "formatting": 9,
            },
            "expanded_checks": {},
            "top_issues": [{"issue_type": "no_major_issue", "severity": "low", "summary": "未发现显著问题", "evidence": "ok"}],
            "rewrite_actions": ["当前稿件可直接进入人工复核"],
            "strengths": ["整体已经接近可发布状态"],
        }
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = load_config(str(root))
            result = run_draft_cycle(
                {
                    "run_id": "cycle_debug_publish",
                    "topic": "中国 AI 行业最危险的不是落后而是开始适应落后",
                    "platform": "wechat",
                    "user_goal": "写一篇公众号观点文",
                    "post_review_profile": "debug",
                    "force_prompt_only": True,
                    "manual_draft_text": "# 中国 AI 行业最危险的不是落后而是开始适应落后\n\n先说结论。\n\n入口重要，但模型上限决定谁定义明天。\n\n长期不用最强模型的人，会误判未来。",
                },
                config,
            )

            self.assertEqual(result["post_review_result"]["status"], "completed")
            self.assertEqual(len(result["post_review_result"]["stages"]), 1)
            self.assertEqual(result["post_review_result"]["stages"][0]["stage"], "publish_pack")
            output_dir = Path(result["post_review_result"]["stages"][0]["result"]["output_dir"])
            self.assertTrue(output_dir.exists())
            self.assertTrue(str(output_dir).startswith(str(root / "sessions" / "cycle_debug_publish")))
            self.assertFalse((root / "publish-packs").exists())

    @patch("writing_brain.workflow.build_review_report")
    def test_draft_cycle_uses_release_cycle_for_multiple_target_platforms(self, mock_review: object) -> None:
        mock_review.return_value = {
            "contract_name": "review_report",
            "contract_version": "v1",
            "review_id": "review_cycle_multi_platform_draft",
            "run_id": "cycle_multi_platform",
            "decision": "pass",
            "total_score": 92,
            "lazy_index": 0,
            "score_breakdown": {
                "alignment": 25,
                "completeness": 18,
                "evidence": 14,
                "structure": 13,
                "style_fit": 12,
                "platform_fit": 10,
            },
            "top_issues": [{"issue_type": "no_major_issue", "severity": "low", "summary": "未发现显著问题", "evidence": "ok"}],
            "rewrite_actions": ["当前稿件可直接进入人工复核"],
            "strengths": ["整体已经接近可发布状态"],
        }
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = load_config(str(root))
            result = run_draft_cycle(
                {
                    "run_id": "cycle_multi_platform",
                    "topic": "中国 AI 行业最危险的不是落后而是开始适应落后",
                    "platform": "wechat",
                    "target_platforms": ["wechat", "zhihu"],
                    "user_goal": "写一篇公众号观点文",
                    "auto_revise": False,
                    "force_prompt_only": True,
                    "manual_draft_text": "# 中国 AI 行业最危险的不是落后而是开始适应落后\n\n先说结论。\n\n入口重要，但模型上限决定谁定义明天。\n\n长期不用最强模型的人，会误判未来。",
                    "manual_platform_articles": {
                        "zhihu": "# 中国 AI 行业最危险的不是落后而是开始适应落后\n\n知乎版第一段。\n\n知乎版第二段。"
                    },
                },
                config,
            )

            self.assertEqual(result["final_decision"], "pass")
            self.assertEqual(result["post_review_result"]["stages"][0]["stage"], "release_cycle")
            self.assertEqual(result["post_review_result"]["stages"][1]["stage"], "collect_public_images")
            output_root = result["post_review_result"]["stages"][0]["result"]["output_root"]
            self.assertTrue(Path(output_root).exists())
            self.assertTrue((Path(output_root) / "投稿包" / "公众号").exists())
            self.assertTrue((Path(output_root) / "投稿包" / "知乎").exists())
            self.assertIn("image_review_report", result["post_review_result"]["stages"][0]["result"]["platform_results"][0])

    @patch("writing_brain.workflow.build_review_report")
    def test_draft_cycle_exposes_governance_bundle(self, mock_review: object) -> None:
        mock_review.side_effect = [
            {
                "contract_name": "review_report",
                "contract_version": "v1",
                "review_id": "review_cycle_governance_draft",
                "run_id": "cycle_governance",
                "decision": "pass",
                "total_score": 93,
                "lazy_index": 0,
                "score_breakdown": {},
                "top_issues": [{"issue_type": "no_major_issue", "severity": "low", "summary": "未发现显著问题", "evidence": "ok"}],
                "rewrite_actions": ["当前稿件可直接进入人工复核"],
                "strengths": ["整体已经接近可发布状态"],
                "governance": {
                    "mode": "three_powers",
                    "user_authority": "final_veto",
                    "policy": {
                        "primary_reviewer": "claude",
                        "secondary_reviewer": "gemini",
                        "procedural_reviewer": "gpt5_4_ppchat",
                        "codex_role": "chancellor_only",
                    },
                    "claude_review": {"valid_vote": True, "decision": "pass", "score": 94},
                    "gemini_review": {"valid_vote": True, "decision": "pass", "score": 92},
                    "gpt_review": {"valid_vote": True, "decision": "pass", "score": 95},
                    "procedural_review": {"valid_vote": True, "decision": "pass", "score": 95, "abnormal_review": False, "flags": []},
                    "procedural_flags": [],
                    "decision_ready": True,
                    "quorum_pass": True,
                    "requires_user_arbitration": False,
                    "decision_options": ["user_review_pass_quorum"],
                },
            }
        ]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = load_config(str(root))
            result = run_draft_cycle(
                {
                    "run_id": "cycle_governance",
                    "topic": "AI coding 与业务理解",
                    "platform": "wechat",
                    "user_goal": "写一篇公众号观点文",
                    "force_prompt_only": True,
                    "manual_draft_text": "先说结论。\n\nAI 先拉平的是执行层。\n\n例如通用动作会被工具压缩。",
                    "use_model_reviewer": True,
                    "use_governance_review": True,
                },
                config,
            )

            self.assertEqual(result["draft_review_governance"]["mode"], "three_powers")
            self.assertEqual(result["final_review_governance"]["decision_options"], ["user_review_pass_quorum"])
            self.assertEqual(result["governance_policy"]["codex_role"], "chancellor_only")
            self.assertEqual(result["governance_policy"]["procedural_reviewer"], "gpt5_4_ppchat")
            self.assertIn("三方中已有至少两方判定通过", result["recommended_next_actions"][0])

    @patch("writing_brain.governance.build_article_review_vote")
    @patch("writing_brain.workflow.build_review_report")
    @patch("writing_brain.revision.run_writer_turn")
    @patch("writing_brain.workflow.run_writer_turn")
    def test_rotation_governance_stops_after_claude_gate_pass(
        self,
        mock_writer_turn: object,
        mock_revision_writer_turn: object,
        mock_review: object,
        mock_vote: object,
    ) -> None:
        mock_writer_turn.return_value = {
            "contract_name": "writer_response",
            "contract_version": "v1",
            "run_id": "cycle_rotation_pass",
            "task_mode": "draft",
            "mode": "prompt_only",
            "provider": "prompt_only",
            "model": "",
            "reply_text": "draft skipped",
            "context_pack": {},
            "recommended_next_actions": [],
        }
        mock_revision_writer_turn.return_value = {
            "contract_name": "writer_response",
            "contract_version": "v1",
            "run_id": "cycle_rotation_pass",
            "task_mode": "revise",
            "revision_owner": "gemini",
            "mode": "model_output",
            "provider": "packyapi",
            "model": "gemini-3.1-pro-preview",
            "reply_text": "# 标题\n\nGemini 修稿版本。\n\n第二段。\n\n第三段。\n\n第四段。\n\n第五段。",
            "context_pack": {},
            "recommended_next_actions": [],
        }
        mock_review.return_value = {
            "contract_name": "review_report",
            "contract_version": "v1",
            "review_id": "review_cycle_rotation_pass_draft",
            "run_id": "cycle_rotation_pass",
            "decision": "revise",
            "total_score": 78,
            "lazy_index": 1,
            "score_breakdown": {},
            "top_issues": [{"issue_type": "model_issue", "severity": "medium", "summary": "需要修稿", "evidence": "x"}],
            "rewrite_actions": ["补强逻辑"],
            "strengths": [],
        }
        mock_vote.return_value = {
            "reviewer": "claude",
            "provider": "ppchat",
            "model": "claude-opus-4-6",
            "mode": "model_output",
            "valid_vote": True,
            "score": 90,
            "decision": "pass",
            "confidence": 0.8,
            "issue": "",
            "action": "",
            "strength": "结构清晰",
            "error": "",
            "raw_output": "{}",
        }

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = load_config(str(root))
            result = run_draft_cycle(
                {
                    "run_id": "cycle_rotation_pass",
                    "topic": "AI coding 与业务理解",
                    "platform": "wechat",
                    "user_goal": "写一篇公众号观点文",
                    "force_prompt_only": True,
                    "manual_draft_text": "# 标题\n\n初稿。\n\n第二段。\n\n第三段。\n\n第四段。\n\n第五段。",
                    "use_governance_review": True,
                    "governance_workflow_mode": "rotation_v2",
                },
                config,
            )

            self.assertEqual(result["revision_rounds"][0]["revision_owner"], "gemini")
            self.assertEqual(result["final_review_governance"]["mode"], "writer_recusal_rotation")
            self.assertTrue(result["final_review_governance"]["quorum_pass"])
            self.assertEqual(result["final_review_governance"]["final_stage"]["stage_type"], "claude_gate")
            self.assertEqual(result["next_revision_owner"], "")
            self.assertIn("当前轮值治理已通过", result["recommended_next_actions"][0])

    @patch("writing_brain.governance.build_article_review_vote")
    @patch("writing_brain.workflow.build_review_report")
    @patch("writing_brain.revision.run_writer_turn")
    @patch("writing_brain.workflow.run_writer_turn")
    def test_rotation_governance_requires_user_after_full_cycle(
        self,
        mock_writer_turn: object,
        mock_revision_writer_turn: object,
        mock_review: object,
        mock_vote: object,
    ) -> None:
        mock_writer_turn.return_value = {
            "contract_name": "writer_response",
            "contract_version": "v1",
            "run_id": "cycle_rotation_user",
            "task_mode": "draft",
            "mode": "prompt_only",
            "provider": "prompt_only",
            "model": "",
            "reply_text": "draft skipped",
            "context_pack": {},
            "recommended_next_actions": [],
        }
        mock_revision_writer_turn.side_effect = [
            {
                "contract_name": "writer_response",
                "contract_version": "v1",
                "run_id": "cycle_rotation_user",
                "task_mode": "revise",
                "revision_owner": "gemini",
                "mode": "model_output",
                "provider": "packyapi",
                "model": "gemini-3.1-pro-preview",
                "reply_text": "# 标题\n\nGemini 版一。\n\n第二段。\n\n第三段。\n\n第四段。\n\n第五段。",
                "context_pack": {},
                "recommended_next_actions": [],
            },
            {
                "contract_name": "writer_response",
                "contract_version": "v1",
                "run_id": "cycle_rotation_user",
                "task_mode": "revise",
                "revision_owner": "gemini",
                "mode": "model_output",
                "provider": "packyapi",
                "model": "gemini-3.1-pro-preview",
                "reply_text": "# 标题\n\nGemini 版二。\n\n第二段。\n\n第三段。\n\n第四段。\n\n第五段。",
                "context_pack": {},
                "recommended_next_actions": [],
            },
            {
                "contract_name": "writer_response",
                "contract_version": "v1",
                "run_id": "cycle_rotation_user",
                "task_mode": "revise",
                "revision_owner": "gpt5_4",
                "mode": "model_output",
                "provider": "ppchat",
                "model": "gpt-5.4",
                "reply_text": "# 标题\n\ngpt 版。\n\n第二段。\n\n第三段。\n\n第四段。\n\n第五段。",
                "context_pack": {},
                "recommended_next_actions": [],
            },
            {
                "contract_name": "writer_response",
                "contract_version": "v1",
                "run_id": "cycle_rotation_user",
                "task_mode": "revise",
                "revision_owner": "claude",
                "mode": "model_output",
                "provider": "ppchat",
                "model": "claude-opus-4-6",
                "reply_text": "# 标题\n\nClaude 版。\n\n第二段。\n\n第三段。\n\n第四段。\n\n第五段。",
                "context_pack": {},
                "recommended_next_actions": [],
            },
        ]
        mock_review.return_value = {
            "contract_name": "review_report",
            "contract_version": "v1",
            "review_id": "review_cycle_rotation_user_draft",
            "run_id": "cycle_rotation_user",
            "decision": "revise",
            "total_score": 70,
            "lazy_index": 1,
            "score_breakdown": {},
            "top_issues": [{"issue_type": "model_issue", "severity": "medium", "summary": "需要修稿", "evidence": "x"}],
            "rewrite_actions": ["补强逻辑"],
            "strengths": [],
        }
        mock_vote.side_effect = [
            {"reviewer": "claude", "provider": "ppchat", "model": "claude-opus-4-6", "mode": "model_output", "valid_vote": True, "score": 80, "decision": "revise", "confidence": 0.8, "issue": "仍需修", "action": "继续修", "strength": "", "error": "", "raw_output": "{}"},
            {"reviewer": "claude", "provider": "ppchat", "model": "claude-opus-4-6", "mode": "model_output", "valid_vote": True, "score": 81, "decision": "revise", "confidence": 0.8, "issue": "仍需修", "action": "继续修", "strength": "", "error": "", "raw_output": "{}"},
            {"reviewer": "claude", "provider": "ppchat", "model": "claude-opus-4-6", "mode": "model_output", "valid_vote": True, "score": 82, "decision": "revise", "confidence": 0.8, "issue": "继续修", "action": "压缩", "strength": "", "error": "", "raw_output": "{}"},
            {"reviewer": "gpt5_4", "provider": "ppchat", "model": "gpt-5.4", "mode": "model_output", "valid_vote": True, "score": 83, "decision": "revise", "confidence": 0.8, "issue": "继续修", "action": "压缩", "strength": "", "error": "", "raw_output": "{}"},
            {"reviewer": "claude", "provider": "ppchat", "model": "claude-opus-4-6", "mode": "model_output", "valid_vote": True, "score": 82, "decision": "revise", "confidence": 0.8, "issue": "继续修", "action": "补论据", "strength": "", "error": "", "raw_output": "{}"},
            {"reviewer": "gemini", "provider": "ikun", "model": "gemini-3.1-pro-preview", "mode": "model_output", "valid_vote": True, "score": 84, "decision": "revise", "confidence": 0.78, "issue": "继续修", "action": "补过渡", "strength": "", "error": "", "raw_output": "{}"},
            {"reviewer": "gpt5_4", "provider": "ppchat", "model": "gpt-5.4", "mode": "model_output", "valid_vote": True, "score": 83, "decision": "revise", "confidence": 0.8, "issue": "继续修", "action": "补论据", "strength": "", "error": "", "raw_output": "{}"},
            {"reviewer": "gemini", "provider": "ikun", "model": "gemini-3.1-pro-preview", "mode": "model_output", "valid_vote": True, "score": 82, "decision": "revise", "confidence": 0.78, "issue": "继续修", "action": "补过渡", "strength": "", "error": "", "raw_output": "{}"},
        ]

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = load_config(str(root))
            result = run_draft_cycle(
                {
                    "run_id": "cycle_rotation_user",
                    "topic": "AI coding 与业务理解",
                    "platform": "wechat",
                    "user_goal": "写一篇公众号观点文",
                    "force_prompt_only": True,
                    "manual_draft_text": "# 标题\n\n初稿。\n\n第二段。\n\n第三段。\n\n第四段。\n\n第五段。",
                    "use_governance_review": True,
                    "governance_workflow_mode": "rotation_v2",
                },
                config,
            )

            self.assertEqual([item["revision_owner"] for item in result["revision_rounds"]], ["gemini", "gemini", "gpt5_4", "claude"])
            self.assertTrue(result["final_review_governance"]["requires_user_arbitration"])
            self.assertEqual(result["final_review_governance"]["decision_options"], ["user_manual_arbitration_required"])
            self.assertIn("必须交由用户人工裁决", result["recommended_next_actions"][0])

    @patch("writing_brain.workflow.build_review_report")
    @patch("writing_brain.revision.run_writer_turn")
    @patch("writing_brain.workflow.run_writer_turn")
    def test_manual_seed_skips_initial_draft_model_but_keeps_revise_live(
        self,
        mock_writer_turn: object,
        mock_revision_writer_turn: object,
        mock_review: object,
    ) -> None:
        mock_writer_turn.return_value = {
            "contract_name": "writer_response",
            "contract_version": "v1",
            "run_id": "cycle_manual_seed",
            "task_mode": "draft",
            "mode": "prompt_only",
            "provider": "prompt_only",
            "model": "",
            "reply_text": "draft skipped in favor of manual seed",
            "context_pack": {},
            "recommended_next_actions": [],
        }
        mock_revision_writer_turn.return_value = {
            "contract_name": "writer_response",
            "contract_version": "v1",
            "run_id": "cycle_manual_seed",
            "task_mode": "revise",
            "revision_owner": "gemini",
            "mode": "model_output",
            "provider": "packyapi",
            "model": "gemini-3.1-pro-preview",
            "reply_text": "# 标题\n\nGemini 修稿版。\n\n第二段。\n\n第三段。\n\n第四段。\n\n第五段。",
            "context_pack": {},
            "recommended_next_actions": [],
        }
        mock_review.side_effect = [
            {
                "contract_name": "review_report",
                "contract_version": "v1",
                "review_id": "review_cycle_manual_seed_draft",
                "run_id": "cycle_manual_seed",
                "decision": "rewrite",
                "total_score": 60,
                "lazy_index": 2,
                "score_breakdown": {},
                "top_issues": [{"issue_type": "model_issue", "severity": "medium", "summary": "需要修稿", "evidence": "x"}],
                "rewrite_actions": ["补强逻辑"],
                "strengths": [],
            },
            {
                "contract_name": "review_report",
                "contract_version": "v1",
                "review_id": "review_cycle_manual_seed_revised_1",
                "run_id": "cycle_manual_seed",
                "decision": "pass",
                "total_score": 90,
                "lazy_index": 0,
                "score_breakdown": {},
                "top_issues": [{"issue_type": "no_major_issue", "severity": "low", "summary": "未发现显著问题", "evidence": "ok"}],
                "rewrite_actions": ["可直接发布"],
                "strengths": ["结构更完整"],
            },
        ]

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = load_config(str(root))
            result = run_draft_cycle(
                {
                    "run_id": "cycle_manual_seed",
                    "topic": "AI coding 与业务理解",
                    "platform": "wechat",
                    "user_goal": "写一篇公众号观点文",
                    "manual_draft_text": "# 标题\n\n现有母稿。\n\n第二段。\n\n第三段。\n\n第四段。\n\n第五段。",
                },
                config,
            )

            self.assertEqual(result["draft_source"], "manual_input")
            self.assertEqual(result["revised_source"], "model_output")
            self.assertEqual(result["final_decision"], "pass")
            first_call_payload = mock_writer_turn.call_args_list[0].args[0]
            second_call_payload = mock_revision_writer_turn.call_args_list[0].args[0]
            self.assertTrue(first_call_payload["force_prompt_only"])
            self.assertEqual(second_call_payload["task_mode"], "revise")
            self.assertFalse(second_call_payload.get("force_prompt_only", False))

    def test_draft_cycle_waits_for_draft_when_writer_falls_back(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = load_config(str(root))
            result = run_draft_cycle(
                {
                    "run_id": "cycle_prompt_only",
                    "topic": "AI coding 与业务理解",
                    "platform": "wechat",
                    "user_goal": "写一篇公众号观点文",
                    "user_message": "先走流程。",
                    "force_prompt_only": True,
                },
                config,
            )

            self.assertEqual(result["status"], "awaiting_draft")
            self.assertEqual(result["draft_turn"]["mode"], "prompt_only")
            self.assertEqual(result["draft_text"], "")
            self.assertIsNone(result["draft_review_report"])
            self.assertIn("当前还没有可评分稿件", result["recommended_next_actions"][-1])

    @patch("writing_brain.workflow.build_review_report")
    @patch("writing_brain.revision.run_writer_turn")
    @patch("writing_brain.workflow.run_writer_turn")
    def test_draft_cycle_switches_to_manual_after_failed_gemini_revise(self, mock_writer_turn: object, mock_revision_writer_turn: object, mock_review: object) -> None:
        mock_writer_turn.return_value = {
            "contract_name": "writer_response",
            "contract_version": "v1",
            "run_id": "cycle_alt",
            "task_mode": "draft",
            "mode": "model_output",
            "provider": "packyapi",
            "model": "gemini-3.1-pro-preview",
            "reply_text": "初稿内容",
            "context_pack": {},
            "recommended_next_actions": [],
        }
        mock_revision_writer_turn.return_value = {
            "contract_name": "writer_response",
            "contract_version": "v1",
            "run_id": "cycle_alt",
            "task_mode": "revise",
            "revision_owner": "gemini",
            "mode": "model_output",
            "provider": "packyapi",
            "model": "gemini-3.1-pro-preview",
            "reply_text": "Gemini 修过的版本",
            "context_pack": {},
            "recommended_next_actions": [],
        }
        mock_review.side_effect = [
            {
                "contract_name": "review_report",
                "contract_version": "v1",
                "review_id": "review_cycle_alt_draft",
                "run_id": "cycle_alt",
                "decision": "rewrite",
                "total_score": 55,
                "lazy_index": 4,
                "score_breakdown": {},
                "top_issues": [{"issue_type": "missing_points", "severity": "high", "summary": "论证不够", "evidence": "x"}],
                "rewrite_actions": ["补论证"],
                "strengths": [],
            },
            {
                "contract_name": "review_report",
                "contract_version": "v1",
                "review_id": "review_cycle_alt_revised_1",
                "run_id": "cycle_alt",
                "decision": "revise",
                "total_score": 82,
                "lazy_index": 1,
                "score_breakdown": {},
                "top_issues": [{"issue_type": "model_issue", "severity": "medium", "summary": "仍需压缩", "evidence": "x"}],
                "rewrite_actions": ["压缩中段"],
                "strengths": [],
            },
        ]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = load_config(str(root))
            result = run_draft_cycle(
                {
                    "run_id": "cycle_alt",
                    "topic": "AI coding 与业务理解",
                    "platform": "wechat",
                    "user_goal": "写一篇公众号观点文",
                    "revision_sequence": ["gemini", "manual"],
                    "max_revision_rounds": 2,
                },
                config,
            )

            self.assertEqual(result["status"], "awaiting_revision")
            self.assertEqual(result["next_revision_owner"], "manual")
            self.assertEqual(result["revision_rounds"][0]["revision_owner"], "gemini")
            self.assertEqual(result["revised_text"], "Gemini 修过的版本")
            self.assertIn("前台 CLI 只负责继续推进和监控流程", result["recommended_next_actions"][0])

    @patch("writing_brain.workflow.build_review_report")
    @patch("writing_brain.revision.run_writer_turn")
    @patch("writing_brain.workflow.run_writer_turn")
    def test_draft_cycle_switches_back_to_gemini_after_manual_revision(self, mock_writer_turn: object, mock_revision_writer_turn: object, mock_review: object) -> None:
        mock_writer_turn.return_value = {
            "contract_name": "writer_response",
            "contract_version": "v1",
            "run_id": "cycle_manual",
            "task_mode": "draft",
            "mode": "model_output",
            "provider": "packyapi",
            "model": "gemini-3.1-pro-preview",
            "reply_text": "初稿内容",
            "context_pack": {},
            "recommended_next_actions": [],
        }
        mock_revision_writer_turn.return_value = {
            "contract_name": "writer_response",
            "contract_version": "v1",
            "run_id": "cycle_manual",
            "task_mode": "revise",
            "revision_owner": "gemini",
            "mode": "model_output",
            "provider": "packyapi",
            "model": "gemini-3.1-pro-preview",
            "reply_text": "Gemini 修过的版本",
            "context_pack": {},
            "recommended_next_actions": [],
        }
        mock_review.side_effect = [
            {
                "contract_name": "review_report",
                "contract_version": "v1",
                "review_id": "review_cycle_manual_draft",
                "run_id": "cycle_manual",
                "decision": "rewrite",
                "total_score": 55,
                "lazy_index": 4,
                "score_breakdown": {},
                "top_issues": [{"issue_type": "missing_points", "severity": "high", "summary": "论证不够", "evidence": "x"}],
                "rewrite_actions": ["补论证"],
                "strengths": [],
            },
            {
                "contract_name": "review_report",
                "contract_version": "v1",
                "review_id": "review_cycle_manual_revised_1",
                "run_id": "cycle_manual",
                "decision": "revise",
                "total_score": 82,
                "lazy_index": 1,
                "score_breakdown": {},
                "top_issues": [{"issue_type": "model_issue", "severity": "medium", "summary": "仍需压缩", "evidence": "x"}],
                "rewrite_actions": ["压缩中段"],
                "strengths": [],
            },
            {
                "contract_name": "review_report",
                "contract_version": "v1",
                "review_id": "review_cycle_manual_revised_2",
                "run_id": "cycle_manual",
                "decision": "revise",
                "total_score": 86,
                "lazy_index": 1,
                "score_breakdown": {},
                "top_issues": [{"issue_type": "model_issue", "severity": "medium", "summary": "还差一轮", "evidence": "x"}],
                "rewrite_actions": ["再压一轮"],
                "strengths": [],
            },
        ]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = load_config(str(root))
            result = run_draft_cycle(
                {
                    "run_id": "cycle_manual",
                    "topic": "AI coding 与业务理解",
                    "platform": "wechat",
                    "user_goal": "写一篇公众号观点文",
                    "revision_sequence": ["gemini", "manual"],
                    "manual_revision_texts": ["Codex 人工修过的版本"],
                    "max_revision_rounds": 2,
                },
                config,
            )

            self.assertEqual(result["status"], "awaiting_revision")
            self.assertEqual(result["next_revision_owner"], "gemini")
            self.assertEqual([item["revision_owner"] for item in result["revision_rounds"]], ["gemini", "manual"])
            self.assertEqual(result["revised_text"], "Codex 人工修过的版本")
            self.assertIn("下一轮应切回 Gemini revise", result["recommended_next_actions"][0])

    @patch("writing_brain.workflow.build_review_report")
    @patch("writing_brain.revision.run_writer_turn")
    @patch("writing_brain.workflow.run_writer_turn")
    def test_draft_cycle_defaults_to_model_only_revision_sequence(self, mock_writer_turn: object, mock_revision_writer_turn: object, mock_review: object) -> None:
        mock_writer_turn.return_value = {
            "contract_name": "writer_response",
            "contract_version": "v1",
            "run_id": "cycle_model_only",
            "task_mode": "draft",
            "mode": "model_output",
            "provider": "packyapi",
            "model": "gemini-3.1-pro-preview",
            "reply_text": "初稿内容",
            "context_pack": {},
            "recommended_next_actions": [],
        }
        mock_revision_writer_turn.return_value = {
            "contract_name": "writer_response",
            "contract_version": "v1",
            "run_id": "cycle_model_only",
            "task_mode": "revise",
            "revision_owner": "gemini",
            "mode": "model_output",
            "provider": "packyapi",
            "model": "gemini-3.1-pro-preview",
            "reply_text": "Gemini 修过的版本",
            "context_pack": {},
            "recommended_next_actions": [],
        }
        mock_review.side_effect = [
            {
                "contract_name": "review_report",
                "contract_version": "v1",
                "review_id": "review_cycle_model_only_draft",
                "run_id": "cycle_model_only",
                "decision": "rewrite",
                "total_score": 55,
                "lazy_index": 4,
                "score_breakdown": {},
                "top_issues": [{"issue_type": "missing_points", "severity": "high", "summary": "论证不够", "evidence": "x"}],
                "rewrite_actions": ["补论证"],
                "strengths": [],
            },
            {
                "contract_name": "review_report",
                "contract_version": "v1",
                "review_id": "review_cycle_model_only_revised_1",
                "run_id": "cycle_model_only",
                "decision": "revise",
                "total_score": 82,
                "lazy_index": 1,
                "score_breakdown": {},
                "top_issues": [{"issue_type": "model_issue", "severity": "medium", "summary": "仍需压缩", "evidence": "x"}],
                "rewrite_actions": ["压缩中段"],
                "strengths": [],
            },
        ]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = load_config(str(root))
            result = run_draft_cycle(
                {
                    "run_id": "cycle_model_only",
                    "topic": "AI coding 与业务理解",
                    "platform": "wechat",
                    "user_goal": "写一篇公众号观点文",
                    "max_revision_rounds": 1,
                },
                config,
            )

            self.assertEqual(result["revision_policy"]["revision_sequence"], ["gemini", "claude", "gpt5_4"])
            self.assertEqual(result["next_revision_owner"], "claude")
            self.assertIn("切换到 claude 修稿", result["recommended_next_actions"][0])

    @patch("writing_brain.workflow.build_review_report")
    @patch("writing_brain.revision.run_writer_turn")
    @patch("writing_brain.workflow.run_writer_turn")
    def test_draft_cycle_escalates_when_multiple_governance_rounds_have_issues(self, mock_writer_turn: object, mock_revision_writer_turn: object, mock_review: object) -> None:
        mock_writer_turn.return_value = {
            "contract_name": "writer_response",
            "contract_version": "v1",
            "run_id": "cycle_governance_alert",
            "task_mode": "draft",
            "mode": "model_output",
            "provider": "packyapi",
            "model": "gemini-3.1-pro-preview",
            "reply_text": "初稿内容",
            "context_pack": {},
            "recommended_next_actions": [],
        }
        mock_revision_writer_turn.return_value = {
            "contract_name": "writer_response",
            "contract_version": "v1",
            "run_id": "cycle_governance_alert",
            "task_mode": "revise",
            "revision_owner": "gemini",
            "mode": "model_output",
            "provider": "packyapi",
            "model": "gemini-3.1-pro-preview",
            "reply_text": "Gemini 修过的版本",
            "context_pack": {},
            "recommended_next_actions": [],
        }
        mock_review.side_effect = [
            {
                "contract_name": "review_report",
                "contract_version": "v1",
                "review_id": "review_cycle_governance_alert_draft",
                "run_id": "cycle_governance_alert",
                "decision": "revise",
                "total_score": 88,
                "lazy_index": 1,
                "score_breakdown": {},
                "top_issues": [{"issue_type": "model_issue", "severity": "medium", "summary": "需要压缩", "evidence": "x"}],
                "rewrite_actions": ["压缩中段"],
                "strengths": [],
                "governance": {
                    "mode": "three_powers",
                    "claude_review": {"decision": "revise"},
                    "gemini_review": {"decision": "pass"},
                    "gpt_review": {"decision": "pass"},
                    "decision_ready": True,
                    "quorum_pass": True,
                    "requires_user_arbitration": True,
                    "procedural_flags": ["multi_party_decision_conflict"],
                },
            },
            {
                "contract_name": "review_report",
                "contract_version": "v1",
                "review_id": "review_cycle_governance_alert_revised_1",
                "run_id": "cycle_governance_alert",
                "decision": "revise",
                "total_score": 89,
                "lazy_index": 1,
                "score_breakdown": {},
                "top_issues": [{"issue_type": "model_issue", "severity": "medium", "summary": "仍需复核", "evidence": "x"}],
                "rewrite_actions": ["继续复核"],
                "strengths": [],
                "governance": {
                    "mode": "three_powers",
                    "claude_review": {"decision": "revise"},
                    "gemini_review": {"decision": "pass"},
                    "gpt_review": {"decision": "revise"},
                    "decision_ready": True,
                    "quorum_pass": False,
                    "requires_user_arbitration": True,
                    "procedural_flags": ["multi_party_decision_conflict", "multi_party_score_gap_10_plus"],
                },
            },
        ]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = load_config(str(root))
            result = run_draft_cycle(
                {
                    "run_id": "cycle_governance_alert",
                    "topic": "AI coding 与业务理解",
                    "platform": "wechat",
                    "user_goal": "写一篇公众号观点文",
                    "max_revision_rounds": 1,
                },
                config,
            )

            self.assertTrue(result["governance_alert"]["escalate_to_user"])
            self.assertEqual(result["governance_alert"]["problem_round_count"], 2)
            self.assertTrue(any("两轮及以上议事仍存在问题" in item for item in result["recommended_next_actions"]))


if __name__ == "__main__":
    unittest.main()
