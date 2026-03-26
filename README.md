# Draftsmith

A contract-driven writing system: plan, compose, gate, deliver.

一套契约驱动的写作系统：规划、成稿、质量闸门、交付。

---

## What is Draftsmith / 这是什么

Draftsmith is not an auto-posting bot. It's a pipeline that turns a topic into a publishable article — with explicit quality gates at every stage.

Draftsmith 不是自动发文机器人，而是一条从选题到交付的流水线，每个阶段都有明确的质量闸门。

The author only does three things:

1. Describe the goal, background, and constraints / 讲清背景
2. Intervene only on exceptions / 异常时裁决
3. Accept the final delivery / 验收交付物

Everything else — planning, drafting, reviewing, packaging, image sourcing — is handled by the pipeline.

## How it works / 工作流程

```
context_pack
  → assignment        freeze the task spec
    → research_pack   gather claims, evidence, memory
      → diagnosis     classify article archetype & risks
        → blueprint   define argument structure & sections
          → compose   writer produces the article
            → quality_evaluation   five-dimension gate
              → image_brief   derive image requirements
                → delivery_manifest   build publish package
                  → accept_delivery   ingest into memory
```

Key contracts bind the stages:

- `assignment_contract` — frozen task definition (topic, platform, constraints, must-cover points)
- `article_blueprint` — argument structure, main claim, section plan
- `quality_evaluation` — five-dimension pass/block with repair strategy
- `delivery_manifest` — text delivery + image gate + rich delivery status
- `memory_ingest_record` — feedback distilled into reusable knowledge

Swap the model, swap the machine — the contracts stay the same.

换模型、换电脑，契约不变。

## Quality Gate / 质量闸门

Total score out of 100, with five hard dimensions that must all pass before delivery:

| Dimension | What it checks |
|-----------|---------------|
| **Argument** | Causal chains, contrast, examples, claim-support gaps |
| **Voice** | No template tone, no filler, no list-style writing |
| **Evidence** | Sufficient evidence signals to support claims |
| **Platform** | Formatting matches target platform (no labels, no dividers) |
| **Editor** | Overall publishability: score ≥ 85, laziness ≤ 4, ≥ 4 paragraphs |

Any blocked dimension triggers `revise` or `rewrite` with a specific repair strategy.

## Architecture / 架构

```
┌─────────────────────────────────────────────┐
│  Frontend (Claude Skill / CLI)              │  ← talks to the author
│  Organizes brief & claims, invokes session  │
├─────────────────────────────────────────────┤
│  Data Layer (local filesystem)              │  ← single source of truth
│  claims/ structures/ projects/ sessions/    │
│  knowledge-entities/ publish-packs/         │
├─────────────────────────────────────────────┤
│  Session Pipeline (Python)                  │  ← does the actual work
│  quality_session / writer / review / memory │
│  publish / image_review / release           │
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
# Start a quality-driven writing session
python3 -m writing_brain.cli start-session \
  --data-dir ~/your-writing-data \
  --project your-project-slug \
  --input '{"platform":"wechat","user_goal":"写一篇公众号观点文章"}'

# Continue from a prior session (e.g. with a manual draft)
python3 -m writing_brain.cli continue-session \
  --data-dir ~/your-writing-data \
  --input '{"run_id":"...","manual_draft_text":"..."}'

# When the system hits an exception, summarize what needs your decision
python3 -m writing_brain.cli resolve-exception \
  --data-dir ~/your-writing-data \
  --input '{"run_id":"..."}'

# Rebuild delivery package after text passes quality gate
python3 -m writing_brain.cli build-delivery \
  --data-dir ~/your-writing-data \
  --input '{"run_id":"..."}'

# Accept delivery and trigger memory ingest
python3 -m writing_brain.cli accept-delivery \
  --data-dir ~/your-writing-data \
  --input '{"run_id":"...","human_feedback":{"approved_points":["ready to publish"]}}'
```

| Command | Purpose |
|---------|---------|
| `start-session` | Run the full pipeline: plan → compose → gate → deliver / 启动完整写作会话 |
| `continue-session` | Resume from prior session with new input / 基于已有会话继续 |
| `resolve-exception` | Surface blockers for author decision / 汇总需要裁决的异常 |
| `build-delivery` | Rebuild package after quality gate passes / 重建交付包 |
| `accept-delivery` | Confirm final output, trigger memory ingest / 验收交付，经验入库 |

## Project Structure / 项目结构

```
draftsmith/
  writing_brain/
    cli.py                # CLI entry point
    session_ops.py        # High-level session orchestration
    pipelines/
      quality_session.py  # V2 contract-driven pipeline engine
    prompt_assets.py      # Prompt file loader
    prompts/              # Prompt templates for each stage
    writer.py             # Writer: draft & revise via LLM
    review.py             # Reviewer: heuristic + model scoring
    memory.py             # Memory: feedback → knowledge entities
    context_pack.py       # Context assembly from data-dir
    publish.py            # Publish pack builder (Word + images)
    image_review.py       # Image package quality gate
    public_images.py      # Public image collection (Wikimedia)
    release.py            # Multi-platform release
    post_review.py        # Post-review delivery pipeline
    workflow.py           # Legacy draft-cycle (internal/debug)
    governance.py         # Rotation governance (internal/debug)
    llm.py                # Model call abstraction
  schemas/                # JSON schemas for all contracts
  docs/                   # Architecture docs
  examples/               # Example inputs & outputs
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

## License

MIT
