from __future__ import annotations

import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from writing_brain.cli import main
from writing_brain.config import load_config
from writing_brain.session_ops import accept_delivery, resolve_exception, start_session


class SessionOpsTests(unittest.TestCase):
    @patch("writing_brain.session_ops.run_draft_cycle")
    def test_start_session_wraps_draft_cycle_with_safe_defaults(self, mock_run_draft_cycle: object) -> None:
        mock_run_draft_cycle.return_value = {
            "run_id": "session_demo",
            "final_decision": "pass",
            "context_pack": {"topic": "AI 写作系统"},
            "artifact_refs": ["/tmp/session_demo.cycle.json"],
        }

        with tempfile.TemporaryDirectory() as tmp:
            config = load_config(tmp)
            result = start_session(
                {
                    "topic": "AI 写作系统",
                    "platform": "wechat",
                    "finalize": True,
                    "published_confirmed": True,
                },
                config,
            )

        self.assertEqual(result["status"], "awaiting_acceptance")
        forwarded_payload = mock_run_draft_cycle.call_args.args[0]
        self.assertTrue(forwarded_payload["auto_revise"])
        self.assertTrue(forwarded_payload["enable_post_review_pipeline"])
        self.assertFalse(forwarded_payload["finalize"])
        self.assertFalse(forwarded_payload["published_confirmed"])

    def test_resolve_exception_reports_review_and_image_issues(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = load_config(tmp)
            result = resolve_exception(
                {
                    "cycle_result": {
                        "run_id": "exception_demo",
                        "final_decision": "revise",
                        "artifact_refs": ["/tmp/exception_demo.review.json"],
                        "post_review_result": {
                            "status": "completed",
                            "stages": [
                                {
                                    "stage": "publish_pack",
                                    "result": {
                                        "artifact_refs": ["/tmp/output/article.md"],
                                        "image_review_report": {
                                            "decision": "revise",
                                            "required_actions": ["替换不合规图片"],
                                            "artifact_refs": ["/tmp/output/image-review.json"],
                                        },
                                    },
                                }
                            ],
                        },
                    }
                },
                config,
            )

        self.assertEqual(result["status"], "exception")
        issue_stages = {item["stage"] for item in result["exception_report"]["issues"]}
        self.assertIn("article_review", issue_stages)
        self.assertIn("image_review", issue_stages)
        self.assertIn("delivery_artifacts", issue_stages)

    def test_accept_delivery_blocks_when_exceptions_exist(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = load_config(tmp)
            result = accept_delivery(
                {
                    "cycle_result": {
                        "run_id": "blocked_demo",
                        "final_decision": "revise",
                        "artifact_refs": ["/tmp/blocked_demo.review.json"],
                    }
                },
                config,
            )

        self.assertEqual(result["status"], "blocked")
        self.assertIsNone(result["memory_record"])
        self.assertFalse((Path(tmp) / "sessions" / "blocked_demo.delivery.json").exists())

    def test_accept_delivery_ingests_memory_and_writes_delivery_record(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = load_config(tmp)
            cycle_result = {
                "run_id": "accepted_demo",
                "final_decision": "pass",
                "final_article_markdown": "# 标题\n\n正文。",
                "context_pack": {
                    "topic": "AI 写作系统",
                    "platform": "wechat",
                },
                "artifact_refs": [str(root / "sessions" / "accepted_demo.cycle.json")],
                "post_review_result": {
                    "status": "completed",
                    "stages": [
                        {
                            "stage": "publish_pack",
                            "result": {
                                "platform": "wechat",
                                "output_dir": str(root / "发布版" / "公众号"),
                                "artifact_refs": [
                                    str(root / "发布版" / "公众号" / "AI 写作系统-可直接发布-纯文本可复制.docx")
                                ],
                                "image_review_report": {
                                    "decision": "pass",
                                    "artifact_refs": [str(root / "发布版" / "公众号" / "image-review.json")],
                                },
                            },
                        }
                    ],
                },
            }

            result = accept_delivery(
                {
                    "cycle_result": cycle_result,
                    "session_summary": "本轮已完成母稿、发布包和图片审核。",
                    "human_feedback": {"approved_points": ["可直接发布"]},
                },
                config,
            )

            self.assertEqual(result["status"], "accepted")
            self.assertEqual(result["memory_record"]["contract_name"], "memory_ingest_record")
            self.assertTrue((root / "sessions" / "accepted_demo.delivery.json").exists())
            saved = json.loads((root / "sessions" / "accepted_demo.delivery.json").read_text(encoding="utf-8"))
            self.assertEqual(saved["status"], "accepted")
            self.assertEqual(saved["delivery_summary"]["outputs"][0]["platform"], "wechat")


class CliSurfaceTests(unittest.TestCase):
    def test_help_focuses_on_high_level_session_commands(self) -> None:
        stdout = io.StringIO()
        with patch("sys.argv", ["writing-brain", "--help"]):
            with self.assertRaises(SystemExit) as cm:
                with redirect_stdout(stdout):
                    main()

        rendered = stdout.getvalue()
        self.assertEqual(cm.exception.code, 0)
        self.assertIn("start-session", rendered)
        self.assertIn("resolve-exception", rendered)
        self.assertIn("accept-delivery", rendered)
        self.assertNotIn("draft-cycle", rendered)
        self.assertNotIn("build-publish-pack", rendered)


if __name__ == "__main__":
    unittest.main()
