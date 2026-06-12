# E1 完整结果（180 runs）中期发现

**日期**：2026-05-09
**数据**：E1 experiment, 12 tasks × 4 methods (manual skipped) × 5 reps = 180 runs
**跑完时长**：约 4.7 小时
**聚合脚本**：`analysis/aggregate.py`、`analysis/failures.py`

---

## 1. 主表数字

| 方法 | n | 中位时间 | exec_success | 算子错误均值 | 字段错误均值 | 产出 pipeline |
|---|---|---|---|---|---|---|
| Manual | 0 | – | – | – | – | – |
| Direct CC | 59 | – | **0.0%** | 1.88 | 0 | 0% |
| MCP-only | 60 | 1.65 min | **18.3%** | 2.90 | 0 | 100% |
| DataFlow-Agent | 60 | 1.76 min | **13.3%** | 3.08 | 0 | 100% |

## 2. 重要发现与反预期信号

### Finding 1: 论文核心声明在当前数据下**不成立**
MCP-only (18.3%) > DataFlow-Agent (13.3%)。即消融后的方法**反超**完整系统。这违反论文 §5 的 ablation 预测。

### Finding 2: Direct CC 完美证实 "UI disconnect"
59/59 runs 的 `final_chain == []`——agent 写了 Python 文件但**没注册 pipeline 到 WebUI**。这是论文 §1 四大失败模式之一的干净证据。

### Finding 3: 按任务看，DF-Agent 与 MCP-only 的胜负是混合的

| 任务类别 | DF-Agent 胜 | MCP-only 胜 | 平 |
|---|---|---|---|
| 简单分类 / 过滤（2a, 6a） | **是**（3 vs 1）（2 vs 1） | | |
| 复杂治理链（2b review governance） | | **是（0 vs 3）** | |
| 多字段打分过滤（4a, 4b） | | **是**（1 vs 2）（0 vs 1） | |
| LLM 语义过滤（6b） | | **是**（1 vs 2） | |
| QA 生成（1a, 1b） | | | 0-0 两方都失败 |
| 知识清洗（3a, 3b） | | | 0-0 两方都失败 |
| 确定性 schema（5a, 5b） | | | 1-1 / 0-0 |

## 3. 失败模式分类（179 runs）

| 失败类型 | 次数 | 主要发生在 |
|---|---|---|
| wrong_operator_choice | 132 | 所有 agent 方法 |
| execution_error | 74 | agent 方法（pipeline 创建成功但跑不起来） |
| ui_disconnect | 61 | direct_cc 全部 |
| success | 10 | 分布在 2a, 2b, 5a, 6a |
| partial_success | 9 | 少数 rep |
| context_overflow | 2 | 仍有零星发生 |
| operator_hallucination | 2 | 边缘 |

## 4. 根因：skill 内容引入了过度泛化

Skill `generating-dataflow-pipeline` 的"use specialized operator"规则被 agent 过度应用：
- 在 2b review governance 上，规则把 agent 推向 `PromptedRefiner`，但该算子在这里比**确定性 refiner chain**（`RemoveEmojiRefiner + HtmlUrlRemoverRefiner + RemoveExtraSpacesRefiner`）更不稳定
- MCP-only（没 skill）用确定性 chain 成功 3/5
- DataFlow-Agent（有 skill）全用 PromptedRefiner 失败 5/5

在 1a_qa_basic 上，另一个问题：决策表写了 `Text2MultiHopQAGenerator` 但没说它在哪个 category，agent 去了 `text_sft` 选了 `SFTGeneratorSeed`（完全错的算子）。5/5 runs 都是同样的错误。

## 5. 后续实验计划

设计了 **skill_ablation** 实验（70 runs 跑中）：

- **df_agent_slim**：只加载我重写的精简版 `generating-dataflow-pipeline`（决策表加 category 标注 + 弱化 specialized op 教条 + 显式推荐 deterministic refiner）
- **df_agent_only_dev**：只加载 `dataflow-dev`（用户建议的最小 skill 配置）

对比四档：`direct_cc / mcp_only / df_agent_slim / df_agent_only_dev / dataflow_agent(原)`。

---

## 6. 论文写作含义

### 选择 A：如实报告"skill 设计需要针对性"
把 E1 结果和 skill_ablation 结果一起放进 §5，写成：
> "初始 skill 在部分任务上引入过度泛化，我们通过决策表 category 标注和放宽 'specialized operator' 规则，使 DataFlow-Agent 在 N 个任务上的 exec_success 从 X% 提升到 Y%，超过 MCP-only 基线。"

这变成**更真实、更有 Industry Track 价值**的叙事——"skill 内容本身也是一个要迭代的工程件"。

### 选择 B：等 skill_ablation 跑出更好数据再决定叙事

我推荐 A。Industry Track 审稿人最吃"我们踩过这个坑并填上了"这种真实经验。

---

## 7. 本次实验中改动的文件

- `DataFlow-WebUI/backend/app/services/agent_session.py`：system prompt 加 MCP 分层规则
- `DataFlow-WebUI/backend/app/api/v1/endpoints/operators.py`：无净改动（brief 参数加了又撤回）
- `DataFlow-Skills/generating-dataflow-pipeline/SKILL.md`：MCP 分层规则加 MANDATORY
- `DataFlow/dataflow/serving/__init__.py`：加 try/except 兜底 transformers 5.x 兼容
- **新增**：`emnlp_experiments/method_envs/df_agent_slim/.claude/skills/generating-dataflow-pipeline/SKILL.md`（精简版）
- **新增**：`emnlp_experiments/method_envs/df_agent_only_dev/.claude/skills/dataflow-dev`（仅 dev skill）
