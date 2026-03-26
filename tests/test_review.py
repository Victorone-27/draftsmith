from __future__ import annotations

import unittest
from unittest.mock import patch

from writing_brain.review import build_review_report


class ReviewTests(unittest.TestCase):
    def test_review_exposes_dimension_checks_and_blocking_rules(self) -> None:
        result = build_review_report(
            {
                "run_id": "run_dimension_checks",
                "draft_text": "标题：这是一篇还没成型的稿子\n\n导语：先铺背景，再慢慢说。\n\n我们应该重视 AI。",
                "context_pack": {
                    "platform": "wechat",
                    "user_goal": "写一篇建立行业影响力的公众号观点文章",
                    "must_cover_points": ["为什么现在成立", "边界在哪里"],
                },
            }
        )

        self.assertFalse(result["expanded_checks"]["length"]["ok"])
        self.assertFalse(result["expanded_checks"]["depth"]["ok"])
        self.assertFalse(result["expanded_checks"]["argument"]["ok"])
        self.assertTrue(result["expanded_checks"]["formatting"]["hard_fail"])
        self.assertIn("too_short", result["expanded_checks"]["blocking_issue_types"])
        self.assertIn("formatting_mismatch", result["expanded_checks"]["blocking_issue_types"])
        issue_types = {item["issue_type"] for item in result["top_issues"]}
        self.assertIn("too_short", issue_types)
        self.assertIn("formatting_mismatch", issue_types)

    def test_review_uses_fuzzy_coverage_for_paraphrased_must_cover_points(self) -> None:
        result = build_review_report(
            {
                "run_id": "run_fuzzy_coverage",
                "draft_text": (
                    "中国 AI 行业真正危险的，不是模型暂时落后，而是越来越多人开始适应落后。\n\n"
                    "版本升级从来不是名字变化，而是能力边界变化。\n\n"
                    "长期不用最强模型的人，很容易把未来理解成低配版本的现在。\n\n"
                    "入口和流量当然重要，但模型上限决定谁有资格定义明天。\n\n"
                    "中国需要更多能重新定义能力边界的标杆型模型。"
                ),
                "context_pack": {
                    "platform": "wechat",
                    "must_cover_points": [
                        "中国 AI 行业最危险的不是落后，而是开始适应落后",
                        "版本升级不是名字变化，而是能力边界变化",
                        "长期不用最强模型的人会误判未来",
                        "入口重要，但模型上限决定谁定义明天",
                        "中国需要更多能重新定义能力边界的标杆型模型",
                    ],
                },
            }
        )

        issue_types = {item["issue_type"] for item in result["top_issues"]}
        self.assertNotIn("missing_points", issue_types)

    def test_review_flags_missing_points_and_late_thesis(self) -> None:
        result = build_review_report(
            {
                "run_id": "run_x",
                "draft_text": "先讲一些大背景。\n\n值得注意的是，世界变化很快。\n\n总的来说，我们要拥抱变化。",
                "context_pack": {
                    "platform": "wechat",
                    "must_cover_points": ["业务理解会重新变得更稀缺", "AI 先拉平执行层"],
                    "core_claims": [{"title": "真正稀缺的会重新回到业务理解"}],
                },
            }
        )

        self.assertEqual(result["contract_name"], "review_report")
        self.assertIn(result["decision"], {"revise", "rewrite"})
        issue_types = {item["issue_type"] for item in result["top_issues"]}
        self.assertIn("missing_points", issue_types)
        self.assertIn("late_thesis", issue_types)
        self.assertEqual(result["review_mode"], "heuristic_only")

    def test_review_does_not_flag_late_thesis_without_reference_terms(self) -> None:
        result = build_review_report(
            {
                "run_id": "run_y",
                "draft_text": "先给一个明确判断。\n\n因为这里有具体场景和例子，所以展开还算完整。",
                "context_pack": {
                    "platform": "wechat",
                    "must_cover_points": [],
                    "core_claims": [],
                },
            }
        )

        issue_types = {item["issue_type"] for item in result["top_issues"]}
        self.assertNotIn("late_thesis", issue_types)

    def test_review_accepts_contrastive_opening_as_clear_thesis(self) -> None:
        result = build_review_report(
            {
                "run_id": "run_contrastive_opening",
                "draft_text": (
                    "中国 AI 行业现在最危险的，不是模型暂时落后，而是在模型代际跃迁还没结束的时候，"
                    "越来越多人已经开始接受另一套逻辑。\n\n"
                    "后文继续展开版本差异、入口叙事和标杆模型。"
                ),
                "context_pack": {
                    "platform": "wechat",
                    "topic": "中国AI行业最危险的，不是落后，而是开始适应落后",
                    "must_cover_points": [
                        "入口重要，但模型上限决定谁定义明天",
                    ],
                    "core_claims": [
                        {"title": "AI 先拉平的是执行层，不是认知层"},
                    ],
                },
            }
        )

        issue_types = {item["issue_type"] for item in result["top_issues"]}
        self.assertNotIn("late_thesis", issue_types)

    def test_review_argument_check_tracks_claim_support_gaps(self) -> None:
        result = build_review_report(
            {
                "run_id": "run_claim_support_gap",
                "draft_text": (
                    "# 判断先行\n\n"
                    "真正决定组织效率的，不是工具数量，而是是否有人能把工具编进业务流程。\n\n"
                    "这会改写团队分工。\n\n"
                    "大家都应该重视。"
                ),
                "context_pack": {
                    "platform": "wechat",
                },
            }
        )

        self.assertGreaterEqual(result["expanded_checks"]["argument"]["claim_support_gaps"], 1)
        self.assertFalse(result["expanded_checks"]["argument"]["ok"])
        self.assertIn("weak_argument", {item["issue_type"] for item in result["top_issues"]})

    @patch(
        "writing_brain.review.call_ppchat_chat",
        return_value={
            "mode": "model_output",
            "provider": "packyapi",
            "model": "gemini-2.5-pro",
            "reply_text": """{
  "semantic_score": 58,
  "decision": "rewrite",
  "confidence": 0.83,
  "top_issues": [
    {
      "issue_type": "blurry_logic",
      "severity": "high",
      "summary": "关键判断和例子之间的连接不够紧",
      "evidence": "例子存在，但没有充分解释为什么能推出结论"
    }
  ],
  "rewrite_actions": ["把例子和结论之间的推导补完整"],
  "strengths": ["主题方向是对的"]
}""",
        },
    )
    def test_review_can_merge_model_reviewer_layer(self, _: object) -> None:
        result = build_review_report(
            {
                "run_id": "run_z",
                "use_model_reviewer": True,
                "draft_text": "先说结论。\n\nAI 先拉平的是执行层。\n\n例如很多通用动作会被工具压缩。",
                "context_pack": {
                    "platform": "wechat",
                    "topic": "AI coding 与业务理解",
                    "must_cover_points": ["AI 先拉平执行层"],
                    "core_claims": [{"title": "AI 先拉平的是执行层，不是认知层"}],
                },
            }
        )

        self.assertEqual(result["review_mode"], "hybrid")
        self.assertEqual(result["decision"], "rewrite")
        self.assertIn("heuristic", result["review_layers"])
        self.assertEqual(result["review_layers"]["model"]["semantic_score"], 58)
        summaries = [item["summary"] for item in result["top_issues"]]
        self.assertIn("关键判断和例子之间的连接不够紧", summaries)
        self.assertIn("把例子和结论之间的推导补完整", result["rewrite_actions"])

    @patch(
        "writing_brain.review.call_ppchat_chat",
        return_value={
            "mode": "model_output",
            "provider": "packyapi",
            "model": "gemini-2.5-pro",
            "reply_text": '{"semantic_score":62,"decision":"rewrite","confidence":0.91,"issue":"论据太薄","action":"补具体案例","strength":"方向是对的"}',
        },
    )
    def test_review_accepts_compact_model_json(self, _: object) -> None:
        result = build_review_report(
            {
                "run_id": "run_compact",
                "use_model_reviewer": True,
                "draft_text": "先说结论。\n\nAI 先拉平的是执行层。\n\n例如很多通用动作会被工具压缩。",
                "context_pack": {
                    "platform": "wechat",
                    "topic": "AI coding 与业务理解",
                    "must_cover_points": ["AI 先拉平执行层"],
                    "core_claims": [{"title": "AI 先拉平的是执行层，不是认知层"}],
                },
            }
        )

        self.assertEqual(result["review_mode"], "hybrid")
        self.assertEqual(result["decision"], "rewrite")
        self.assertIn("论据太薄", [item["summary"] for item in result["top_issues"]])
        self.assertIn("补具体案例", result["rewrite_actions"])

    @patch(
        "writing_brain.review.call_ppchat_chat",
        side_effect=[
            {
                "mode": "model_output",
                "provider": "ppchat",
                "model": "claude-opus-4-6",
                "reply_text": '{"semantic_score":96,"decision":"pass","confidence":0.91,"issue":"无显著问题","action":"可直接发布","strength":"论证完整"}',
            },
            {
                "mode": "model_output",
                "provider": "ppchat",
                "model": "gpt-5.4",
                "reply_text": '{"score":95,"decision":"pass","abnormal_review":false,"flags":[],"reason":"流程正常","recommendation":"可交由用户确认"}',
            },
        ],
    )
    @patch(
        "writing_brain.review.call_packy_chat",
        return_value={
            "mode": "model_output",
            "provider": "packyapi",
            "model": "gemini-3.1-pro-preview",
            "reply_text": "SCORE: 94\nDECISION: pass\nISSUE: 无显著问题\nACTION: 可直接发布\nSTRENGTH: 判断锋利",
        },
    )
    def test_review_can_attach_governance_votes(self, _: object, __: object) -> None:
        result = build_review_report(
            {
                "run_id": "run_governance",
                "use_model_reviewer": True,
                "use_governance_review": True,
                "draft_text": "先说结论。\n\n模型上限决定谁定义明天。\n\n这件事会改变产业判断。",
                "context_pack": {
                    "platform": "wechat",
                    "topic": "中国AI行业最危险的，不是落后，而是开始适应落后",
                    "must_cover_points": ["入口重要，但模型上限决定谁定义明天"],
                    "core_claims": [{"title": "长期不用最强模型的人会系统性误判 AI 未来"}],
                },
            }
        )

        governance = result["governance"]
        self.assertEqual(governance["mode"], "three_powers")
        self.assertEqual(governance["user_authority"], "final_veto")
        self.assertTrue(governance["claude_review"]["valid_vote"])
        self.assertTrue(governance["gemini_review"]["valid_vote"])
        self.assertTrue(governance["procedural_review"]["valid_vote"])
        self.assertTrue(governance["gpt_review"]["valid_vote"])
        self.assertTrue(governance["quorum_pass"])
        self.assertEqual(governance["policy"]["procedural_reviewer"], "gpt5_4_ppchat")
        self.assertEqual(governance["policy"]["codex_role"], "chancellor_only")
        self.assertEqual(governance["panel_average_score"], 95.0)
        self.assertNotIn("invalid_gpt5_4_vote", governance["procedural_flags"])
        self.assertEqual(governance["decision_options"][0], "user_review_pass_quorum")

    @patch(
        "writing_brain.review.call_ppchat_chat",
        side_effect=[
            {
                "mode": "model_output",
                "provider": "ppchat",
                "model": "claude-opus-4-6",
                "reply_text": '{"semantic_score":92,"decision":"pass","confidence":0.91,"issue":"无显著问题","action":"可直接发布","strength":"判断完整"}',
            },
            {
                "mode": "model_output",
                "provider": "ppchat",
                "model": "gpt-5.4",
                "reply_text": '{"score":93,"decision":"pass","abnormal_review":false,"flags":[],"reason":"流程正常","recommendation":"可交给用户确认"}',
            },
        ],
    )
    @patch(
        "writing_brain.review.call_packy_chat",
        return_value={
            "mode": "model_output",
            "provider": "packyapi",
            "model": "gemini-3.1-pro-preview",
            "reply_text": "SCORE: 95\nDECISION: pass\nISSUE: 无显著问题\nACTION: 可直接发布\nSTRENGTH: 论据扎实",
        },
    )
    def test_review_can_parse_gemini_plaintext_contract(self, _: object, __: object) -> None:
        result = build_review_report(
            {
                "run_id": "run_governance_plaintext_gemini",
                "use_model_reviewer": True,
                "use_governance_review": True,
                "draft_text": "先说结论。\n\n模型上限决定谁定义明天。\n\n这件事会改变产业判断。",
                "context_pack": {
                    "platform": "wechat",
                    "topic": "中国AI行业最危险的，不是落后，而是开始适应落后",
                },
            }
        )

        governance = result["governance"]
        self.assertTrue(governance["gemini_review"]["valid_vote"])
        self.assertEqual(governance["gemini_review"]["score"], 95)
        self.assertEqual(governance["gemini_review"]["decision"], "pass")
        self.assertTrue(governance["decision_ready"])

    @patch(
        "writing_brain.review.call_packy_chat",
        side_effect=[
            {
                "mode": "model_output",
                "provider": "packyapi",
                "model": "gemini-3.1-pro-preview",
                "reply_text": '{"score":88,"decision":"revise","issue":"中段重复","action":"压缩重复论据","strength":"开头判断直接"}',
            },
        ],
    )
    @patch(
        "writing_brain.review.call_ppchat_chat",
        side_effect=[
            {
                "mode": "model_output",
                "provider": "ppchat",
                "model": "claude-opus-4-6",
                "reply_text": '{"semantic_score":86,"decision":"revise","confidence":0.88,"issue":"论据略散","action":"收紧中段","strength":"判断明确"}',
            },
            {
                "mode": "model_output",
                "provider": "ppchat",
                "model": "gpt-5.4",
                "reply_text": '{"score":84,"decision":"revise","abnormal_review":false,"flags":[],"reason":"流程正常","recommendation":"把三方意见交给用户"}',
            },
        ],
    )
    def test_review_prefers_packy_schema_output(self, _: object, mock_packy: object) -> None:
        result = build_review_report(
            {
                "run_id": "run_governance_packy_schema",
                "use_model_reviewer": True,
                "use_governance_review": True,
                "draft_text": "先说结论。\n\n模型上限决定谁定义明天。\n\n中段还有重复表达。",
                "context_pack": {
                    "platform": "wechat",
                    "topic": "中国AI行业最危险的，不是落后，而是开始适应落后",
                },
            }
        )

        governance = result["governance"]
        self.assertTrue(governance["gemini_review"]["valid_vote"])
        self.assertEqual(governance["gemini_review"]["provider"], "packyapi")
        self.assertEqual(governance["gemini_review"]["score"], 88)
        self.assertEqual(mock_packy.call_count, 1)
        response_format = mock_packy.call_args.kwargs["response_format"]
        self.assertEqual(response_format["type"], "json_schema")
        self.assertTrue(response_format["json_schema"]["strict"])

    @patch(
        "writing_brain.review.call_packy_chat",
        side_effect=[
            {
                "mode": "model_output",
                "provider": "packyapi",
                "model": "gemini-3.1-pro-preview",
                "reply_text": '{"score":90,"decision":"pass"',
            },
            {
                "mode": "model_output",
                "provider": "packyapi",
                "model": "gemini-3.1-pro-preview",
                "reply_text": "SCORE: 90\nDECISION: pass\nISSUE: 无显著问题\nACTION: 可直接发布\nSTRENGTH: 结构稳定",
            },
        ],
    )
    @patch(
        "writing_brain.review.call_ppchat_chat",
        side_effect=[
            {
                "mode": "model_output",
                "provider": "ppchat",
                "model": "claude-opus-4-6",
                "reply_text": '{"semantic_score":92,"decision":"pass","confidence":0.91,"issue":"无显著问题","action":"可直接发布","strength":"判断完整"}',
            },
            {
                "mode": "model_output",
                "provider": "ppchat",
                "model": "gpt-5.4",
                "reply_text": '{"score":91,"decision":"pass","abnormal_review":false,"flags":[],"reason":"流程正常","recommendation":"可交给用户确认"}',
            },
        ],
    )
    def test_review_falls_back_to_plaintext_when_packy_schema_unavailable(
        self,
        _: object,
        mock_packy: object,
    ) -> None:
        result = build_review_report(
            {
                "run_id": "run_governance_packy_schema_fallback",
                "use_model_reviewer": True,
                "use_governance_review": True,
                "draft_text": "先说结论。\n\n模型上限决定谁定义明天。\n\n结构已经比较完整。",
                "context_pack": {
                    "platform": "wechat",
                    "topic": "中国AI行业最危险的，不是落后，而是开始适应落后",
                },
            }
        )

        governance = result["governance"]
        self.assertTrue(governance["gemini_review"]["valid_vote"])
        self.assertEqual(governance["gemini_review"]["provider"], "packyapi")
        self.assertEqual(governance["gemini_review"]["decision"], "pass")
        self.assertEqual(mock_packy.call_count, 1)
        self.assertEqual(mock_packy.call_args_list[0].kwargs["response_format"]["type"], "json_schema")

    @patch.dict("os.environ", {"IKUN_BASE_URL": "https://ikuncode.cc/v1"}, clear=False)
    @patch(
        "writing_brain.review.call_packy_chat",
        return_value={
            "mode": "model_output",
            "provider": "ikun",
            "model": "gemini-3.1-pro-preview",
            "reply_text": '{"score":87,"decision":"revise","issue":"结尾偏虚","action":"收紧结尾","strength":"开头锋利"}',
        },
    )
    @patch(
        "writing_brain.review.call_ppchat_chat",
        side_effect=[
            {
                "mode": "model_output",
                "provider": "ppchat",
                "model": "claude-opus-4-6",
                "reply_text": '{"semantic_score":84,"decision":"revise","confidence":0.9,"issue":"结尾偏虚","action":"收紧结尾","strength":"开头明确"}',
            },
            {
                "mode": "model_output",
                "provider": "ppchat",
                "model": "gpt-5.4",
                "reply_text": '{"score":83,"decision":"revise","abnormal_review":false,"flags":[],"reason":"流程正常","recommendation":"把三方意见交给用户"}',
            },
        ],
    )
    def test_review_can_route_gemini_reviewer_to_ikun(self, _: object, mock_packy: object) -> None:
        result = build_review_report(
            {
                "run_id": "run_governance_ikun_gemini",
                "use_model_reviewer": True,
                "use_governance_review": True,
                "draft_text": "先说结论。\n\n模型上限决定谁定义明天。\n\n结尾还有点虚。",
                "context_pack": {
                    "platform": "wechat",
                    "topic": "中国AI行业最危险的，不是落后，而是开始适应落后",
                },
            }
        )

        governance = result["governance"]
        self.assertTrue(governance["gemini_review"]["valid_vote"])
        self.assertEqual(governance["gemini_review"]["provider"], "ikun")
        self.assertEqual(mock_packy.call_args.kwargs["provider"], "ikun")
        self.assertEqual(mock_packy.call_args.kwargs["base_url_env_vars"][0], "WRITING_BRAIN_GEMINI_REVIEWER_BASE_URL")
        self.assertEqual(mock_packy.call_args.kwargs["api_key_env_vars"][0], "WRITING_BRAIN_GEMINI_REVIEWER_API_KEY")

    @patch(
        "writing_brain.review.call_ppchat_chat",
        side_effect=[
            {
                "mode": "model_output",
                "provider": "ppchat",
                "model": "claude-opus-4-6",
                "reply_text": '{"semantic_score":91,"decision":"revise","confidence":0.86,"issue":"论证略松","action":"补强中段论据","strength":"主题明确"}',
            },
            {
                "mode": "model_output",
                "provider": "ppchat",
                "model": "gpt-5.4",
                "reply_text": '{"score":89,"decision":"revise","abnormal_review":true,"flags":["invalid_gemini_vote"],"reason":"Gemini 票据无效","recommendation":"重跑 Gemini"}',
            },
        ],
    )
    @patch(
        "writing_brain.review.call_packy_chat",
        return_value={
            "mode": "model_output",
            "provider": "packyapi",
            "model": "gemini-2.5-pro",
            "reply_text": "Here",
        },
    )
    def test_review_governance_flags_invalid_gemini_vote(self, _: object, __: object) -> None:
        result = build_review_report(
            {
                "run_id": "run_governance_invalid_gemini",
                "use_model_reviewer": True,
                "use_governance_review": True,
                "draft_text": "先说结论。\n\n模型上限决定谁定义明天。\n\n这件事会改变产业判断。",
                "context_pack": {
                    "platform": "wechat",
                    "topic": "中国AI行业最危险的，不是落后，而是开始适应落后",
                },
            }
        )

        governance = result["governance"]
        self.assertTrue(governance["claude_review"]["valid_vote"])
        self.assertFalse(governance["gemini_review"]["valid_vote"])
        self.assertIn("invalid_gemini_vote", governance["procedural_flags"])
        self.assertEqual(governance["decision_options"], ["rerun_gemini_review"])

    @patch(
        "writing_brain.review.call_ppchat_chat",
        side_effect=[
            {
                "mode": "model_output",
                "provider": "ppchat",
                "model": "claude-opus-4-6",
                "reply_text": '{"semantic_score":90,"decision":"pass","confidence":0.9,"issue":"无显著问题","action":"可直接发布","strength":"结构完整"}',
            },
            {
                "mode": "model_output",
                "provider": "ppchat",
                "model": "gpt-5.4",
                "reply_text": "Evaluate the Review Process:**\n    *   *Claude",
            },
        ],
    )
    @patch(
        "writing_brain.review.call_packy_chat",
        return_value={
            "mode": "model_output",
            "provider": "packyapi",
            "model": "gemini-3.1-pro-preview",
            "reply_text": "SCORE: 85\nDECISION:",
        },
    )
    def test_review_can_fallback_when_governance_outputs_are_partial(self, _: object, __: object) -> None:
        result = build_review_report(
            {
                "run_id": "run_governance_partial_outputs",
                "use_model_reviewer": True,
                "use_governance_review": True,
                "draft_text": "先说结论。\n\n模型上限决定谁定义明天。\n\n这件事会改变产业判断。",
                "context_pack": {
                    "platform": "wechat",
                    "topic": "中国AI行业最危险的，不是落后，而是开始适应落后",
                },
            }
        )

        governance = result["governance"]
        self.assertTrue(governance["gemini_review"]["valid_vote"])
        self.assertEqual(governance["gemini_review"]["decision"], "revise")
        self.assertTrue(governance["procedural_review"]["valid_vote"])
        self.assertTrue(governance["procedural_review"]["abnormal_review"])
        self.assertIn("invalid_gpt5_4_vote", governance["procedural_flags"])


if __name__ == "__main__":
    unittest.main()
