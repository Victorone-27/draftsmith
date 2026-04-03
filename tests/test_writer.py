from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from writing_brain.config import load_config
from writing_brain.writer import run_writer_turn


class WriterTests(unittest.TestCase):
    def test_writer_turn_includes_memory_knowledge_and_fallbacks(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "knowledge-entities").mkdir(parents=True)
            (root / "knowledge-entities" / "k1.json").write_text(
                """{
  "knowledge_entity": {
    "entity_id": "success_pattern_demo",
    "entity_type": "success_pattern",
    "statement": "开头直接给出判断时，作者认可度更高。",
    "applies_to": {
      "platform": "wechat",
      "topic_keywords": ["ai", "coding", "业务理解"],
      "article_stage": "opening"
    },
    "source_feedback_run_ids": ["run_demo"],
    "confidence": 0.82,
    "support_count": 1,
    "source": "human_feedback"
  }
}""",
                encoding="utf-8",
            )
            config = load_config(str(root))
            result = run_writer_turn(
                {
                    "run_id": "writer_demo",
                    "task_mode": "brainstorm",
                    "topic": "AI coding 与业务理解",
                    "platform": "wechat",
                    "user_goal": "写公众号观点文",
                    "must_cover_points": ["AI 先拉平执行层"],
                    "user_message": "我想先聊怎么写。",
                    "force_prompt_only": True,
                },
                config,
            )

            self.assertEqual(result["contract_name"], "writer_response")
            self.assertEqual(result["mode"], "prompt_only")
            self.assertEqual(result["provider"], "prompt_only")
            self.assertIn("Long-term memory knowledge", result["writer_prompt"])
            self.assertIn("开头直接给出判断时，作者认可度更高。", result["writer_prompt"])
            self.assertTrue((root / "sessions" / "writer_demo.writer.json").exists())
            self.assertTrue(any("默认不调用 Gemini" in item for item in result["recommended_next_actions"]))

    def test_revise_prompt_includes_draft_and_review_actions(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = load_config(str(root))
            result = run_writer_turn(
                {
                    "run_id": "writer_revise",
                    "task_mode": "revise",
                    "topic": "AI coding 与业务理解",
                    "platform": "wechat",
                    "user_goal": "修公众号稿",
                    "current_draft": "先说一个大背景。",
                    "review_report": {
                        "top_issues": [{"summary": "核心判断出现过晚"}],
                        "rewrite_actions": ["把核心判断提前到第一段"],
                    },
                    "user_message": "按 reviewer 意见改。",
                    "force_prompt_only": True,
                },
                config,
            )

            self.assertEqual(result["task_mode"], "revise")
            self.assertIn("Current draft", result["writer_prompt"])
            self.assertIn("先说一个大背景。", result["writer_prompt"])
            self.assertIn("把核心判断提前到第一段", result["writer_prompt"])

    @patch(
        "writing_brain.writer.call_packy_chat",
        return_value={
            "mode": "model_output",
            "reply_text": (
                "# AI coding 与业务理解\n\n"
                "先说结论，AI 先拉平的不是认知层，而是执行层。\n\n"
                "这意味着很多过去依赖重复劳动积累经验的岗位，会先被工具压缩掉中间动作。\n\n"
                "比如需求整理、信息归纳、初版方案、数据清洗和脚本拼装，这些工作会越来越多地被模型接手。\n\n"
                "真正决定差距的，会重新回到业务理解、边界判断和资源取舍。\n\n"
                "所以如果还把 AI 只当成提效插件，而不是新的生产界面，组织判断就会系统性滞后。\n\n"
                "这也是 reviewer 要求提前核心判断的原因，因为这篇文章要先把结论打出来，再展开论证。"
            ),
            "provider": "packyapi",
            "model": "gemini-3.1-pro-preview",
        },
    )
    @patch.dict("os.environ", {"PACKYAPI_API_KEY": "test-key"}, clear=False)
    def test_revise_prefers_gemini_by_default(self, _: object) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = load_config(str(root))
            result = run_writer_turn(
                {
                    "run_id": "writer_revise_provider",
                    "task_mode": "revise",
                    "topic": "AI coding 与业务理解",
                    "platform": "wechat",
                    "user_goal": "修公众号稿",
                    "current_draft": "先说一个大背景。",
                    "review_report": {
                        "top_issues": [{"summary": "核心判断出现过晚"}],
                        "rewrite_actions": ["把核心判断提前到第一段"],
                    },
                },
                config,
            )

            self.assertEqual(result["mode"], "model_output")
            self.assertEqual(result["provider"], "packyapi")
            self.assertEqual(result["model"], "gemini-3.1-pro-preview")
            self.assertEqual(result["revision_owner"], "gemini")
            self.assertIn("AI 先拉平的不是认知层，而是执行层", result["reply_text"])

    @patch(
        "writing_brain.writer.call_packy_chat",
        return_value={
            "mode": "model_output",
            "reply_text": "先说结论。\n\n这是一篇完整初稿。\n\n第二段。\n\n第三段。\n\n第四段。\n\n第五段。",
            "provider": "packyapi",
            "model": "gemini-3.1-pro-preview",
        },
    )
    @patch.dict("os.environ", {"PACKYAPI_API_KEY": "test-key"}, clear=False)
    def test_draft_uses_larger_token_budget(self, mock_packy: object) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = load_config(str(root))
            result = run_writer_turn(
                {
                    "run_id": "writer_draft_budget",
                    "task_mode": "draft",
                    "topic": "AI coding 与业务理解",
                    "platform": "wechat",
                    "user_goal": "写公众号观点文",
                    "user_message": "直接出一版初稿。",
                },
                config,
            )

            self.assertEqual(result["mode"], "model_output")
            self.assertEqual(mock_packy.call_args.kwargs["max_tokens"], 6000)

    @patch(
        "writing_brain.writer.call_packy_chat",
        return_value={
            "mode": "model_output",
            "reply_text": (
                "# AI coding 与业务理解\n\n"
                "先说结论，AI 先拉平的不是认知层，而是执行层。\n\n"
                "这意味着很多过去依赖重复劳动积累经验的岗位，会先被工具压缩掉中间动作。\n\n"
                "比如需求整理、信息归纳、初版方案、数据清洗和脚本拼装，这些工作会越来越多地被模型接手。\n\n"
                "真正决定差距的，会重新回到业务理解、边界判断和资源取舍。\n\n"
                "所以如果还把 AI 只当成提效插件，而不是新的生产界面，组织判断就会系统性滞后。\n\n"
                "这也是 reviewer 要求提前核心判断的原因，因为这篇文章要先把结论打出来，再展开论证。"
            ),
            "provider": "packyapi",
            "model": "gemini-3.1-pro-preview",
        },
    )
    @patch.dict("os.environ", {"PACKYAPI_API_KEY": "test-key"}, clear=False)
    def test_gemini_revise_uses_larger_token_budget(self, mock_packy: object) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = load_config(str(root))
            result = run_writer_turn(
                {
                    "run_id": "writer_revise_budget",
                    "task_mode": "revise",
                    "topic": "AI coding 与业务理解",
                    "platform": "wechat",
                    "user_goal": "修公众号稿",
                    "current_draft": "先说一个大背景。",
                    "review_report": {
                        "top_issues": [{"summary": "核心判断出现过晚"}],
                        "rewrite_actions": ["把核心判断提前到第一段"],
                    },
                },
                config,
            )

            self.assertEqual(result["mode"], "model_output")
            self.assertEqual(mock_packy.call_args.kwargs["max_tokens"], 6000)

    @patch(
        "writing_brain.writer.call_packy_chat",
        return_value={
            "mode": "model_output",
            "reply_text": (
                "先说结论，AI 先拉平的不是认知层，而是执行层。\n\n"
                "这意味着很多过去依赖重复劳动积累经验的岗位，会先被工具压缩掉中间动作。\n\n"
                "比如需求整理、信息归纳、初版方案、数据清洗和脚本拼装，这些工作会越来越多地被模型接手。\n\n"
                "真正决定差距的，会重新回到业务理解、边界判断和资源取舍。\n\n"
                "如果还把 AI 只当成提效插件，而不是新的生产界面，组织判断就会系统性滞后。\n\n"
                "这也是 reviewer 要求提前核心判断的原因，因为这篇文章要先把结论打出来，再展开论证。"
            ),
            "provider": "packyapi",
            "model": "gemini-3.1-pro-preview",
        },
    )
    @patch.dict("os.environ", {"PACKYAPI_API_KEY": "test-key"}, clear=False)
    def test_gemini_revise_accepts_complete_article_without_heading(self, _: object) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = load_config(str(root))
            result = run_writer_turn(
                {
                    "run_id": "writer_revise_no_heading",
                    "task_mode": "revise",
                    "topic": "AI coding 与业务理解",
                    "platform": "wechat",
                    "user_goal": "修公众号稿",
                    "current_draft": "先说一个大背景。\n\n这是一段需要被改写的完整草稿。\n\n这里还有一些补充内容，用来模拟正常长度。",
                    "review_report": {
                        "top_issues": [{"summary": "核心判断出现过晚"}],
                        "rewrite_actions": ["把核心判断提前到第一段"],
                    },
                },
                config,
            )

            self.assertEqual(result["mode"], "model_output")
            self.assertIn("AI 先拉平的不是认知层，而是执行层", result["reply_text"])

    @patch(
        "writing_brain.writer.call_packy_chat",
        return_value={
            "mode": "model_output",
            "reply_text": (
                "这里是根据 reviewer 意见定向修改后的稿件。\n\n"
                "主要调整动作：\n"
                "1. 删掉重复表达。\n\n"
                "***\n\n"
                "**修改后正文：**\n\n"
                "先说结论，AI 先拉平的不是认知层，而是执行层。\n\n"
                "这意味着很多过去依赖重复劳动积累经验的岗位，会先被工具压缩掉中间动作。\n\n"
                "比如需求整理、信息归纳、初版方案、数据清洗和脚本拼装，这些工作会越来越多地被模型接手。\n\n"
                "真正决定差距的，会重新回到业务理解、边界判断和资源取舍。\n\n"
                "如果还把 AI 只当成提效插件，而不是新的生产界面，组织判断就会系统性滞后。\n\n"
                "这也是 reviewer 要求提前核心判断的原因，因为这篇文章要先把结论打出来，再展开论证。"
            ),
            "provider": "packyapi",
            "model": "gemini-3.1-pro-preview",
        },
    )
    @patch.dict("os.environ", {"PACKYAPI_API_KEY": "test-key"}, clear=False)
    def test_gemini_revise_extracts_article_from_explanatory_wrapper(self, _: object) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = load_config(str(root))
            result = run_writer_turn(
                {
                    "run_id": "writer_revise_extract_body",
                    "task_mode": "revise",
                    "topic": "AI coding 与业务理解",
                    "platform": "wechat",
                    "user_goal": "修公众号稿",
                    "current_draft": "先说一个大背景。\n\n再补一点正文。",
                    "review_report": {
                        "top_issues": [{"summary": "核心判断出现过晚"}],
                        "rewrite_actions": ["把核心判断提前到第一段"],
                    },
                },
                config,
            )

            self.assertEqual(result["mode"], "model_output")
            self.assertTrue(result["reply_text"].startswith("先说结论"))
            self.assertNotIn("主要调整动作", result["reply_text"])

    @patch(
        "writing_brain.writer.call_packy_chat",
        return_value={
            "mode": "model_output",
            "reply_text": (
                "这里是根据 reviewer 意见定向修改后的稿件。\n\n"
                "本次修改重点补齐了你要求的 5 个核心判断，并补了更多案例。\n\n"
                "***\n\n"
                "中国 AI 行业真正危险的，不是模型暂时落后，而是越来越多人开始适应落后。\n\n"
                "版本升级不是名字变化，而是能力边界变化。这会直接改写产品形态。\n\n"
                "长期不用最强模型的人，很容易把未来理解成低配版本的现在。\n\n"
                "入口重要，但模型上限决定谁定义明天。两者不是同一层竞争。\n\n"
                "中国需要更多能重新定义能力边界的标杆型模型。否则行业会更快适应落后。这会持续削弱对下一代能力边界的判断。"
            ),
            "provider": "packyapi",
            "model": "gemini-3.1-pro-preview",
        },
    )
    @patch.dict("os.environ", {"PACKYAPI_API_KEY": "test-key"}, clear=False)
    def test_gemini_revise_strips_leading_meta_without_body_marker(self, _: object) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = load_config(str(root))
            result = run_writer_turn(
                {
                    "run_id": "writer_revise_strip_meta",
                    "task_mode": "revise",
                    "topic": "中国 AI 行业最危险的，不是落后，而是开始适应落后",
                    "platform": "wechat",
                    "user_goal": "修公众号稿",
                    "current_draft": "旧草稿。",
                    "review_report": {
                        "top_issues": [{"summary": "首段判断不够直接"}],
                        "rewrite_actions": ["把核心判断提前到第一段"],
                    },
                },
                config,
            )

            self.assertEqual(result["mode"], "model_output")
            self.assertTrue(result["reply_text"].startswith("中国 AI 行业真正危险的"))
            self.assertNotIn("这里是根据 reviewer", result["reply_text"])

    @patch(
        "writing_brain.writer.call_ppchat_chat",
        return_value={
            "mode": "model_output",
            "reply_text": "这是 Claude 按 reviewer 意见改过的版本。",
            "provider": "ppchat",
            "model": "claude-opus-4-6",
        },
    )
    def test_revise_can_explicitly_route_to_claude(self, _: object) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = load_config(str(root))
            result = run_writer_turn(
                {
                    "run_id": "writer_revise_claude",
                    "task_mode": "revise",
                    "topic": "AI coding 与业务理解",
                    "platform": "wechat",
                    "user_goal": "修公众号稿",
                    "current_draft": "先说一个大背景。",
                    "review_report": {
                        "top_issues": [{"summary": "核心判断出现过晚"}],
                        "rewrite_actions": ["把核心判断提前到第一段"],
                    },
                    "revision_owner": "claude",
                },
                config,
            )

            self.assertEqual(result["mode"], "model_output")
            self.assertEqual(result["provider"], "ppchat")
            self.assertEqual(result["model"], "claude-opus-4-6")
            self.assertEqual(result["revision_owner"], "claude")

    @patch(
        "writing_brain.writer.call_ppchat_chat",
        return_value={
            "mode": "model_output",
            "reply_text": "这是 gpt-5.4 按 reviewer 意见改过的版本。",
            "provider": "ppchat",
            "model": "gpt-5.4",
        },
    )
    def test_revise_can_explicitly_route_to_gpt5_4(self, _: object) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = load_config(str(root))
            result = run_writer_turn(
                {
                    "run_id": "writer_revise_gpt",
                    "task_mode": "revise",
                    "topic": "AI coding 与业务理解",
                    "platform": "wechat",
                    "user_goal": "修公众号稿",
                    "current_draft": "先说一个大背景。",
                    "review_report": {
                        "top_issues": [{"summary": "核心判断出现过晚"}],
                        "rewrite_actions": ["把核心判断提前到第一段"],
                    },
                    "revision_owner": "gpt5_4",
                },
                config,
            )

            self.assertEqual(result["mode"], "model_output")
            self.assertEqual(result["provider"], "ppchat")
            self.assertEqual(result["model"], "gpt-5.4")
            self.assertEqual(result["revision_owner"], "gpt5_4")

    @patch(
        "writing_brain.writer.call_packy_chat",
        return_value={
            "mode": "model_output",
            "reply_text": "这里是按照 Reviewer 要求和你的补充方向修改后的版本。\n\n重点动作：\n1. 压缩第4-6段。",
            "provider": "packyapi",
            "model": "gemini-3.1-pro-preview",
        },
    )
    @patch.dict("os.environ", {"PACKYAPI_API_KEY": "test-key"}, clear=False)
    def test_gemini_revise_rejects_explanatory_output(self, _: object) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = load_config(str(root))
            result = run_writer_turn(
                {
                    "run_id": "writer_revise_bad_explainer",
                    "task_mode": "revise",
                    "topic": "AI coding 与业务理解",
                    "platform": "wechat",
                    "user_goal": "修公众号稿",
                    "current_draft": "先说一个大背景。",
                    "review_report": {
                        "top_issues": [{"summary": "核心判断出现过晚"}],
                        "rewrite_actions": ["把核心判断提前到第一段"],
                    },
                },
                config,
            )

            self.assertEqual(result["mode"], "prompt_only")
            self.assertIn("不完整", result["reply_text"])

    @patch(
        "writing_brain.writer.call_packy_chat",
        side_effect=[
            {
                "mode": "model_output",
                "reply_text": "这里是按照 Reviewer 要求和你的补充方向修改后的版本。\n\n重点动作：\n1. 压缩第4-6段。",
                "provider": "packyapi",
                "model": "gemini-3.1-pro-preview",
            },
            {
                "mode": "model_output",
                "reply_text": (
                    "# AI coding 与业务理解\n\n"
                    "先说结论，AI 先拉平的不是认知层，而是执行层。\n\n"
                    "这意味着很多过去依赖重复劳动积累经验的岗位，会先被工具压缩掉中间动作。\n\n"
                    "比如需求整理、信息归纳、初版方案、数据清洗和脚本拼装，这些工作会越来越多地被模型接手。\n\n"
                    "真正决定差距的，会重新回到业务理解、边界判断和资源取舍。\n\n"
                    "所以如果还把 AI 只当成提效插件，而不是新的生产界面，组织判断就会系统性滞后。\n\n"
                    "这也是 reviewer 要求提前核心判断的原因，因为这篇文章要先把结论打出来，再展开论证。"
                ),
                "provider": "packyapi",
                "model": "gemini-3.1-pro-preview",
            },
        ],
    )
    @patch.dict("os.environ", {"PACKYAPI_API_KEY": "test-key"}, clear=False)
    def test_gemini_revise_retries_once_after_invalid_output(self, mock_packy: object) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = load_config(str(root))
            result = run_writer_turn(
                {
                    "run_id": "writer_revise_retry",
                    "task_mode": "revise",
                    "topic": "AI coding 与业务理解",
                    "platform": "wechat",
                    "user_goal": "修公众号稿",
                    "current_draft": "先说一个大背景。",
                    "review_report": {
                        "top_issues": [{"summary": "核心判断出现过晚"}],
                        "rewrite_actions": ["把核心判断提前到第一段"],
                    },
                },
                config,
            )

            self.assertEqual(result["mode"], "model_output")
            self.assertEqual(mock_packy.call_count, 2)
            self.assertIn("AI 先拉平的不是认知层，而是执行层", result["reply_text"])

    @patch(
        "writing_brain.writer.call_packy_chat",
        return_value={
            "mode": "model_output",
            "reply_text": "# 标题\n\n这是一段正文，但后面被截断了。",
            "provider": "packyapi",
            "model": "gemini-3.1-pro-preview",
        },
    )
    @patch.dict("os.environ", {"PACKYAPI_API_KEY": "test-key"}, clear=False)
    def test_gemini_revise_rejects_truncated_article(self, _: object) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = load_config(str(root))
            result = run_writer_turn(
                {
                    "run_id": "writer_revise_bad_truncated",
                    "task_mode": "revise",
                    "topic": "AI coding 与业务理解",
                    "platform": "wechat",
                    "user_goal": "修公众号稿",
                    "current_draft": "先说一个大背景。",
                    "review_report": {
                        "top_issues": [{"summary": "核心判断出现过晚"}],
                        "rewrite_actions": ["把核心判断提前到第一段"],
                    },
                },
                config,
            )

            self.assertEqual(result["mode"], "prompt_only")
            self.assertIn("不完整", result["reply_text"])

    @patch(
        "writing_brain.writer.call_packy_chat",
        return_value={
            "mode": "model_output",
            "reply_text": (
                "# AI coding 与业务理解\n\n"
                "先说结论，AI 先拉平的不是认知层，而是执行层。很多岗位会先被工具压缩。\n\n"
                "模型版本升级正在抬高能力边界，旧模型判断会滞后。\n\n"
                "中国市场更容易被低价和入口逻辑牵引，所以需要标杆模型重新校准判断。\n\n"
                "Seedance 2.0 就是在把能力边界往前推，"
            ),
            "provider": "packyapi",
            "model": "gemini-3.1-pro-preview",
        },
    )
    @patch.dict("os.environ", {"PACKYAPI_API_KEY": "test-key"}, clear=False)
    def test_gemini_revise_rejects_mid_sentence_truncation_against_long_draft(self, _: object) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = load_config(str(root))
            result = run_writer_turn(
                {
                    "run_id": "writer_revise_bad_ratio",
                    "task_mode": "revise",
                    "topic": "AI coding 与业务理解",
                    "platform": "wechat",
                    "user_goal": "修公众号稿",
                    "current_draft": "这是一篇很长的草稿。" * 120,
                    "review_report": {
                        "top_issues": [{"summary": "中段重复"}],
                        "rewrite_actions": ["压缩第3-4段"],
                    },
                },
                config,
            )

            self.assertEqual(result["mode"], "prompt_only")
            self.assertIn("不完整", result["reply_text"])


if __name__ == "__main__":
    unittest.main()
