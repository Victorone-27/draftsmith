from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from writing_brain.config import load_config
from writing_brain.context_pack import build_context_pack


class ContextPackTests(unittest.TestCase):
    def test_build_context_pack_picks_claim_and_structure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "claims").mkdir()
            (root / "structures").mkdir()
            (root / "image-plans").mkdir()
            (root / "feedback-history").mkdir()
            (root / "knowledge-entities").mkdir()
            (root / "claims" / "claim.md").write_text(
                """---
id: c1
title: 真正稀缺的会重新回到业务理解
evidence_strength: medium
usable_for:
  - 核心判断
---

业务理解会重新变得更稀缺。
""",
                encoding="utf-8",
            )
            (root / "structures" / "wechat.md").write_text(
                """---
id: s1
name: 观点型-公众号
fit_platforms:
  - wechat
---

首段先下判断。
""",
                encoding="utf-8",
            )
            (root / "image-plans" / "img.md").write_text(
                """---
id: i1
name: 观点型-概念图加结构图
---

- 先用结构图解释复杂变化。
""",
                encoding="utf-8",
            )
            (root / "feedback-history" / "f1.json").write_text(
                """{
  "run_id": "run_feedback",
  "human_feedback": {
    "keep_doing": ["第一段先给判断"],
    "avoid_next_time": ["不要把结尾写虚"],
    "approved_points": ["开头很直接"],
    "criticized_points": ["结尾不够落地"]
  }
}""",
                encoding="utf-8",
            )
            (root / "knowledge-entities" / "k1.json").write_text(
                """{
  "run_id": "run_feedback",
  "knowledge_entity": {
    "entity_id": "preference_rule_run_feedback_opening",
    "entity_type": "preference_rule",
    "statement": "首段应尽快给出核心判断。",
    "applies_to": {
      "platform": "wechat",
      "topic_keywords": ["ai", "coding", "业务理解"],
      "article_stage": "opening"
    },
    "source_feedback_run_ids": ["run_feedback"],
    "confidence": 0.82,
    "support_count": 1,
    "source": "human_feedback"
  }
}""",
                encoding="utf-8",
            )

            config = load_config(str(root))
            result = build_context_pack(
                {
                    "topic": "AI coding 与业务理解",
                    "platform": "wechat",
                    "user_goal": "写公众号观点文",
                },
                config,
            )

            self.assertEqual(result["contract_name"], "context_pack")
            self.assertEqual(result["platform"], "wechat")
            self.assertEqual(result["core_claims"][0]["claim_id"], "c1")
            self.assertEqual(result["preferred_structures"][0]["structure_id"], "s1")
            self.assertTrue(result["memory_knowledge"])
            self.assertTrue(any("已学习偏好规则" in note for note in result["memory_notes"]))

    def test_build_context_pack_preserves_project_constraints_and_claim_refs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "claims").mkdir()
            (root / "structures").mkdir()
            (root / "image-plans").mkdir()
            (root / "claims" / "claim.md").write_text(
                """---
id: clm-001
title: 入口重要，但模型上限决定谁定义明天
evidence_strength: medium
usable_for:
  - 核心判断
---

入口重要，但模型上限决定谁定义明天。
""",
                encoding="utf-8",
            )
            config = load_config(str(root))
            result = build_context_pack(
                {
                    "topic": "中国 AI 行业最危险的不是落后而是开始适应落后",
                    "platform": "wechat",
                    "user_goal": "写一篇公众号观点文章",
                    "project_claim_refs": ["clm-001"],
                    "constraints": ["不要写成空泛情绪文"],
                    "evidence_needs": ["补 DeepSeek 和百度价格战的外部论据"],
                },
                config,
            )

            self.assertEqual(result["project_claim_refs"], ["clm-001"])
            self.assertEqual(result["project_constraints"], ["不要写成空泛情绪文"])
            self.assertEqual(result["evidence_needs"], ["补 DeepSeek 和百度价格战的外部论据"])
            self.assertEqual(result["core_claims"][0]["claim_id"], "clm-001")
            self.assertTrue(any("项目已显式绑定" in note for note in result["memory_notes"]))


if __name__ == "__main__":
    unittest.main()
