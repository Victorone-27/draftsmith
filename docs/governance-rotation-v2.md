# Governance Rotation V2

## 1. 目标

这套治理规则的核心目标只有一条：

- 不让同一个模型在同一轮里既当运动员又当裁判

Codex 在这套规则里只负责流程编排、结果汇总、产物落盘，不直接写正文，也不参与裁决。

用户保留 `final_veto`。

## 2. 角色

- `Gemini 3.1 Pro`
  - 可担任写作者
  - 可担任评审
  - 当轮若负责修稿，则自动回避投票

- `gpt-5.4`
  - 可担任写作者
  - 可担任评审
  - 当轮若负责修稿，则自动回避投票

- `Claude`
  - 可担任写作者
  - 可担任评审
  - 当轮若负责修稿，则自动回避投票

- `Codex`
  - `chancellor_only`
  - 不直接写文章
  - 不参与裁决
  - 只负责调度、记录、汇总和传达用户指令

- `User`
  - `final_veto`

## 3. 总原则

- 谁写，谁该轮不投票
- 写作方之外的两方负责双评审
- 双评审的平均分用于通过判断
- 任一票给出 `rewrite`，该轮直接视为不通过
- 一整轮轮值后仍未通过，必须交由用户人工裁决

## 4. 正式流程

### Phase 0: 现有母稿优先

如果当前会话已经有母稿：

1. `draft-cycle` 直接以 `manual_draft_text` / `draft_text` 作为当前稿件
2. 首轮 `draft` 模型调用会被跳过
3. 后续 `revise` 仍可正常调用 `Gemini / gpt-5.4 / Claude`

只有在明确要求 `prefer_model_draft_over_manual = true` 时，系统才会忽略现有母稿、重新起草。

### Phase A: Gemini 先修，Claude 单审

1. `Gemini revise` 先出一版修稿
2. `Claude` 单独审核
3. 如果 `Claude pass`，本轮通过，交给用户
4. 如果 `Claude` 连续驳回达到阈值，进入轮值治理阶段

默认阈值：

- `claude_gate_reject_threshold = 2`

### Phase B: 轮值修稿 + 双评审

进入该阶段后，按固定顺序跑一整轮：

1. `Gemini` 作为写作者
   - `Claude + gpt-5.4` 双评审

2. 若未通过，改由 `gpt-5.4` 写
   - `Claude + Gemini` 双评审

3. 若仍未通过，改由 `Claude` 写
   - `gpt-5.4 + Gemini` 双评审

这三步走完，算一整轮循环。

## 5. 通过规则

双评审通过条件：

- 两票都必须有效
- `average_score >= 85`
- 任一票不能为 `rewrite`

默认参数：

- `pair_pass_average_score = 85`

## 6. 人工裁决出口

如果 `Gemini -> gpt-5.4 -> Claude` 这一整轮都没有通过：

- 不再继续自动修稿
- 必须进入用户人工裁决

## 7. 当前代码实现边界

在当前版本中：

- 新治理状态机挂在 `draft-cycle`，通过 `governance_workflow_mode = "rotation_v2"` 启用
- 如果提供现有母稿，默认不重新起草，而是直接从母稿进入治理
- 老的 `review-draft` 三权包保留为兼容路径
- 新状态机负责：
  - `Gemini revise -> Claude gate`
  - 升级后的轮值修稿
  - 写作方回避
  - 双评审平均分判断
  - 一整轮后人工裁决出口

## 8. Gemini 独立评审路由

独立评审默认优先走兼容层的 `json_schema` 路由；如果 relay 输出仍不稳定，再退回固定五行纯文本合同：

1. 先尝试 schema 输出
2. 解析失败时再退回 `SCORE / DECISION / ISSUE / ACTION / STRENGTH`
3. 无论哪条路，Gemini 都只作为独立评审，不参与程序层异常裁决

这一步的目标不是让 Codex 补票，而是尽量把 Gemini 自己的票稳定成有效票。
