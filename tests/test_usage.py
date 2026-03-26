from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from writing_brain.usage import query, record, usage_scope, get_current_context


class UsageTests(unittest.TestCase):
    def test_record_writes_jsonl(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            record(
                tmp,
                run_id="run1",
                provider="packyapi",
                model="gpt-4o",
                caller="writer.draft",
                prompt_tokens=100,
                completion_tokens=50,
                total_tokens=150,
            )
            files = list(Path(tmp, "usage").glob("*.jsonl"))
            self.assertEqual(len(files), 1)
            lines = files[0].read_text(encoding="utf-8").strip().splitlines()
            self.assertEqual(len(lines), 1)
            entry = json.loads(lines[0])
            self.assertEqual(entry["run_id"], "run1")
            self.assertEqual(entry["provider"], "packyapi")
            self.assertEqual(entry["model"], "gpt-4o")
            self.assertEqual(entry["total_tokens"], 150)

    def test_query_by_run_id(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            record(tmp, run_id="run_a", provider="p1", model="m1", caller="c1", prompt_tokens=10, completion_tokens=5, total_tokens=15)
            record(tmp, run_id="run_b", provider="p1", model="m1", caller="c1", prompt_tokens=20, completion_tokens=10, total_tokens=30)
            record(tmp, run_id="run_a", provider="p2", model="m2", caller="c2", prompt_tokens=30, completion_tokens=15, total_tokens=45)

            result = query(tmp, run_id="run_a")
            self.assertEqual(result["total_calls"], 2)
            self.assertEqual(result["total_tokens"], 60)
            self.assertEqual(result["period"], "run_a")

    def test_query_by_date(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            record(tmp, run_id="r1", provider="p1", model="m1", caller="c1", prompt_tokens=100, completion_tokens=50, total_tokens=150)
            # Get the date from the file that was just written
            files = list(Path(tmp, "usage").glob("*.jsonl"))
            date_str = files[0].stem

            result = query(tmp, date=date_str)
            self.assertEqual(result["total_calls"], 1)
            self.assertEqual(result["total_tokens"], 150)

    def test_query_aggregates_by_provider(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            record(tmp, run_id="r1", provider="packyapi", model="gpt-4o", caller="writer", prompt_tokens=100, completion_tokens=50, total_tokens=150)
            record(tmp, run_id="r1", provider="gemini", model="gemini-2.5", caller="review", prompt_tokens=200, completion_tokens=100, total_tokens=300)
            record(tmp, run_id="r1", provider="packyapi", model="gpt-4o", caller="writer", prompt_tokens=80, completion_tokens=40, total_tokens=120)

            result = query(tmp, run_id="r1")
            self.assertEqual(result["by_provider"]["packyapi"]["calls"], 2)
            self.assertEqual(result["by_provider"]["packyapi"]["total_tokens"], 270)
            self.assertEqual(result["by_provider"]["gemini"]["calls"], 1)
            self.assertEqual(result["by_provider"]["gemini"]["total_tokens"], 300)
            self.assertEqual(result["by_model"]["gpt-4o"]["calls"], 2)
            self.assertEqual(result["by_model"]["gemini-2.5"]["calls"], 1)

    def test_usage_scope_sets_context(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsNone(get_current_context())
            with usage_scope(tmp, run_id="test_run", caller_prefix="test"):
                ctx = get_current_context()
                self.assertIsNotNone(ctx)
                self.assertEqual(ctx["run_id"], "test_run")
                self.assertEqual(ctx["data_dir"], tmp)
            self.assertIsNone(get_current_context())
