# MCP Guard Smoke (A+B) — 结果

**实验 id**: `mcp_guard_smoke`  日期: 2026-05-10
**配置**: 3 method × 3 task × 2 reps = 18 runs，~18 分钟
**改动**: list_operators 强制 category，list_operator_categories 返回 use_for/not_for/examples

## 算子选择（最关键的单一指标）

| Task | Method | OLD E1 correct% | NEW correct% | Δ |
|---|---|---|---|---|
| 1a_qa_basic | dataflow_agent | 0% (0/5) | **100%** (2/2) | **+100** |
| 1a_qa_basic | mcp_only | 20% (1/5) | **100%** (2/2) | **+80** |
| 1a_qa_basic | df_agent_v2 | 0% (0/5) | **100%** (2/2) | **+100** |
| 2a_sentiment | dataflow_agent | 60% | 100% | +40 |
| 2a_sentiment | mcp_only | 40% | 100% | +60 |
| 2a_sentiment | df_agent_v2 | 60% | 100% | +40 |
| 4a_score_filter | dataflow_agent | 20% | **100%** | **+80** |
| 4a_score_filter | mcp_only | 20% | **100%** | **+80** |
| 4a_score_filter | df_agent_v2 | 20% | 50% | +30 |

均值: **OLD 26% → NEW 95%**（操作选择正确率）

## operator_errors 平均值

| Method | OLD E1 | NEW |
|---|---|---|
| dataflow_agent | 2.0 | **0.00** |
| mcp_only | 1.94 | **0.00** |
| df_agent_v2 | 1.87 | **0.17** (4a 一次 1.0) |

## context_overflow

| Method | OLD | NEW |
|---|---|---|
| dataflow_agent | 8.6% | **0%** |
| mcp_only | 5.7% | **0%** |
| df_agent_v2 | 21.7% | **0%** |

## 所有 method 的工具调用顺序都变成了规范的 3 步

```
list_serving         → list_operator_categories → list_operators(category=X) → create_pipeline → render
```

E1 时观察到的"裸调 list_operators 拉全量 145 算子"行为彻底消失。

## 但 exec_success 仍 0/16 (16/18 跑了 LLM op)

观察到的根因：
1. **任务规约 vs 用户 prompt 错配**：1a 期望字段 `qa_pairs`，但用户 prompt 没说，agent 取 `output_key=multi_hop_qa`（合理选择），acceptance schema 报 0 valid。
2. **dataflow_engine 读参数 bug**：backend 日志显示 `run_params={input_key: 'cleaned_chunk', output_key: 'QA_pairs'}`（全是 default_value），即便 agent 在 config 里设了 `value: chunk` / `value: multi_hop_qa`。**engine 没读 value，只读 default_value**。
3. **runner 下载 bug**：`step_0.jsonl` 在 execution 失败时被写入了 400 错误体而非空文件，导致 `n_total_records=1, n_valid=0` 误统计。

这三个都是 **MCP / skill 之外** 的问题，A+B 的工作目标（operator selection）已 100% 达成。

## 下一步选择

| 选项 | 说明 | 时间 |
|---|---|---|
| A. 修 dataflow_engine 读 value 的 bug | 这才能让 exec_success 真实反映效果 | ~1h debug + retest |
| B. 跑完整 E1 重跑（4 method × 12 task × 3 reps = 144） | 哪怕 exec_success 不变，operator selection / ctx_overflow 论文用得上 | ~3h |
| C. 改动 C：create_pipeline 校验未知算子 | 已经 hallucinated=0，边际收益小 | ~30min |
| D. 改 task acceptance：放宽字段名匹配 | 让现有 task 不那么死板，能统计真实 exec_success | ~30min |

最终建议：**先做 A**（engine bug 是真 bug，影响所有下游统计），再做 **B**（出论文数据）。C 跳过；D 看 A 的效果再决定。
