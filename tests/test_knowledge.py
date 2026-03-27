"""Tests for memory and knowledge merging behavior."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from writing_brain.knowledge import merge_knowledge_entity, persist_knowledge_entities


class TestKnowledgeMerge(unittest.TestCase):
    def test_merge_combines_runs_and_boosts_confidence(self) -> None:
        existing = {
            "entity_type": "rule",
            "statement": "不要用首先其次",
            "source_feedback_run_ids": ["run1"],
            "support_count": 1,
            "confidence": 0.5,
        }
        incoming = {
            "entity_type": "rule",
            "statement": "不要用首先其次",
            "source_feedback_run_ids": ["run2"],
            "support_count": 1,
            "confidence": 0.5,
        }
        merged = merge_knowledge_entity(existing, incoming)
        self.assertEqual(merged["support_count"], 2)
        self.assertEqual(sorted(merged["source_feedback_run_ids"]), ["run1", "run2"])
        # Confidence should increment by 0.02
        self.assertAlmostEqual(merged["confidence"], 0.52)

    def test_merge_caps_confidence(self) -> None:
        existing = {
            "entity_type": "rule",
            "statement": "test",
            "support_count": 50,
            "confidence": 0.94,
        }
        incoming = {
            "entity_type": "rule",
            "statement": "test",
            "support_count": 1,
            "confidence": 0.5,
        }
        merged = merge_knowledge_entity(existing, incoming)
        self.assertEqual(merged["confidence"], 0.95)

    def test_persist_updates_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            # Persist first
            entities = [{"entity_type": "test", "statement": "a", "confidence": 0.5, "source_feedback_run_ids": ["r1"]}]
            persisted = persist_knowledge_entities(root, entities)
            self.assertEqual(len(persisted), 1)
            file_count = len(list(root.glob("*.json")))
            self.assertEqual(file_count, 1)

            # Persist again with same statement but different run
            entities2 = [{"entity_type": "test", "statement": "a", "confidence": 0.6, "source_feedback_run_ids": ["r2"]}]
            persisted2 = persist_knowledge_entities(root, entities2)

            # Should overwrite, not create new
            file_count2 = len(list(root.glob("*.json")))
            self.assertEqual(file_count2, 1)
            self.assertEqual(persisted2[0]["support_count"], 2)


if __name__ == "__main__":
    unittest.main()
