# V1 Architecture

## 1. 目标

V1 不是做一套复杂的多 Agent 平台，而是做一个能稳定运行的个人写作系统。

核心目标：

- 把“写”“审”“记”拆开
- 三个角色共享一个资料库
- 所有中间产物都有明确结构
- 支持从当前电脑迁移到更强的电脑继续使用
- 作者的认可和批评都能进入下一轮上下文

这里的“进入下一轮上下文”，不是原话照抄，而是抽象成知识体。

## 2. 非目标

V1 不做这些事：

- 不做自动发布
- 不做本地大模型训练
- 不做复杂网页工作台
- 不做多用户协作
- 不做全自动热点爬虫集群

## 3. 三角色边界

### Writer

输入：

- 作者当前需求
- `context_pack`
- 历史对话摘要

输出：

- `brief`
- `draft`
- `revised_draft`

职责：

- 跟作者聊选题和角度
- 把思路结构化
- 生成初稿
- 根据审稿意见改稿

### Reviewer

输入：

- `draft`
- `review_policy`
- `context_pack`

输出：

- `review_report`

职责：

- 评分
- 找问题
- 判断是否偷懒
- 判断是否跑偏

### Memory

输入：

- 历史文章
- 会话摘要
- 审稿结果
- 作者长期关注主题
- 外部热点

输出：

- `context_pack`
- `memory_ingest_record`
- `daily_digest`

职责：

- 平时收集和整理资料
- 生成本次写作材料包
- 收纳最终稿
- 更新规则和摘要
- 记录作者明确认可和批评的点
- 把反馈抽象成可复用知识体

## 4. 数据流

```text
memory -> context_pack -> writer -> draft -> reviewer -> review_report
                                           ^              |
                                           |              v
                                     revised_draft <- writer

final_article + review_report + session_summary -> memory -> knowledge base
```

## 5. 统一中间产物

V1 只固定三份核心契约：

1. `context_pack`
2. `review_report`
3. `memory_ingest_record`

好处：

- 换模型不换结构
- 换电脑不换结构
- 将来加 UI 或 CLI 也不改核心数据流
- 作者反馈可以成为下一轮真实约束

反馈闭环分三层：

1. 原始反馈
2. 反馈解释
3. 长期知识体

V1 先把第 1 层和第 3 层落下来。

## 6. 审稿评分逻辑

总分 100，另加一个 `偷懒指数`。

评分项：

- `alignment`：25
- `completeness`：20
- `evidence`：15
- `structure`：15
- `style_fit`：15
- `platform_fit`：10

偷懒指数范围：

- `0-10`

### 通过规则

- `score >= 85` 且 `lazy_index <= 4`：通过
- `70 <= score < 85`：退回修改
- `score < 70`：重写
- `lazy_index >= 7`：直接退回

### 偷懒判断信号

- 用户要求的点覆盖不全
- 多段重复表达同一个意思
- 大量空泛过渡句
- 关键判断没有展开
- 只有结论，没有证据或例子
- 模板味太重
- 平台差异改写过浅

## 7. 资料库最小结构

V1 建议先用文件系统 + 轻量索引。

### 长期资料

- `claims/`
- `sources/`
- `structures/`
- `image-plans/`
- `platform-rules/`
- `style-rules/`
- `articles/`

### 运行沉淀

- `sessions/`
- `feedback-history/`
- `knowledge-entities/`
- `review-history/`
- `daily-digests/`
- `hotspots/`

## 8. 部署建议

### V1 推荐形态

- Python 后端脚本或轻量服务
- SQLite
- JSON schema
- repo 外 data root
- 全部模型通过 API 调用

### 为什么这样设计

- 轻
- 好迁移
- 本机压力小
- 容易在第二台电脑复现

## 9. 模型建议

### V1

- `writer.brainstorm`：当前 GPT 对话层
- `writer.draft` / `writer.revise`：Gemini 路线
- `reviewer`：先用本地规则打分，后续再接独立评分模型
- `memory`：更便宜的总结模型

### V2

当以下情况出现时，再拆不同模型：

- 审稿太软
- 写作成本太高
- 每日总结量明显上升
- 需要并发处理多个项目

## 10. 实现顺序

### Phase 1

- 定义 schema
- 搭建 data root
- 实现 `build_context_pack`
- 实现 `review_draft`

### Phase 2

- 实现 `memory_ingest`
- 实现 `daily_digest`
- 让 `writer` 先读 `context_pack` 再开聊
- 串起 `draft-cycle`

### Phase 3

- 再决定是否上网页
- 再决定是否拆多模型
- 再决定是否做真正的 agent runtime
