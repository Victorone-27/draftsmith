# Governance Session Summary 2026-03-24

## 当前有效治理结构

- User: `final_veto`
- Codex: `chancellor_only`
- Claude: 单审 gate + 非本轮写作者时参与双评审
- Gemini 3.1 Pro: 默认首个修稿方 + 非本轮写作者时参与双评审
- gpt-5.4: 轮值修稿方 + 非本轮写作者时参与双评审

核心原则：

- 同一轮里，写作者必须回避投票
- Codex 只做调度、记录、汇总，不写正文，不投票
- 需要用户决策时，必须完整展示 Claude、Gemini、gpt-5.4 三方意见

## 当前正式流程

### 1. 现有母稿入口

- 如果会话已有母稿，直接通过 `manual_draft_text` / `draft_text` 进入治理
- 默认跳过首轮 `draft` 模型调用
- 后续 `revise` 继续允许真实模型调用

### 2. Phase A

- `Gemini revise`
- `Claude gate`
- 若 `Claude pass`，直接汇报用户
- 若 `Claude` 连续驳回达到阈值，进入 Phase B

默认阈值：

- `claude_gate_reject_threshold = 2`

### 3. Phase B

- `Gemini` 写，`Claude + gpt-5.4` 双评审
- 若未通过，`gpt-5.4` 写，`Claude + Gemini` 双评审
- 若仍未通过，`Claude` 写，`gpt-5.4 + Gemini` 双评审

### 4. 通过条件

- 两票都有效
- 平均分 `>= 85`
- 任一票不能为 `rewrite`

### 5. 失败出口

- `Gemini -> gpt-5.4 -> Claude` 一整轮都未通过
- 自动流程停止
- 必须交由用户人工裁决

## Gemini 独立评审稳定化

- 优先走兼容层 `json_schema`
- schema 失败时退回固定五行纯文本合同
- 当前目标是尽量把 Gemini 票稳定为有效票，而不是让 Codex 补裁决

## 当前代码状态

- `writing_brain/workflow.py`: 已接入 `rotation_v2`，并支持“现有母稿优先”
- `writing_brain/review.py`: 已提供 `build_article_review_vote(...)`
- `writing_brain/writer.py`: 已支持 `gpt-5.4` 修稿路由
- `tests/`: 当前全量单测通过
