# Draftsmith

A contract-driven writing system with three roles: **write**, **review**, **remember**.

一套契约驱动的三角色写作系统：写、审、记。

---

## What is Draftsmith / 这是什么

Draftsmith is not an auto-posting bot. It's a workflow skeleton for a personal writing team — one that drafts, critiques, and learns from every round.

Draftsmith 不是自动发文机器人，而是一个"会写、会审、会记"的个人写作团队骨架。

Three roles, clear boundaries:

| Role | Responsibility |
|------|---------------|
| **Writer** | Brainstorm, draft, revise / 拆题、写初稿、改稿 |
| **Reviewer** | Score, find issues, detect laziness / 打分、挑错、判断偷懒 |
| **Memory** | Collect materials, ingest feedback, build knowledge / 整理资料、沉淀反馈、构建知识体 |

The author only does three things:

1. Describe the goal, background, and constraints / 讲清背景
2. Intervene only on exceptions / 异常时裁决
3. Accept the final delivery / 验收交付物

Everything else — drafting, reviewing, revising, publishing, image sourcing — is handled by the workflow.

## How it works / 工作流程

```
memory ──> context_pack ──> writer ──> draft ──> reviewer ──> review_report
                                        ^                        |
                                        |                        v
                                   revised_draft <────────── writer

final_article + review_report + session_summary ──> memory ──> knowledge base
```

Three contracts bind the roles together:

- `context_pack` — everything the writer needs before starting
- `review_report` — structured scoring with a laziness index
- `memory_ingest_record` — feedback distilled into reusable knowledge

Swap the model, swap the machine — the contracts stay the same.

换模型、换电脑，契约不变。

## Architecture / 架构

```
┌─────────────────────────────────────────────┐
│  Frontend (Claude Skill / CLI)              │  ← talks to the author
│  Organizes brief & claims, invokes workflow │
├─────────────────────────────────────────────┤
│  Data Layer (local filesystem)              │  ← single source of truth
│  claims/ structures/ projects/ sessions/    │
│  knowledge-entities/ publish-packs/         │
├─────────────────────────────────────────────┤
│  Workflow Engine (Python)                   │  ← does the actual work
│  writer / reviewer / memory / governance    │
└─────────────────────────────────────────────┘
```

Code and data are separated. The repo holds code and schemas; your writing materials live outside in a `data-dir` you control.

代码和数据分离。仓库只放代码和契约，写作资料放在你自己的数据目录里。

## Quick Start / 快速开始

```bash
git clone https://github.com/Victorone-27/draftsmith.git
cd draftsmith
pip install -e .
```

Point to your data directory:

```bash
# Start a full writing session
python3 -m writing_brain.cli start-session \
  --data-dir ~/your-writing-data \
  --project your-project-slug \
  --input '{"platform":"wechat","auto_revise":true}'

# When the system hits an exception, summarize what needs your decision
python3 -m writing_brain.cli resolve-exception \
  --data-dir ~/your-writing-data \
  --input '{"run_id":"..."}'

# Accept delivery and trigger memory ingest
python3 -m writing_brain.cli accept-delivery \
  --data-dir ~/your-writing-data \
  --input '{"run_id":"...","human_feedback":{"approved_points":["ready to publish"]}}'
```

These three commands are the only entry points you need:

| Command | Purpose |
|---------|---------|
| `start-session` | Launch a full draft → review → revise cycle / 启动完整写作会话 |
| `resolve-exception` | Surface blockers for author decision / 汇总需要裁决的异常 |
| `accept-delivery` | Confirm final output, trigger memory ingest / 验收交付，经验入库 |

## Project Structure / 项目结构

```
draftsmith/
  writing_brain/
    cli.py              # CLI entry point
    workflow.py          # Draft-cycle orchestration
    governance.py        # Rotation governance state machine
    writer.py            # Writer role
    review.py            # Reviewer role (rule + model layers)
    memory.py            # Memory role
    context_pack.py      # Context assembly
    knowledge.py         # Knowledge persistence & merging
    publish.py           # Publish pack builder
    release.py           # Multi-platform release
    post_review.py       # Post-review pipeline
    llm.py               # Model call abstraction
  schemas/               # JSON schemas for all contracts
  docs/                  # Architecture & governance docs
  examples/              # Example inputs & outputs
```

## Feedback Loop / 反馈闭环

Your feedback doesn't just get filed away — it gets distilled.

你的反馈不是存档，而是被提炼成知识体。

```
Raw feedback
  → approved_points / criticized_points / keep_doing / avoid_next_time
    → preference_rule / anti_pattern / success_pattern
```

When the same feedback appears repeatedly, the system merges it: `support_count` goes up, `confidence` increases, and `source_feedback_run_ids` tracks which sessions validated the pattern.

Next time you write, the system reads patterns — not memos.

下次写作前，系统读到的是规律，不是备忘录。

## Review Scoring / 审稿评分

Total score out of 100, plus a laziness index (0–10):

| Dimension | Weight |
|-----------|--------|
| Alignment | 25 |
| Completeness | 20 |
| Evidence | 15 |
| Structure | 15 |
| Style fit | 15 |
| Platform fit | 10 |

- Score >= 85 & laziness <= 4 → pass
- 70–84 → revise
- < 70 → rewrite
- Laziness >= 7 → reject immediately

## License

MIT
