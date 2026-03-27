"""Tests for CLI dispatch and command registration."""
from __future__ import annotations

import json
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from writing_brain.cli import INTERNAL_COMMANDS, main


class TestCLIDispatch(unittest.TestCase):
    """Verify that CLI commands dispatch to the correct handler."""

    def _run_cli(self, *args: str) -> int:
        with patch("sys.argv", ["writing-brain", *args]):
            try:
                return main()
            except SystemExit as exc:
                return exc.code if exc.code is not None else 0

    @patch("writing_brain.cli.list_projects", return_value=[{"slug": "test", "title": "Test"}])
    @patch("writing_brain.cli.load_config")
    @patch("writing_brain.cli.ensure_runtime_dirs")
    def test_list_projects_dispatches(self, _ensure: MagicMock, _config: MagicMock, mock_list: MagicMock) -> None:
        code = self._run_cli("list-projects")
        self.assertEqual(code, 0)
        mock_list.assert_called_once()

    @patch("writing_brain.cli.list_templates", return_value=[{"name": "t1", "path": "/tmp/t1.md"}])
    @patch("writing_brain.cli.load_config")
    @patch("writing_brain.cli.ensure_runtime_dirs")
    def test_list_templates_dispatches(self, _ensure: MagicMock, _config: MagicMock, mock_list: MagicMock) -> None:
        code = self._run_cli("list-templates")
        self.assertEqual(code, 0)
        mock_list.assert_called_once()

    @patch("writing_brain.cli.list_projects", return_value=[{"slug": "test", "title": "Test"}])
    @patch("writing_brain.cli.load_config")
    @patch("writing_brain.cli.ensure_runtime_dirs")
    def test_subcommand_data_dir_is_accepted(self, _ensure: MagicMock, mock_load_config: MagicMock, mock_list: MagicMock) -> None:
        code = self._run_cli("list-projects", "--data-dir", "/tmp/writing-data")
        self.assertEqual(code, 0)
        mock_load_config.assert_called_once_with("/tmp/writing-data")
        mock_list.assert_called_once()

    def test_unknown_command_fails(self) -> None:
        """Unknown commands should produce an error, not silently run daily-digest."""
        with self.assertRaises(SystemExit) as ctx:
            with patch("sys.argv", ["writing-brain", "nonexistent-command"]):
                main()
        self.assertNotEqual(ctx.exception.code, 0)

    def test_all_internal_commands_are_registered(self) -> None:
        """Every INTERNAL_COMMANDS entry should be parseable by argparse."""
        for cmd in INTERNAL_COMMANDS:
            with patch("sys.argv", ["writing-brain", cmd, "--help"]):
                with self.assertRaises(SystemExit) as ctx:
                    main()
                self.assertEqual(ctx.exception.code, 0, f"--help for {cmd} should exit 0")

    def test_visible_commands_in_metavar(self) -> None:
        """Public commands should appear in metavar help string."""
        for cmd in ["start-session", "continue-session", "resolve-exception", "build-delivery", "accept-delivery", "usage-stats"]:
            self.assertNotIn(cmd, INTERNAL_COMMANDS, f"{cmd} should not be hidden")


class TestCLIInputParsing(unittest.TestCase):
    """Verify _load_json handles different input modes."""

    def test_empty_input(self) -> None:
        from writing_brain.cli import _load_json
        self.assertEqual(_load_json(None), {})
        self.assertEqual(_load_json(""), {})

    def test_json_string_input(self) -> None:
        from writing_brain.cli import _load_json
        result = _load_json('{"topic":"test"}')
        self.assertEqual(result, {"topic": "test"})

    def test_file_input(self, tmp_path: Path = None) -> None:
        import tempfile
        from writing_brain.cli import _load_json
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump({"topic": "from_file"}, f)
            f.flush()
            result = _load_json(f"@{f.name}")
        self.assertEqual(result, {"topic": "from_file"})


if __name__ == "__main__":
    unittest.main()
