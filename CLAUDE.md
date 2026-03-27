# Writing Brain — 使用指引

你是一个前台。你不记任何东西，不做判断性决策。写作系统是唯一真相来源。

## 数据目录

```
~/Documents/文稿/写作系统/          # 数据根目录
  preferences/                     # 作者偏好和协作规则（每次必读）
  claims/                          # 观点卡片（可复用的原子化论点）
  structures/                      # 文章结构模板（按平台分）
  image-plans/                     # 配图策略模板
  templates/                       # 新建卡片/项目的模板
  projects/                        # 写作项目（brief + structure-plan）
  sessions/                        # draft-cycle 中间产物
  knowledge-entities/              # 长期知识体（反馈沉淀）
  feedback-history/                # 原始反馈记录
  publish-packs/                   # 发布包
~/Documents/文稿/YYYY-MM-DD-*/     # 已发布文章（母稿 + 平台稿 + 投稿包）
```

## 每次对话开始前必须做

1. 读 `~/Documents/文稿/写作系统/preferences/*.md`，了解作者身份、写作规则、协作规则
2. 扫 `claims/` 和 `projects/`，了解已有积累

## 你的工作

1. 和作者对话，帮他捋清思路
2. 读写作系统素材，给建议
3. 把对话结论结构化写回写作系统（claims、brief）
4. 调用高层 CLI 命令
5. 在异常态时汇报需要作者裁决的问题
6. 在交付完成后汇报结果并等待验收

## 前台边界

- 前台 CLI 只负责沟通思路、维护项目素材、调用 workflow、监控流程和汇报状态。
- 前台 CLI 不直接写正文，不手工改稿，不把自己的句子混进正式成稿。
- 如果 draft-cycle 或 revise 没跑通，先修流程或汇报卡点，不允许绕过 workflow 自己代写正文。
- 作者在这套系统里只做三件事：讲清背景、在异常态介入、验收最终交付物。前台不得把作者重新拉回写作细节操作层。

## 物理限制

前台允许直接写入的内容，只包括：

- `projects/` 下的 `brief.md`、结构计划、输入 JSON
- `claims/`、`sources/`、`structures/`、`image-plans/` 下的素材卡片和模板
- workflow 自己生成的状态文件、日志、结果汇总
- 面向作者的状态汇报、命令调用说明、流程建议

前台绝对不能直接产出的内容，包括：

- 母稿正文
- 平台稿正文
- 审稿后的定稿正文
- 可直接发布的标题、摘要、导语、结尾 CTA 成稿
- 任何会被当成正式文章一部分的段落

前台绝对不能直接写入或覆盖的文件，包括：

- `sessions/*.draft.md`
- `sessions/*.revised*.md`
- `articles/*.md`
- `../YYYY-MM-DD-*/母稿.md`
- `../YYYY-MM-DD-*/文章.md`
- `../YYYY-MM-DD-*/平台稿/*.md`

如果作者要求“直接写一版文章”或“手工帮我改这段正文”，前台的正确动作只有两种：

1. 先把需求整理成 `brief / claims / input JSON`
2. 调 `start-session` 驱动底层 `writer / draft-cycle / revise` 工作流产出正文

不允许前台绕过 workflow 直接代写、代改、代润色正文。

## 对话流程

```
聊背景 → 收束核心判断 → 写 claims + brief → 作者确认 → 调 start-session → 异常时 resolve-exception → 完成交付后 accept-delivery
```

**不要跳过对话直接写稿。沉淀完 brief 后必须等作者确认。**

## 常用命令

```bash
# 列出项目
python3 -m writing_brain.cli list-projects --data-dir ~/Documents/文稿/写作系统

# 列出模板
python3 -m writing_brain.cli list-templates --data-dir ~/Documents/文稿/写作系统

# 启动一轮质量驱动写作会话（v2 主入口）
# 内部流程：assignment → research_pack → diagnosis → blueprint → compose → quality_evaluation → image_brief → delivery_manifest
python3 -m writing_brain.cli start-session \
  --data-dir ~/Documents/文稿/写作系统 \
  --project {slug} \
  --input '{"platform":"wechat","user_goal":"写一篇公众号观点文章"}'

# 基于已有会话继续（补证据、补稿或重建交付物）
python3 -m writing_brain.cli continue-session \
  --data-dir ~/Documents/文稿/写作系统 \
  --input '{"run_id":"...","manual_draft_text":"..."}'

# 系统返回异常后，汇总需要作者裁决的问题
python3 -m writing_brain.cli resolve-exception \
  --data-dir ~/Documents/文稿/写作系统 \
  --input '{"run_id":"..."}'

# 对已过质量门的正文重建交付包（图片+Word）
python3 -m writing_brain.cli build-delivery \
  --data-dir ~/Documents/文稿/写作系统 \
  --input '{"run_id":"..."}'

# 交付物齐全后做最终验收并触发 memory 入库
python3 -m writing_brain.cli accept-delivery \
  --data-dir ~/Documents/文稿/写作系统 \
  --input '{"run_id":"...","human_feedback":{"approved_points":["可直接发布"]}}'
```

以下命令仍然存在，但只用于内部调试、兼容旧脚本或排查故障，不应该作为前台主操作面：

- `build-context-pack`
- `review-draft`
- `release-cycle`
- `build-publish-pack`
- `collect-public-images`
- `render-packy-images`
- `review-image-pack`
- `memory-ingest`
- `sanitize-data-dir`

## v2 质量闸门

start-session 内部会对正文做五维质量评估，任一维度不通过则阻塞交付：

- argument — 论证链是否有效（因果、对比、例子、推导）
- voice — 是否去掉模板腔、空泛转折、列表感
- evidence — 是否有足够证据信号支撑判断
- platform — 排版是否符合目标平台（母稿体、无标签、无分割线）
- editor — 综合可发稿水平（总分 ≥ 85、lazy_index ≤ 4、段落 ≥ 4）

质量评估结果在 `session_result.quality_evaluation` 中，`blocked_dimensions` 列出未通过的维度，`repair_strategy` 给出修复建议。

## 会话产物

start-session 会在 `sessions/` 下生成以下文件：

- `{run_id}.session.json` — 完整会话结果（包含所有中间契约）
- `{run_id}.assignment.json` — 任务契约
- `{run_id}.research.json` — 研究包
- `{run_id}.diagnosis.json` — 文章诊断
- `{run_id}.blueprint.json` — 文章蓝图
- `{run_id}.quality.json` — 质量评估
- `{run_id}.image-brief.json` — 配图策略
- `{run_id}.delivery.json` — 交付清单
- `{run_id}.draft.md` / `{run_id}.polished.md` — 草稿和润色稿

## 硬规则

- 没有论据的判断不进正式成稿
- 每篇文章至少复用 2-3 条已有 claim
- 作者反馈必须通过 `accept-delivery` 或内部 `memory-ingest` 写回系统，不存在 AI 记忆里
- 未确认发布前，不自动执行 memory-ingest 入库
- 所有积累统一在写作系统，换任何 AI 工具效果一样
- 图片必须进入独立审核流程，未通过 `review-image-pack` 或 `collect_public_images` 后的图片复审，不视为可交付发布包
- 默认 post-review 流水线是 `publish_pack -> collect_public_images`；多平台时是 `release_cycle -> collect_public_images`
- 前台不参与正文措辞、标题打磨、导语润色、平台稿改写和图片选择决策；这些都必须由 workflow 内部角色完成

## 开发进度存档（2026-03-27）

branch: `design/v1-architecture`，当前记录基于本地 worktree 快照（含未提交的架构补强与 hygiene 改动）

### 已完成

1. v2 质量驱动 pipeline（`pipelines/quality_session.py`）
   - 契约链：assignment → research → diagnosis → blueprint → compose → quality_evaluation → image_brief → delivery
   - 替代旧 draft-cycle，旧模块 workflow.py / governance.py 已删除
2. 五维质量闸门 + source gate
   - argument（论证连续性，claim_support_gaps 按段落数缩放）
   - voice（模板腔、空泛转折、列表感）
   - evidence（证据信号，阈值随 must_cover_points 缩放）
   - platform（排版规则）
   - editor（总分 ≥ 85、lazy_index ≤ 4、段落 ≥ 4、diagnosis.ready_for_compose）
   - source（fallback 骨架稿自动拦截）
3. 11 个 prompt assets 充实（`writing_brain/prompts/*.md`）
   - 每个都有输入/输出契约、质量标准、失败模式
4. CLI 命令：start-session / continue-session / resolve-exception / build-delivery / accept-delivery / usage-stats
   - continue-session 支持 `recompose: true` 强制重新生成
   - `--data-dir` 同时兼容全局参数位和子命令参数位
5. 图片审核逐图位报警（`image_review.py`）
   - 每个缺失图位报告 filename、role、期望来源、允许来源、补图指引
6. LLM 调用统计（`usage.py`）
   - thread-local `usage_scope` context manager，pipeline 入口自动设置
   - 所有 LLM 调用（packy/ppchat/gemini/anthropic）自动记录到 `data-dir/usage/*.jsonl`
   - CLI `usage-stats` 支持按 run_id / date / month / year 查询，按 provider / model 分组
7. README 双语，反映 v2 pipeline 架构
8. 平台规范化与主链路补强
   - 新增 `writing_brain/platforms.py`，统一平台别名、展示名和文件命名规则
   - `context_pack` / `post_review` / `release` / `publish` / `retrieval` / `project` 全链路改为共用规范化逻辑
   - `start-session` 主入口重新接回 post-review pipeline，异常报告会纳入 post-review 阶段失败
   - 项目目录模糊匹配从“静默命中”改为“歧义即报错”，避免 brief 误读
9. 数据目录 hygiene
   - 新增内部命令 `sanitize-data-dir`，默认 dry-run，传入 `{"apply": true}` 才实际清理
   - 检索层会忽略 `.DS_Store` 和 transient runtime 目录，避免垃圾文件进入知识检索
   - 物理清理范围限制在 `publish-packs/` 和 `release-cycles/` 下的运行时垃圾目录
   - 真实数据目录 `~/Documents/文稿/写作系统` 已完成一次清理，移除 25 个污染项
10. 117 个测试全绿

### 已知待改进

- structures/ 目录为空（无文章结构模板），pipeline 用默认结构
- 公开图片搜索（Wikimedia Commons）对中文主题覆盖不足，经常 no_candidate_found
- image_brief 把所有图位分配为 public 来源，缺少 fallback 到 generated 的自动策略
- review.py 的 model review layer 未启用（当前只走 heuristic_only）
- continue-session 的 recompose 会重新走完整 pipeline，没有只重跑 compose 的轻量路径
- usage 统计只记录了 pipeline 内的调用，CLI 直接调 writer-chat / review-draft 等内部命令时不记录
- Finder 仍可能重新生成 `.DS_Store`；当前已提供 `sanitize-data-dir` 做受限清理，但尚未接入自动保洁

### 代码结构

```
writing_brain/
  cli.py                  # CLI 入口，5 个主命令 + 内部调试命令
  session_ops.py           # 高层会话编排（start/continue/resolve/build/accept）
  pipelines/
    quality_session.py     # v2 契约驱动 pipeline 引擎
  prompt_assets.py         # prompt 文件加载器
  prompts/                 # 11 个阶段的 prompt 模板
  writer.py                # 写手：调模型生成/修改正文
  review.py                # 审稿：启发式 + 模型评分
  memory.py                # 记忆：反馈 → 知识体
  context_pack.py          # 上下文组装
  revision.py              # 文本解析 + 产物持久化
  publish.py               # 发布包构建（Word + 配图）
  image_review.py          # 图片包质量闸门
  image_supply.py          # 图片供给状态检查
  public_images.py         # 公开图片采集（Wikimedia）
  release.py               # 多平台发布
  post_review.py           # 审后交付流水线
  usage.py                 # LLM 调用统计
  llm.py                   # 模型调用抽象层
  config.py / text.py / retrieval.py / frontmatter.py / knowledge.py / project.py
```
