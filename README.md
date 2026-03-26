# Writing Brain

`Writing Brain` 是一套面向个人作者的三角色写作系统：

- `writer`：和作者聊天、拆题、写初稿、改稿
- `reviewer`：打分、挑错、判断是否偷懒或跑偏
- `memory`：整理热点、沉淀资料、收纳文章、记录复盘

它不是一个“自动发文机器人”，而是一个“会写、会审、会记”的写作团队骨架。

在这套系统里，作者只做三件事：

1. 把背景、目标、限制讲清楚
2. 只在审稿、图片审核或交付异常时介入裁决
3. 验收最终交付物

正文写作、审稿、修稿、发布包整理、图片补齐、图片审核、多平台改写和经验入库，都应该由 workflow 自动完成。

这里的“记”，不仅记文章，也记作者的人工反馈：

- 你认可的地方
- 你批评的地方
- 下次继续保持什么
- 下次避免什么

但喂给系统的不是这些原话本身，而是从反馈里抽象出来的知识体。

## V1 目标

V1 先解决六件事：

1. 写作前先组装一份统一的 `context_pack`
2. 写作后用统一评分表做审稿
3. 通过后的文章和复盘进入统一资料库
4. 代码和数据分离，方便换电脑继续使用
5. 作者的认可和批评进入下一轮参考
6. 把人工操作面收敛成“启动会话 / 异常裁决 / 最终验收”

## 前后台角色

对作者可见的主入口，应该是会话编排，而不是前台亲自参与写稿。

后台角色：

- `writer` 负责正文与修稿
- `reviewer` 负责质量控制
- `image review` 负责图片与图文包复核
- `memory` 负责资料准备和经验沉淀

推荐流程：

1. 作者先把背景、目标和限制讲清楚
2. 系统生成 `context_pack`
3. `writer` 产出初稿并自动进入审稿
4. `reviewer` 输出评分、问题和修稿建议
5. `writer` 自动修稿，直到通过或触发治理异常
6. 审稿通过后，触发 `post-review pipeline`
7. `post-review pipeline` 生成可复制 Word、图文发布包、多平台稿和配图资产
8. 图片进入独立审核流程，未通过则进入异常态
9. 作者只在异常态做裁决，或在交付物齐全后做最终验收
10. `memory` 在验收后入库文章、规则、反馈和总结

## CLI 边界

CLI 的主职责只有三件事：

1. `start-session`：启动一轮完整 workflow
2. `resolve-exception`：把当前需要作者裁决的异常汇总出来
3. `accept-delivery`：确认最终交付物并触发 memory 入库

低层命令仍保留给调试和兼容场景，但不应该作为主操作面。CLI 不直接参与正文写作决策，也不手工补正文、标题、导语或平台稿。

## 仓库结构

```text
writing-brain/
  README.md
  writing_brain/
    cli.py              # CLI 入口，支持 --project 参数
    config.py           # 数据目录配置
    workflow.py          # draft-cycle 主流程编排
    governance.py        # 轮值治理状态机（rotation_v2）
    revision.py          # 修稿轮次、产物写入
    writer.py            # writer 角色
    review.py            # reviewer 角色
    memory.py            # memory 角色
    context_pack.py      # 上下文组装（含已发布文章检索）
    retrieval.py         # 文件检索与排序
    knowledge.py         # 知识体持久化与合并
    project.py           # 项目/模板加载
    publish.py           # 发布包构建
    release.py           # 多平台发布编排
    post_review.py       # 审稿通过后的扩展流水线
    frontmatter.py       # Markdown frontmatter 解析
    llm.py               # 模型调用封装
    text.py              # 文本工具函数
  docs/
    v1-architecture.md
    governance-rotation-v2.md
  schemas/
    context-pack.schema.json
    review-report.schema.json
    memory-ingest.schema.json
    draft-cycle-result.schema.json
  examples/
    context-pack.example.json
    review-report.example.json
    memory-ingest.example.json
    draft-cycle-input.example.json
  config/
    models.example.json
```

## 数据目录约定

仓库只放代码和契约，不放你的长期资料。

运行数据建议放到 repo 外，例如：

```text
~/Documents/文稿/
  写作系统/                # data_dir
    claims/
    sources/
    structures/
    image-plans/
    templates/
    projects/
    sessions/
    articles/
    knowledge-entities/
    feedback-history/
    review-history/
    daily-digests/
    hotspots/
  2026-03-19-xxx/          # published_dir（已发布文章）
  2026-03-20-yyy/
```

`published_dir` 默认为 `data_dir` 的父目录。系统会自动扫描 `YYYY-MM-DD-*` 目录读取已发布文章，纳入 `context_pack` 的 `recent_articles` 字段。

这样以后你可以：

- 在这台电脑开发
- 推到 Git
- 在另一台性能更好的电脑 `git clone`
- 把数据目录拷过去继续跑

## 本机压力判断

V1 默认不跑本地大模型，只调在线模型 API。

所以本机主要负担是：

- 读写文件
- SQLite 或轻量索引
- 调外部模型
- 生成总结和 JSON

这对普通笔记本通常是可承受的。

真正重的情况通常来自：

- 本地跑大模型
- 本地大规模 OCR
- 批量向量化
- 多任务并发过多

## 模型策略

V1 建议先这样：

- `writer.brainstorm`：走当前 GPT 对话层，负责和你聊、收束思路
- `writer.draft` / `writer.revise`：走 Gemini 路线，负责出初稿和修稿
- 当前默认实现是：`draft` 走 Gemini 中转，`revise` 走 Claude 中转
- `reviewer`：V1 先用本地规则打分，后面再接独立评分模型
- `memory`：优先用更便宜的模型做总结和归档

V2 再考虑把 `writer` 和 `reviewer` 拆成不同模型，减少“自己写自己放过自己”的问题。

模型配置示例见 [models.example.json](/Users/wangzhihao/writing-brain/config/models.example.json)。

## 下一步

当前仓库已经有可跑的 V1 骨架，接下来优先补强闭环质量。

实现优先级建议：

1. 把 `draft-cycle` 接上真实可用的 Gemini 通道
2. 给 reviewer 增加可插拔的模型评分层
3. 强化 memory 知识体合并和置信度更新
4. 持续稳定异常治理、交付验收和 memory 入库契约

## 人工反馈怎么进入系统

人工反馈分两层：

- 原始反馈原样保存
- 反馈再提炼成下一次写作前的提醒

建议每次至少给这四类中的一部分：

- `approved_points`
- `criticized_points`
- `keep_doing`
- `avoid_next_time`

这样系统不会只记“文章写了什么”，还会记“你为什么认可或不认可”。

系统内部会把这些反馈沉淀成三类长期记忆：

- `preference_rule`
- `anti_pattern`
- `success_pattern`

也就是说，下次写作前读到的会更像“规律”，而不是“口头备忘录”。

如果同一类反馈反复出现，系统不会一直新增碎片记录，而会合并成同一个知识体：

- `support_count` 会增加
- `confidence` 会逐步提高
- `source_feedback_run_ids` 会记录这条规律被哪些轮次反复验证过

## 当前可用命令

仓库根目录下直接运行：

```bash
python3 -m writing_brain.cli start-session --project 某个项目 --input '{"platform":"wechat"}'
python3 -m writing_brain.cli resolve-exception --input '{"run_id":"某次会话 run_id"}'
python3 -m writing_brain.cli accept-delivery --input '{"run_id":"某次会话 run_id","human_feedback":{"approved_points":["可直接发布"]}}'
```

`start-session` 会启动完整 `draft-cycle`。稿件通过 reviewer 后，会默认触发一条可扩展的 `post_review_pipeline`。
单平台默认 stage 是 `publish_pack -> collect_public_images`，会先生成“可直接发布-纯文本可复制.docx”和图文发布包，再自动尝试补公共图并刷新 Word。
如果 payload 中存在多个 `target_platforms`，默认 stage 会切到 `release_cycle -> collect_public_images`，直接生成多平台平台稿、投稿包和 Word 文档，再逐个平台补公共图。
后续如果要接 LLM 拓写、找图、生成图，可以继续往这条 pipeline 里加 stage，而不用改写审稿主流程。
现在已支持可选 stage `collect_public_images`，会基于 `图片/公开图片线索.md` 检索 Wikimedia Commons 公共图、刷新 Word，并重新产出图片审核结果。

如果 `start-session` 返回异常态，用 `resolve-exception` 汇总当前阻塞项。
只有当文章审稿、图片审核和交付物都通过后，才应该执行 `accept-delivery`。
`accept-delivery` 会输出交付摘要，并把本轮经验写入 memory。

### 内部调试命令

`build-context-pack`、`review-draft`、`draft-cycle`、`release-cycle`、`build-publish-pack`、`review-image-pack`、`collect-public-images`、`render-packy-images`、`memory-ingest` 等命令仍然保留，供调试、兼容旧脚本或排查流程时使用，但不是推荐的人工主入口。

图片生成默认走 `IKUN` 图片通道：

- 默认模型：`nano2`
- 默认基地址：`https://api.ikuncode.cc`
- 推荐环境变量：`IKUN_IMAGE_API_KEY`
- 兼容回退：`WRITING_BRAIN_IMAGE_API_KEY`、`IKUN_API_KEY`
- `nano2` 会按别名映射到真实请求模型 `gemini-3.1-flash-image-preview`

如果要走 `PACKYAPI` 图片通道，请单独配置图片专用环境变量：

- `PACKYAPI_IMAGE_API_KEY`
- 可选：`PACKYAPI_IMAGE_BASE_URL`，默认 `https://www.packyapi.com`
- 可选：`PACKYAPI_IMAGE_MODEL`，默认 `gemini-3.1-flash-image-preview`

不要直接复用聊天通道的 `PACKYAPI_API_KEY` / `PACKYAPI_BASE_URL` 作为图片配置。

如果主图片通道是 `IKUN`，但你希望在主渠道异常时自动切到备用通道，可额外传或配置：

- `image_fallback_provider` / `IMAGE_FALLBACK_PROVIDER`
- `image_fallback_api_key` / `IMAGE_FALLBACK_API_KEY`
- `image_fallback_base_url` / `IMAGE_FALLBACK_BASE_URL`
- 可选：`image_fallback_model` / `IMAGE_FALLBACK_MODEL`

备用通道只会在主通道已按同一模型重试完仍失败后再触发；如果未单独指定 fallback model，默认沿用主通道模型。

`memory-ingest` 现在主要作为内部调试命令保留。
推荐路径是通过 `accept-delivery` 统一完成最终验收和 memory 入库。
只有显式传入 `finalize=true` 或 `published_confirmed=true`，才会在 `draft-cycle` 结束后自动入库。

### 用项目驱动写作

可以用 `--project` 参数从项目 brief 加载上下文，省去手动拼 JSON：

```bash
# 列出所有项目
writing-brain list-projects

# 用项目 brief 驱动 start-session
writing-brain start-session \
  --project 2026-03-21-测试-ai-coding与业务理解 \
  --input '{"platform":"wechat"}'

# 也可以只传 slug 的一部分，系统会模糊匹配
writing-brain start-session \
  --project ai-coding与业务理解 \
  --input '{"platform":"wechat"}'
```

`--project` 会读取项目目录下的 `brief.md`，把 topic、tone、constraints、claim refs 等字段合并到 payload 里。`--input` 里显式传的参数优先。

任何外部工具都应该优先通过 `start-session / resolve-exception / accept-delivery` 这组三段式入口调用完整工作流；低层命令仅用于调试或兼容旧流程。

`writer-chat` 默认先读 `context_pack` 和长期知识体，再决定是否调用外部模型。

- `brainstorm`：默认不调用 Gemini，留给当前 GPT 对话层来聊和收束思路
- `draft` / `revise`：默认尝试调用 Gemini
- 没配模型或模型不可用时：自动退回 `prompt_only`

这样即使模型暂时不可用，你也能先拿到一份完整、高约束的写作提示。

`review-draft` 现在支持双层审稿：

- 规则层：默认必跑，负责覆盖率、结构、偷懒指数这些硬信号
- 模型层：按需开启，负责判断语义完整度、论证扎实度、是否真的在说人话

如果要开启模型层，可以在输入里加 `"use_model_reviewer": true`，或者设置环境变量 `WRITING_BRAIN_ENABLE_MODEL_REVIEWER=true`。

推荐用法：

```bash
# 先用当前 GPT 层聊清楚怎么写
python3 -m writing_brain.cli writer-chat --input @examples/writer-chat-input.example.json

# 一键串起初稿 -> 审稿 -> 修稿
python3 -m writing_brain.cli draft-cycle --input @examples/draft-cycle-input.example.json

# 需要拆开跑时，也可以单独调 draft / revise
python3 -m writing_brain.cli writer-chat --input @examples/writer-draft-input.example.json
python3 -m writing_brain.cli writer-chat --input @examples/writer-revise-input.example.json
```

`draft-cycle` 会：

- 自动构建 `context_pack`
- 先跑一轮 `draft`
- 用 reviewer 打分
- 需要时自动触发 `revise`
- 把中间稿件保存到 `sessions/`
- 在你允许时把最终稿和反馈送进 `memory`

如果要启用“写作方回避投票”的轮值治理，请在输入里加：

```json
{
  "use_governance_review": true,
  "governance_workflow_mode": "rotation_v2"
}
```

治理规则见 [governance-rotation-v2.md](/Users/wangzhihao/writing-brain/docs/governance-rotation-v2.md)。

如果模型通道暂时不可用，它会停在 `awaiting_draft`，同时把可继续使用的 `writer_prompt` 留给你。

如果要直接读取你现有的写作系统：

```bash
python3 -m writing_brain.cli \
  --data-dir ~/Documents/文稿/写作系统 \
  build-context-pack \
  --input '{"topic":"AI coding 与业务理解","platform":"wechat","user_goal":"写一篇公众号观点文章"}'
```
