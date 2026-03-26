from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from writing_brain.config import load_config
from writing_brain.context_pack import build_context_pack
from writing_brain.memory import build_daily_digest, ingest_memory_record


class MemoryTests(unittest.TestCase):
    def test_memory_ingest_writes_runtime_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = load_config(str(root))
            result = ingest_memory_record(
                {
                    "run_id": "run_demo",
                    "topic": "AI coding 与业务理解",
                    "platform": "wechat",
                    "final_article_markdown": "第一段结论。\n\n第二段展开。",
                    "session_summary": "本次先讨论选题，再写稿。",
                    "review_report": {
                        "top_issues": [{"issue_type": "late_thesis"}],
                        "rewrite_actions": ["把结论前置"],
                    },
                    "human_feedback": {
                        "approved_points": ["这次开头够直接"],
                        "criticized_points": ["结尾太虚"],
                        "keep_doing": ["继续保持结论先行"],
                        "avoid_next_time": ["结尾不要空泛升华"]
                    },
                    "context_pack": {
                        "core_claims": [{"title": "真正稀缺的会重新回到业务理解", "summary": "summary"}]
                    },
                },
                config,
            )

            self.assertEqual(result["contract_name"], "memory_ingest_record")
            self.assertTrue((root / "sessions" / "run_demo.json").exists())
            self.assertTrue((root / "feedback-history" / "run_demo.json").exists())
            self.assertTrue((root / "knowledge-entities").exists())
            self.assertTrue((root / "review-history" / "run_demo.json").exists())
            self.assertTrue((root / "articles").exists())
            self.assertTrue(result["knowledge_entities"])

            digest = build_daily_digest({}, config)
            self.assertTrue(digest["recent_topics"])
            self.assertTrue(digest["recent_feedback"])
            self.assertTrue(digest["recent_knowledge"])

    def test_memory_ingest_merges_repeated_knowledge_entities(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = load_config(str(root))

            first = ingest_memory_record(
                {
                    "run_id": "run_one",
                    "topic": "AI coding 与业务理解",
                    "platform": "wechat",
                    "final_article_markdown": "第一版。",
                    "human_feedback": {
                        "approved_points": ["这次开头够直接"],
                        "criticized_points": ["结尾太虚"],
                    },
                },
                config,
            )
            second = ingest_memory_record(
                {
                    "run_id": "run_two",
                    "topic": "AI coding 与业务理解",
                    "platform": "wechat",
                    "final_article_markdown": "第二版。",
                    "human_feedback": {
                        "approved_points": ["这次开头够直接"],
                        "criticized_points": ["结尾太虚"],
                    },
                },
                config,
            )

            success_first = next(item for item in first["knowledge_entities"] if item["entity_type"] == "success_pattern")
            success_second = next(item for item in second["knowledge_entities"] if item["entity_type"] == "success_pattern")
            self.assertEqual(success_first["entity_id"], success_second["entity_id"])
            self.assertEqual(success_second["support_count"], 2)
            self.assertEqual(success_second["source_feedback_run_ids"], ["run_one", "run_two"])
            self.assertGreaterEqual(success_second["confidence"], success_first["confidence"])

            knowledge_files = sorted((root / "knowledge-entities").glob("*.json"))
            self.assertEqual(len(knowledge_files), 2)

            context_pack = build_context_pack(
                {
                    "run_id": "run_three",
                    "topic": "AI coding 与业务理解",
                    "platform": "wechat",
                    "user_goal": "继续写公众号观点文",
                },
                config,
            )
            loaded_success = next(item for item in context_pack["memory_knowledge"] if item["entity_type"] == "success_pattern")
            self.assertEqual(loaded_success["support_count"], 2)
            self.assertIn("开头直接给出判断时，作者认可度更高。", [item["statement"] for item in context_pack["memory_knowledge"]])


if __name__ == "__main__":
    unittest.main()
