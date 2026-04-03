"""Behavioral contract tests for the writing pipeline.

These tests encode the pipeline's behavioral invariants. They exist specifically
to prevent accidental changes to state machine semantics, pipeline step order,
and critical writeback logic — whether by human developers or AI coding assistants.

Rule: if your code change breaks these tests, fix your code, not the tests.
If you genuinely need to change a contract (e.g. intentionally altering the state
machine), explain the reason in the commit message.
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from writing_brain.config import AppConfig, load_config
from writing_brain.pipelines.quality_session import (
    PIPELINE_STEPS,
    _session_status,
    build_quality_evaluation,
    maybe_accept_delivery,
)
from writing_brain.usage import _aggregate, record


class TestPipelineStepOrder(unittest.TestCase):
    """Contract 2: pipeline step order must not change."""

    def test_pipeline_steps_constant_exists_and_matches(self) -> None:
        expected = (
            "assignment", "research", "diagnosis", "blueprint",
            "compose", "quality_evaluation", "image_brief",
            "delivery", "post_review",
        )
        self.assertEqual(PIPELINE_STEPS, expected)


class TestSessionStatusTruthTable(unittest.TestCase):
    """Contract 1: _session_status() truth table.

    This is the single most important contract. It defines when a session
    is blocked, in exception, or ready for acceptance.
    """

    def _call(
        self,
        article: str = "有正文",
        can_continue: bool = True,
        delivery_status: str = "completed",
        post_review_status: str | None = "completed",
    ) -> str:
        quality = {"can_continue_to_delivery": can_continue}
        delivery = {"status": delivery_status}
        post_review = {"status": post_review_status} if post_review_status is not None else None
        return _session_status(
            article_markdown=article,
            quality_evaluation=quality,
            delivery_manifest=delivery,
            post_review_result=post_review,
        )

    def test_no_article_is_blocked(self) -> None:
        self.assertEqual(self._call(article=""), "blocked")

    def test_quality_gate_fail_is_exception(self) -> None:
        self.assertEqual(self._call(can_continue=False), "exception")

    def test_delivery_blocked_is_exception(self) -> None:
        self.assertEqual(self._call(delivery_status="blocked"), "exception")

    def test_delivery_partial_is_exception(self) -> None:
        """Bug 1 regression: partial delivery must be treated as exception."""
        self.assertEqual(self._call(delivery_status="partial"), "exception")

    def test_post_review_failed_is_exception(self) -> None:
        self.assertEqual(self._call(post_review_status="failed"), "exception")

    def test_post_review_partial_is_exception(self) -> None:
        self.assertEqual(self._call(post_review_status="partial"), "exception")

    def test_all_pass_is_awaiting_acceptance(self) -> None:
        self.assertEqual(self._call(), "awaiting_acceptance")

    def test_no_post_review_is_awaiting_acceptance(self) -> None:
        self.assertEqual(self._call(post_review_status=None), "awaiting_acceptance")


class TestAcceptDeliveryWritesBackSession(unittest.TestCase):
    """Contract 3: accept-delivery must update session.json to 'accepted'."""

    def test_acceptance_updates_session_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = load_config(tmp)
            run_id = "contract_test_accept"
            session_data = {
                "contract_name": "session_start_result",
                "contract_version": "v2",
                "run_id": run_id,
                "status": "awaiting_acceptance",
                "assignment": {"topic": "测试", "platform": "wechat", "context_pack": {}},
                "article_markdown": "# 测试\n\n正文内容。",
                "final_review_report": {},
                "quality_evaluation": {
                    "blocked_dimensions": [],
                    "can_continue_to_delivery": True,
                },
                "delivery_manifest": {
                    "status": "completed",
                    "text_delivery": {"status": "passed"},
                    "image_gate": {"status": "passed"},
                },
                "post_review_result": {"status": "completed"},
                "artifact_refs": [],
                "recommended_next_actions": [],
            }
            session_path = config.sessions_dir / f"{run_id}.session.json"
            session_path.parent.mkdir(parents=True, exist_ok=True)
            session_path.write_text(json.dumps(session_data, ensure_ascii=False), encoding="utf-8")

            with patch("writing_brain.pipelines.quality_session.ingest_memory_record") as mock_ingest:
                mock_ingest.return_value = {"archive_refs": []}
                result = maybe_accept_delivery(
                    {"run_id": run_id},
                    config,
                )

            self.assertEqual(result["status"], "accepted")
            # The critical assertion: session.json must be updated
            updated = json.loads(session_path.read_text(encoding="utf-8"))
            self.assertEqual(updated["status"], "accepted")


class TestReviewRunIdPropagation(unittest.TestCase):
    """Contract 4: review_report must receive run_id from the pipeline."""

    @patch("writing_brain.pipelines.contracts.build_review_report")
    def test_review_receives_run_id(self, mock_review: object) -> None:
        mock_review.return_value = {
            "contract_name": "review_report",
            "contract_version": "v1",
            "review_id": "review_test",
            "run_id": "test_run",
            "decision": "pass",
            "total_score": 90,
            "lazy_index": 2,
            "expanded_checks": {
                "argument": {"ok": True},
                "formatting": {"ok": True},
            },
            "top_issues": [],
            "rewrite_actions": [],
            "strengths": [],
            "review_mode": "heuristic_only",
            "review_layers": {},
        }
        assignment = {
            "run_id": "test_run_123",
            "topic": "测试",
            "platform": "wechat",
            "must_cover_points": ["核心判断"],
        }
        build_quality_evaluation(
            {},
            assignment=assignment,
            research_pack={"evidence_items": [], "claim_items": []},
            diagnosis={"primary_archetype": "industry_analysis", "ready_for_compose": True},
            blueprint={"must_cover_points": ["核心判断"], "main_claim": "核心判断"},
            article_markdown="# 测试\n\n正文。\n\n第二段。\n\n第三段。\n\n第四段。",
            context_pack={"topic": "测试", "platform": "wechat"},
        )
        # The critical assertion: run_id must be in the payload passed to build_review_report
        call_payload = mock_review.call_args.args[0]
        self.assertEqual(call_payload["run_id"], "test_run_123")


class TestPartialDeliveryBlocksAcceptance(unittest.TestCase):
    """Contract 5: partial delivery_manifest must block acceptance."""

    def test_partial_delivery_blocks(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = load_config(tmp)
            result = maybe_accept_delivery(
                {
                    "session_result": {
                        "run_id": "partial_test",
                        "status": "awaiting_acceptance",
                        "delivery_manifest": {"status": "partial"},
                        "post_review_result": {"status": "completed"},
                        "artifact_refs": [],
                        "recommended_next_actions": [],
                    }
                },
                config,
            )
            self.assertEqual(result["status"], "blocked")


class TestPublishPackStageReflectsImageReview(unittest.TestCase):
    """Contract 6: publish_pack stage status must reflect image review decision."""

    @patch("writing_brain.post_review.build_publish_pack")
    def test_publish_pack_partial_when_image_review_fails(self, mock_publish: object) -> None:
        from writing_brain.post_review import _run_publish_pack_stage

        mock_publish.return_value = {
            "output_dir": "/tmp/test",
            "image_review_report": {"decision": "revise"},
            "artifact_refs": [],
            "recommended_next_actions": [],
        }
        result = _run_publish_pack_stage(
            {"article_markdown": "# 测试\n\n正文。", "platform": "wechat"},
            AppConfig(data_dir=Path(tempfile.mkdtemp())),
            {"stage": "publish_pack"},
        )
        self.assertEqual(result["status"], "partial")

    @patch("writing_brain.post_review.build_publish_pack")
    def test_publish_pack_completed_when_image_review_passes(self, mock_publish: object) -> None:
        from writing_brain.post_review import _run_publish_pack_stage

        mock_publish.return_value = {
            "output_dir": "/tmp/test",
            "image_review_report": {"decision": "pass"},
            "artifact_refs": [],
            "recommended_next_actions": [],
        }
        result = _run_publish_pack_stage(
            {"article_markdown": "# 测试\n\n正文。", "platform": "wechat"},
            AppConfig(data_dir=Path(tempfile.mkdtemp())),
            {"stage": "publish_pack"},
        )
        self.assertEqual(result["status"], "completed")


class TestUsageAggregationFallback(unittest.TestCase):
    """Contract 7: usage aggregation must fallback when total_tokens is 0."""

    def test_aggregate_fallback_for_missing_total_tokens(self) -> None:
        records = [
            {"provider": "test", "model": "m", "run_id": "r1",
             "prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 0},
            {"provider": "test", "model": "m", "run_id": "r1",
             "prompt_tokens": 200, "completion_tokens": 80, "total_tokens": 280},
        ]
        result = _aggregate(records, period="test")
        # First record: fallback 100+50=150, second: 280. Total: 430
        self.assertEqual(result["total_tokens"], 430)
        self.assertEqual(result["by_provider"]["test"]["total_tokens"], 430)

    def test_record_then_aggregate_with_zero_total(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            record(
                tmp,
                run_id="fallback_test",
                provider="test",
                model="m",
                prompt_tokens=100,
                completion_tokens=50,
                total_tokens=0,
            )
            from writing_brain.usage import query
            result = query(tmp, run_id="fallback_test")
            self.assertEqual(result["total_tokens"], 150)


if __name__ == "__main__":
    unittest.main()
