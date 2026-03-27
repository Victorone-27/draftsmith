from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from writing_brain.config import load_config
from writing_brain.hygiene import collect_cleanup_targets, sanitize_data_dir
from writing_brain.retrieval import load_markdown_items


class HygieneTests(unittest.TestCase):
    def test_collect_cleanup_targets_finds_ds_store_and_runtime_dirs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            publish_dir = root / "publish-packs" / "demo" / "公众号" / "图片"
            runtime_dir = publish_dir / "已生成-test-runtime"
            runtime_dir.mkdir(parents=True)
            (runtime_dir / "封面.png").write_bytes(b"png")
            (root / ".DS_Store").write_text("", encoding="utf-8")

            targets = collect_cleanup_targets(root)

            self.assertEqual(
                [path.relative_to(root).as_posix() for path in targets],
                [
                    "publish-packs/demo/公众号/图片/已生成-test-runtime",
                    ".DS_Store",
                ],
            )

    def test_sanitize_data_dir_apply_removes_targets(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = load_config(tmp)
            root = Path(tmp)
            runtime_dir = root / "publish-packs" / "demo" / "公众号" / "图片" / "已生成-runtime-verify"
            runtime_dir.mkdir(parents=True)
            (root / ".DS_Store").write_text("", encoding="utf-8")

            result = sanitize_data_dir({"apply": True}, config)

            self.assertEqual(result["removed_count"], 2)
            self.assertFalse(runtime_dir.exists())
            self.assertFalse((root / ".DS_Store").exists())

    def test_collect_cleanup_targets_does_not_remove_runtime_dirs_outside_scoped_roots(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            runtime_dir = root / "claims" / "已生成-test-runtime"
            runtime_dir.mkdir(parents=True)

            targets = collect_cleanup_targets(root)

            self.assertEqual(targets, [])

    def test_load_markdown_items_ignores_transient_runtime_dirs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            valid_dir = root / "claims"
            valid_dir.mkdir(parents=True)
            (valid_dir / "good.md").write_text("---\nid: c1\ntitle: 正常文档\n---\n\n正文", encoding="utf-8")

            ignored_dir = root / "claims" / "已生成-test-runtime"
            ignored_dir.mkdir(parents=True)
            (ignored_dir / "noise.md").write_text("---\nid: c2\ntitle: 不该被读到\n---\n\n正文", encoding="utf-8")
            (root / "claims" / ".DS_Store").write_text("", encoding="utf-8")

            items = load_markdown_items(valid_dir, "id", "title")

            self.assertEqual([item.item_id for item in items], ["c1"])


if __name__ == "__main__":
    unittest.main()
