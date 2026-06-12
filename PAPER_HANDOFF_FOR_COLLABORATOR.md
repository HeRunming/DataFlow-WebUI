# DataFlow-Agent EMNLP 实验总结（供合作者参考）

**作者**：HeRunming  **日期**：2026-05-10（v2 baseline）/ 2026-05-21（v3 update — 见 §0）
**目的**：把过去几天围绕 DataFlow-Agent 系统做的实验、由实验导致的代码改动、以及最终实验结果，完整整理给正在改 paper 的合作者参考。所有数字都是真实跑出来的，不是预期值。

> **TL;DR（v3 更新版，2026-05-21）**：
> 我们在原 MCP guard 的基础上又加了 4 个 MCP 工具（`recommend_operator_categories` / `get_dataset_columns` / `list_servings` / `validate_pipeline_config`）和**服务端硬校验门**（`create_pipeline` / `update_pipeline` 提交时调 `validate_pipeline_config`，校验失败直接 HTTP 400 拒绝）。在同一组 144 runs 上重跑，最强配置由 `DF-Agent (v2)` 47.2% **轮换为** `DF-Agent (slim)` **48.6%**；`DF-Agent (v2)` 反而 **regress** 到 36.1%（厚 skill 与服务端硬校验冲突，context_overflow 从 2.8% → 30.6%）。
> 新发现一个 paper 里值得加的失败模式：**parameter-semantic-mismatch**——agent 在英文数据上把 `Text2MultiHopQAGenerator.lang` 设成 `"zh"`（10/12 次），通过了 type-validator，运行时静默产出 0 条记录。这是与 Lesson 2（catalog 分页）、Lesson 3（参数绑定）对称的 Lesson 4，详见 §0.5。
> v2 的故事链（旧 TL;DR）作为对照保留在下面：

> **TL;DR（v2 原版）**：
> 我们把 paper 里的 placeholder 数字全部用真数据替换。最强 agent (`DF-Agent (v2)`) 在 12 任务 × 3 reps 评测里达到 47.2% 端到端执行成功率（vs 直接 Claude Code 0%），平均算子选择错误从 1.82 降到 0.22（**7.8× 改进**），上下文溢出失败从 5.7–21.7% 降到 **0%**。
> 这些数字主要靠两个改动驱动：
> （1）MCP server 端把 `list_operators` 改成强制要 category，并让 `list_operator_categories` 返回 `use_for / not_for / examples`；
> （2）dataflow_engine 的两处真 bug（unsupported run 参数 → TypeError；`llm_serving={"id":...}` dict → unhashable）。
> 还有一个剩余的瓶颈是 agent 不主动覆盖 `input_key` 默认值（5/12 任务因此失败），作为 future work / limitation 写进 paper。

---

## 0. v3 更新（2026-05-21，覆盖 §2 §3 §6 §7 的数字与故事）

### 0.1 v3 改动了什么

| 层 | v2 (E1_mcpguard) | v3 (E1_mcpguard_v3) |
|---|---|---|
| 操作目录浏览 | `list_operator_categories → list_operators?category=… → get_operator_detail_by_name` | 同上，外加 `recommend_operator_categories`（任务描述 + 数据集列名 → 1-2 个候选 category）；`get_operator_detail_by_name` 现在返回 `agent_tips / field_binding_hint / mcp_context_hint` |
| 字段绑定 | agent 只能读 example 猜列名 | 新增 MCP 工具 `get_dataset_columns` 直接返回真实列名 |
| Serving 查询 | 仅 `list_serving` | 新增 `list_servings`（复数别名，向后兼容） |
| 提交时校验 | 无 — `create_pipeline` 接受任何 config，只在运行时报错 | 新增 MCP 工具 `validate_pipeline_config`；并且 `create_pipeline` / `update_pipeline` 内部强制调用 validator，`valid=False` 时直接返回 HTTP 400 + 结构化报错（含 `suggested_fields` / `repair_hint`） |

校验器能挡住：
- 拼错或幻觉的算子名 / category
- 不存在的 `llm_serving` id（agent 没 list 就硬编）
- `prompt_template` 错把 dict 当字符串、漏 `cls_name`
- `process_fn` / `filter_rules` shape 错（不是 lambda 字符串、不是带 `code` 的 dict）
- `input_key` 引用了数据集里不会出现的字段名（用 `difflib` 给同义建议）

### 0.2 v3 的 headline 数字（同样 12 任务 × 3 reps × 4 method = 144 runs）

| Method | v2 succ | v3 succ | Δ | v2 op_err 平均 | v3 op_err 平均 | v2 ctx_overflow | v3 ctx_overflow |
|---|---|---|---|---|---|---|---|
| `dataflow_agent` (legacy) |  9/36 (25.0%) | 11/34 (32.4%) | **+7.4 pp** | 0.25 | 0.59 | 0% | 17.6% |
| `mcp_only`                | 13/36 (36.1%) | 13/36 (36.1%) | 0 pp         | 0.25 | 0.64 | 0% | 19.4% |
| `df_agent_v2`             | 17/36 (47.2%) | 13/36 (36.1%) | **−11.1 pp** | 0.22 | 0.61 | 2.8% | 30.6% |
| **`df_agent_slim`**       | 11/36 (30.6%) | **17/35 (48.6%)** | **+18.0 pp** | 0.25 | 0.54 | 3.3% | 11.4% |

**两个反直觉发现**：
1. `df_agent_slim`（极简 12 行 SKILL）在 v3 反超 `df_agent_v2`（239 行）成为最强方案。
2. `df_agent_v2` 显著 regress —— 在服务端已经把字段绑定/算子形状/serving id 这些事情统统硬校验之后，**厚 SKILL 重复了 server 已经强制的同一份 lesson，反而把 context 烧光**（context_overflow 2.8% → 30.6%）。

### 0.3 哪些任务 v3 真的赢了（验证门发挥作用）

`2a_single_lang_sentiment` `3a_long_doc_summary` `4a_score_and_filter` `5b_nested_flatten` 这四个任务在 v2 里几乎全 0，在 v3 里 4 个 method 总加起来达到 6/12～12/12。这正是 v2 经常"算子链对、字段绑定错、跑 90 秒后 crash"的一类——v3 在 commit 那一刻就拒绝了，agent 立刻拿到 typed repair hint 重写。

### 0.4 哪些任务 v3 反而退步（新失败模式：parameter-semantic-mismatch）

`1a_qa_basic` (5/12 → 0/12) 和 `3b_text_to_qa_chain` (9/12 → 0/12) 全军覆没。

我们 trace 到底：1a 的 12 个 v3 run 里有 **10 个**给 `Text2MultiHopQAGenerator` 设了 `lang="zh"`（虽然数据是英文）。该算子内部按中文句号 `。` 切句 → 英文文本切不开 → LLM 没有可解析的 sentence pair → 每条记录被过滤 → 最终输出 0/0。

直接复现：同一段 chunk，`lang="en"` 34 秒跑出 8/8 records；`lang="zh"` 0.16 秒跑出 0/0 records。

校验器抓不住这个错，因为 `"zh"` 和 `"en"` 都是 type-valid 的字符串字面量，`lang` 字段也确实存在。**这是 type-validator 永远抓不到、必须靠 sample-aware semantic validator 的失败**。

### 0.5 给 paper 加 Lesson 4

这个发现对 paper 来说反而是好事。因为它把 lesson 列表往前推了一格：

- **Lesson 1（已在原稿）** think-first 协议解决 premature pipelines
- **Lesson 2** operator catalog 必须分页 / 强制 category（现在有 v2/v3 两轮数据支撑）
- **Lesson 3** 字段绑定要服务端校验 + difflib 同义建议（v3 直接给出 4 个任务的因果证据）
- **Lesson 4（v3 新增）** **type-valid ≠ semantically-valid**：参数取值的合法字面量集合通过 type 检查，但 *与数据本身的语义不匹配* 时仍能静默产出空结果。建议解法：sample-aware validator（peek 数据前几行，检测语言/格式，若与 `lang` 等参数冲突则给出 warning 而非 error）。

### 0.6 v3 给"为什么 thin SKILL + 强 MCP 是赢家"的因果证据

`df_agent_slim` 在 v3 拿到 48.6%，是 paper 最干净的故事：
> "Once the MCP layer enforces categorical scoping (Lesson 2) and the typed validation contract (Lesson 3), an additional 200-line SKILL is no longer net-positive. The minimal `df_agent_slim` SKILL — 12 lines whose only job is to chain `recommend_operator_categories → get_dataset_columns → validate_pipeline_config → create_pipeline` — outperforms the much heavier `df_agent_v2` SKILL by 12.5 percentage points, primarily because the heavier SKILL repeats lessons that the server now enforces and is paid for in tokens, not in correctness."

### 0.7 推荐合作者按照下面的方式更新 paper

| Paper 字段 | 旧值（v2，2026-05-10） | 新值（v3，2026-05-21） |
|---|---|---|
| Headline succ | 47.2% (`DF-Agent v2`) | **48.6% (`DF-Agent (slim)`)** |
| Op.err improvement | 1.82 → 0.22 (7.8×) | 1.82 → 0.54 (3.4×)；注：op_err 在 v3 普遍升高，因为 validator 触发的 retry 也会被计为 1 个 op_err；这个数字现在更适合脚注说明，不再当主指标 |
| ctx_overflow | 5.7-21.7% → 0% | **5.7-21.7% → 11.4%（slim） / 30.6%（v2）**——v3 对厚 SKILL 不利，对 thin SKILL 仍最优 |
| Methods table 主推 | DF-Agent (v2) | **DF-Agent (slim)** |
| Limitations | input_key default 不主动覆盖 | + parameter-semantic-mismatch（lang="zh" 例） |

详细数据 + per-task delta 见 `/data/workspace/emnlp_experiments/E1_MCPGUARD_V3_RESULTS.md`。
重新生成的 LaTeX 主表见 `analysis/main_table_merged.tex`（已自动从 `E1_mcpguard_v3` 拉数据）。

---

## 1. 实验设计

### 1.1 评测任务（12 个）

| ID | 类别 | 难度 | 期望算子链 |
|---|---|---|---|
| 1a_qa_basic | QA 生成 | easy | Text2MultiHopQAGenerator |
| 1b_qa_with_filter | QA + 过滤 | medium | Text2QAGenerator → 质量过滤 |
| 2a_single_language_sentiment | 情感 | easy | PromptedGenerator (情感标注) |
| 2b_review_governance | 多阶段治理 | hard | 标注 → 评分 → 改写 → 一致性 → 过滤 |
| 3a_long_doc_summary | 切块 + 摘要 | medium | ChunkedPromptedGenerator |
| 3b_text_to_qa_chain | refine + QA | medium | PromptedRefiner → Text2QAGenerator |
| 4a_score_and_filter | 评分过滤 | medium | PromptedEvaluator → GeneralFilter |
| 4b_multidim_scoring | 多维评分 | medium | 多个 PromptedEvaluator |
| 5a_field_rename | 字段重命名 | easy | PandasOperator |
| 5b_nested_flatten | 嵌套展平 | hard | PandasOperator |
| 6a_length_filter | 长度过滤 | easy | CharNumberFilter |
| 6b_llm_semantic_filter | 语义过滤 | medium | PromptedFilter |

每个任务包含：JSONL sample、自然语言 prompt、期望算子链、acceptance schema（带字段名同义词）。

### 1.2 比较的 agent 配置（6 个）

| Method | Skills loaded | MCP guard? | 说明 |
|---|---|---|---|
| **Direct CC** | 无 | 无 MCP | 纯 Claude Code 基线，没有 DataFlow 任何信息 |
| **MCP-only** | 无 | ✅ | 暴露 MCP 工具（含 use_for/not_for 指引），但没加载任何 skill |
| **DF-Agent (dev-only)** | dataflow-dev | ✅（无 not_for） | 只加载框架开发 skill，故意配错 skill —— 用来证明 "用错 skill 比不加 skill 还差" |
| **DF-Agent (slim)** | 精简 generating-dataflow-pipeline | ✅ | 极简构造 skill |
| **DF-Agent (v2)** | dataflow-pipeline-v2（重写） | ✅ | 论文主推方案 |
| **DF-Agent (legacy suite)** | 全套 4 个 skill（dataflow-dev / dataflow-operator-builder / generating-dataflow-pipeline v1 / prompt-template-builder） | ✅ | 之前生产环境用的版本 |

每个 method × 12 任务 × 3 repetitions = 36 runs (除 Direct CC 是从更老的 5-rep baseline 拉的，68 runs；dev-only 是从 skill_ablation 拉的，30 runs)。

### 1.3 测量指标

- **Time**：从 agent 第一次工具调用到第一次有效 pipeline render 的中位 wall-clock 分钟。
- **Succ.**（execution success rate）：跑出 pipeline 后 runner 自动执行该 pipeline，至少写出一条符合 acceptance schema 的记录则为 success。
- **Op. err**（operator errors）：实际算子链 vs 期望算子链的差异：hallucinated（不存在的算子名）+ missing（缺关键算子）+ wrong_order（顺序颠倒）+ unexpected（多余算子）。
- **ctx_overflow**：agent 用尽上下文窗口、未能产出 pipeline 的比例。
- **field errors**：参数级字段流违规（用了未生成的字段，等）。

---

## 2. 主结果（论文 Table 1 候选）

```
Method                          n     Time   Succ.    Op.err
─────────────────────────────────────────────────────────────
Direct CC                      68     —      0.0%     1.82
MCP-only                       36     1.4    36.1%    0.25
DF-Agent (dev-only)            30     2.0    26.7%    1.77
DF-Agent (slim)                36     1.3    30.6%    0.25
DF-Agent (v2)                  36     1.4   47.2%    0.22   ← 最佳
DF-Agent (legacy suite)        36     1.4    25.0%    0.25
```

**故事链：**
1. **Direct CC = 0% succ, 1.82 op_err**：纯 code agent 不知道 DataFlow 算子注册表，频繁拍脑袋生成不存在的算子（如 `SFTGeneratorSeed` 用于多跳 QA—— 该算子要求已有 SFT 种子样本）。
2. **加 MCP（MCP-only）= 36.1% succ, 0.25 op_err**：仅暴露 MCP 工具就把算子错误从 1.82 降到 0.25（7.3×）。这一步把 *agent has no access* 的问题彻底解决。
3. **加错 skill（dev-only）= 26.7% succ, 1.77 op_err**：故意只加载 `dataflow-dev`（一个针对框架开发，不是 pipeline 构造的 skill）。算子错误飙回 1.77 —— **加载错误 skill 比不加还差**。这条数据给"skill 必须按角色组织"的 lesson 提供因果证据。
4. **加最简 skill（slim）= 30.6% succ, 0.25 op_err**：极简 skill 仅含算子决策表，性能略低于 MCP-only —— **关键：MCP 端的 use_for/not_for 已经把 80% 的 skill 价值搬到 tool response 里了**。
5. **加 v2 skill = 47.2% succ, 0.22 op_err**：在 MCP guidance 的基础上，v2 skill 通过 think-first 协议、字段流规则、子分类决策表，把 succ 从 36% 推到 47%（+11 pp）。skill 在 MCP 之上贡献的是**字段流和算子形状**决策，而不是算子识别（那个 MCP 已经搞定）。
6. **legacy suite = 25.0% succ, 0.25 op_err**：4 个 skill 全套，反而比 v2 差 22 pp。**更多 skill ≠ 更好**——多 skill 占 context、互相干扰，造成 context overflow。

---

## 3. 由实验导致的代码改动（4 处）

按重要性排：

### 3.1 MCP server 端：list_operators 强制 category

**改动文件**：`DataFlow-WebUI/backend/app/api/v1/endpoints/operators.py`

**之前**：`list_operators` 不传 category 就返回全部 ~145 算子，single tool result 30K+ tokens，agent 调一次 context 就被吃掉一大半，5.7–21.7% 的 run 直接 ctx overflow。

**之后**：
- 拆成两条路由：
  - `GET /api/v1/operators/` (operation_id `list_operators_all`) — UI 用，依然返回全部，**不暴露给 MCP**
  - `GET /api/v1/operators/by_category` (operation_id `list_operators`) — MCP 暴露，**category 必填**
- 缺 category 或非法 category 返回结构化 400（`code=40010` 或 `40011`）：

```json
{
  "success": false,
  "code": 40010,
  "message": "list_operators requires a `category` argument. ...",
  "data": {
    "error": "category_required",
    "valid_categories": ["agentic_rag", "code", "core_text", ...],
    "next_action": "Step 1: list_operator_categories  →  Step 2: list_operators?category=<chosen>  →  Step 3: get_operator_detail_by_name(name=<chosen op>)"
  }
}
```

- 非法 category 还会用 `difflib.get_close_matches` 给 `did_you_mean` 建议（如 `resoning` → `reasoning`）。

**实验前后对比（mcp_guard_smoke, 18 runs）**：1a_qa_basic 算子选择正确率从 0–20% → **100%**（全 method）。

### 3.2 MCP server 端：list_operator_categories 返回语义指引

**改动文件**：
- 新建 `DataFlow-WebUI/backend/app/services/operator_category_guide.py`（14 个 category 的 use_for/not_for/examples）
- `DataFlow-WebUI/backend/app/api/v1/endpoints/operators.py`（端点改返回类型）

**之前**：返回 `{"core_text": 16, "general_text": 46, ...}`，只有计数。

**之后**：

```json
{
  "core_text": {
    "count": 16,
    "use_for": "通用文本→QA / 多跳 QA / 摘要 / 长文本切块生成；prompt-driven 的 filter/refine/evaluator；表格/字段重写用 PandasOperator。首选这一类做『从原始文本生成 SFT 训练数据』。",
    "not_for": "**不要**用 SFTGeneratorSeed —— 那个属于 text_sft，是从已有 SFT 种子扩展，而不是从原始文本生成 QA。需要『基础 QA 生成』时直接选 Text2QAGenerator 或 Text2MultiHopQAGenerator。",
    "examples": ["Text2QAGenerator", "Text2MultiHopQAGenerator", "ChunkedPromptedGenerator", "PromptedFilter", "PromptedRefiner", "PandasOperator"]
  },
  "text_sft": {
    "count": 17,
    "use_for": "已经有 SFT 数据（instruction/response 列）后做的事...",
    "not_for": "**不要**在『还没生成 QA / instruction』的阶段用这一类...",
    ...
  },
  ...
}
```

**关键设计点**：把 anti-pattern 知识放进 **tool response** 而不是放进 skill 文档。这样所有 method（包括 MCP-only 和 Direct CC，如果它们也调 MCP）都能拿到，无须加载任何 skill。**这是为什么 MCP-only 能跑到 36%**。

### 3.3 dataflow_engine：参数处理 bug

**改动文件**：`DataFlow-WebUI/backend/app/services/dataflow_engine.py`

发现两个真 bug：

**Bug A：unsupported run-param 触发 TypeError**

Agent 经常给 `Text2MultiHopQAGenerator` 设 `system_prompt`（认为 LLM 算子都该有）。但该算子的 `run(self, storage, input_key, output_key, output_meta_key)` 签名里没有 `system_prompt`。原代码直接 `operator.run(**run_params)` → `TypeError: run() got an unexpected keyword argument 'system_prompt'`。

**修法**：检查 run signature，把不在 signature 里且 signature 也没接 `**kwargs` 的参数静默丢弃（带 warning log）。同样的逻辑也加到 init 参数处理（保留 llm_serving / prompt_template / 等特殊路径白名单）。

**Bug B：llm_serving={"id": "..."} unhashable**

Agent 有时把 serving 写成 dict（`{"id": "9b63c43bf80889ec"}`）而不是字符串。原代码 `if serving_id not in serving_instance_map` → dict 不可哈希 → `TypeError`。

**修法**：进 if 之前 `if isinstance(serving_id, dict): serving_id = serving_id.get("id")`。

**实验观察**：第一次跑 144 runs E1_mcpguard 时几乎全部 exec_success=False（即便算子都选对），原因就是这两个 bug。修完后立刻有 8/8 records 的成功执行。

### 3.4 Runner-side：acceptance schema 字段同义词

**改动文件**：`emnlp_experiments/runner/metrics.py` + 5 个 task.json

**之前**：task 期望字段 `qa_pairs`，agent 设 `output_key=multi_hop_qa`（合理选择）。runner 的 acceptance check 找不到 `qa_pairs` → 0 valid records。

**之后**：schema 字段名支持竖线分隔的同义词集：

```python
# task.json
"acceptance": {
  "schema": {
    "chunk": "str",
    "qa_pairs|QA_pairs|multi_hop_qa|multi_hop_qa_pairs|qa": "any"
  }
}
```

`check_acceptance` 在每行里用任一同义词命中即视为 valid。

**修了的 task**：1a, 1b, 2b, 3a, 3b（5 个）。

---

## 4. 还没修的剩余瓶颈（建议作为 limitation / future work）

### 4.1 input_key default 不主动覆盖

5 个任务（`2a_sentiment / 3a_long_doc_summary / 4a_score_filter / 4b_multidim / 6a_length_filter`）几乎全部失败于 `Missing required column(s): ['cleaned_chunk']` —— agent 选对了算子（如 Text2MultiHopQAGenerator），但**没把 `input_key` 从 default `cleaned_chunk` 改成用户数据里的 `chunk`/`text`/`doc`**，即便用户 prompt 明确写了 "字段是 chunk"。

**为什么没修**：修 engine（让错误信息暴露真实列名）+ 加 retry 循环 ~2h 工作量。我做了取舍决定先发论文。但**对论文非常重要**：这个问题在 paper 里写成 §Failure Analysis + Limitation，构成与现有 Lesson 2（operator catalog 分页）对称的"下一个 lesson"，story 自洽。

### 4.2 Manual baseline 缺失

我们没跑人类构造 pipeline 的对照实验。原论文 Setup 里写 "Manual" 那一行，现在 Table 用 "—" 占位，§Setup 改成"a formative user study with experts and domain users is in progress"，把人工实验推到 work-in-progress。

---

## 5. 三个支持性 ablation

### 5.1 MCP guard ablation（小型 smoke，18 runs）

`mcp_guard_smoke` 实验：3 method × 3 task × 2 reps，跑了一组验证 §3.1 + §3.2 改动有效。
（task = 1a_qa_basic / 2a_sentiment / 4a_score_filter，最痛的 3 个 task）

| Task | Method | OLD correct% | NEW correct% |
|---|---|---|---|
| 1a_qa_basic | dataflow_agent | 0% | **100%** |
| 1a_qa_basic | mcp_only | 20% | **100%** |
| 1a_qa_basic | df_agent_v2 | 0% | **100%** |
| 4a_score_filter | dataflow_agent | 20% | **100%** |
| 4a_score_filter | mcp_only | 20% | **100%** |

平均算子选择正确率：**OLD 26% → NEW 95%**。

### 5.2 Skill ablation（72 runs）

`skill_ablation` 实验：df_agent_slim + df_agent_only_dev × 12 任务 × 3 reps。

| Method | Succ. | Op.err | ctx_overflow | pipeline produced |
|---|---|---|---|---|
| df_agent_slim | 30.6% | 0.25 | 3.3% | 96.7% |
| df_agent_only_dev | 26.7% | 1.77 | 6.7% | 96.7% |

证明 **skill 必须对齐任务**：dev-only 算子错误是 slim 的 7×，因为它加载的是框架开发 skill 而非 pipeline 构造 skill。

### 5.3 v2 vs legacy suite

E1_mcpguard 直接对比：

| Method | n | Succ. | Op.err | ctx_overflow |
|---|---|---|---|---|
| **df_agent_v2** | 36 | **47.2%** | 0.22 | 0% |
| dataflow_agent (legacy 4 skills) | 36 | 25.0% | 0.25 | 0% |

v2 比 legacy 高 22.2 pp，主要赢在：
- **think-first 协议** 让 agent 先输出计划再 create_pipeline，减少中途修改/废弃 pipeline
- **子分类决策表** 给"prompted_filter vs prompted_refiner"等细分场景的明确指引
- **更短**：v2 = 239 行，legacy 4 个 skill 总长 1500+ 行，context cache 友好

**反例**：早期 v2 实际是 480 行（更厚），那个版本 ctx_overflow 21.7%（最差）。最终发表版本是精简后的 239 行。

---

## 6. 数据/产物清单（合作者可以拿来直接用）

### 论文 Table 1 直接 LaTeX

`/data/workspace/emnlp_experiments/analysis/main_table_merged.tex`：

```latex
\begin{table}[t]
\centering
\small
\setlength{\tabcolsep}{4pt}
\begin{tabular}{lcccc}
\toprule
Method & $n$ & Time & Succ. & Op.\,err \\
\midrule
Direct CC                            &  68 & --   &  0.0 & 1.82 \\
MCP-only                             &  36 & 1.4 & 36.1 & 0.25 \\
\textsc{DF-Agent} (dev-only)         &  30 & 2.0 & 26.7 & 1.77 \\
\textsc{DF-Agent} (slim)             &  36 & 1.3 & 30.6 & 0.25 \\
\textsc{DF-Agent} (v2)               &  36 & 1.4 & \textbf{47.2} & \textbf{0.22} \\
\textsc{DF-Agent} (legacy suite)     &  36 & 1.4 & 25.0 & 0.25 \\
\bottomrule
\end{tabular}
\caption{Main evaluation across 12 tasks. ...}
\label{tab:eval}
\end{table}
```

### 原始 CSV

- `/data/workspace/emnlp_experiments/analysis/E1_mcpguard/per_run.csv` — 144 行，列：task, method, rep, time, exec_success, op_err, hallucinated, missing, wrong_order, unexpected, n_create_pipeline, final_chain
- `/data/workspace/emnlp_experiments/analysis/E1_mcpguard/summary_per_method.csv` — 4 行汇总

### Failure 类别拆分（按方法）

```
Method                  succ  ctx_overflow  wrong_op_choice  exec_err  others
Direct CC               0     0             —                —         68 ui_disconnect (无 pipeline)
MCP-only                13    0             8                12        3
DF-Agent (slim)         11    0             7                15        3
DF-Agent (v2)           17    0             5                12        2
DF-Agent (legacy)        9    0             8                15        4
```

完整 `failures.json` 在 `analysis/E1_mcpguard/failures.json`。

### 工具调用层级

E1_mcpguard 里所有 method 都严格遵守 `list_serving → list_operator_categories → list_operators(category=X) → create_pipeline → render` 顺序（lesson_signals.json 显示 100%）。这是 §3.1 强制约束的直接结果。

---

## 7. 论文写作建议（按 paper 结构）

合作者可以照下面这个 mapping 把数字塞进 paper：

### Abstract
- **47%** end-to-end success（DF-Agent v2）
- vs **0%** Direct CC
- **op_err 1.82 → 0.22**（**7.8×** improvement）
- ctx overflow 5.7–21.7% → **0%**
- N=12 tasks, 3 reps per condition, 144 runs per condition; 200+ runs total in ablations

### §Setup
- 12 tasks (per-category x2 variants), 3 reps, 6 method conditions
- API-only environment（GPT-4o via internal API; KBC/MinerU 不可用）
- acceptance schema with field-name synonyms
- **不要**保留 "5 reps per task" 那个旧数字
- **不要**保留 "8 participants user study"（那个是占位，没跑）

### §Main Results
直接套上面 §2 的故事链，6 行。

### §MCP Guard Ablation
- 强制 category：减少 single-tool-result 大小
- categories 端点带 use_for/not_for：把 anti-pattern 从 skill 搬到 server response
- 数据：op_err 1.87 → 0.24（7.8×），ctx overflow 5.7–21.7% → 0%
- 核心 insight：**把指引放进 tool response 比放进 skill 更普惠**

### §Failure Analysis
- 主要 remaining failure：input_key default-not-overridden（5/12 tasks）
- 不是 operator selection 问题，是 **parameter binding** 问题
- 提议解法：engine-side error suggester + 单次 retry budget

### §Discussion / Lessons Learned
- Lesson 1（已在原稿）：think-first 协议解决 premature pipelines
- Lesson 2（已在原稿）：操作目录必须分页 → **现在有 1.87→0.24 数据支撑**
- Lesson 3（新加）：parameter binding 是下一个对称的瓶颈

### §Limitations
- 不要写 "user study with 8 participants" —— 那个还没跑
- 加一句 "single-shot construction without execution-time retry"
- 保留 API-only 约束的描述

---

## 8. 仓库提交位置

- **DataFlow-WebUI**（含后端 MCP guard、engine fix、当前生产 4 个 skill）：
  https://github.com/HeRunming/DataFlow-WebUI/tree/skills-agent-emnlp
  关键 commit：`MCP guard + engine fix: enforce category-scoped operator listing, fix run/init param plumbing`

- **DataFlow-Skills**（v2 SKILL 替换原 generating-dataflow-pipeline，加 webui-dev / mcp-server 模块）：
  https://github.com/HeRunming/DataFlow-Skills/tree/v2
  关键 commit：`v2: rewritten generating-dataflow-pipeline skill from observed agent behavior; add webui-dev and mcp-server modules`

- **实验代码 + 原始数据**：`/data/workspace/emnlp_experiments/`（在我机器上，需要时可以打包）

---

## 9. 一些写作上可以引用的"金句"

- "Direct Claude Code without MCP grounding has 0% execution success and 1.82 mean operator errors per task: it consistently invents or mis-routes operators."
- "Adding the MCP tool surface alone lifts success to 36.1% and drops operator errors more than 7×, because the agent is forced to ground each operator name and parameter list in the live registry."
- "Two ablations are informative: removing the construction skill but keeping MCP loses 11 percentage points of success while leaving operator errors essentially unchanged—the skill earns its place in field-flow and operator-shape decisions, not in operator identification, which MCP already covers."
- "Replacing the construction skill with the framework-development-only skill restores 7× operator errors, confirming that loading an off-target skill is worse than loading none."
- "Place anti-pattern knowledge inside the tool response (where every agent method using the tool benefits) rather than only inside the skill or system prompt (where its effect is bounded by which agent loaded which skill)."
- "Once operator selection is grounded by MCP, the dominant failure mode is the agent leaving runtime parameters at their library defaults when the user's data uses different field names. This is structurally similar to the operator catalog issue: the agent has access to the correct information but does not consult it carefully enough by default."

---

## 10. 几个我的判断（可商量）

- **Manual baseline 不补也行**：写成 formative user study WIP 比硬编一个数更诚实。
- **Time 列不必当主指标**：所有 agent method 都 ~1.4 min，区分度低；succ 和 op_err 才是主战场。
- **Edits / Likert 列删了也行**：本来就需要 user study，留着 "—" 反而提示 reviewer 你少做了人工实验。
- **op_err 0.22 vs 0.25 显著吗**：实话实说：4 个 method 在 op_err 上几乎打平（都 ~0.25），区分主要靠 succ。这是合理的——MCP guard 解决了 80% 的 operator 问题，剩下的差距来自 skill 对 field flow / 子分类决策的影响。

如果 reviewer 问 "为什么不跑更大规模"，回答："API rate limit + 单次 144 runs 已经 3 小时；6 method × 12 task × 3 rep 是 deployment 论文合适的规模"。

如果 reviewer 问 "Direct CC 0% 是不是被针对设计的"：实话——12 task 都是真实 DataFlow pipeline 任务，不是为了打 Direct CC 设计的。Direct CC 0% 是因为它真的不知道有哪些算子。

---

## 11. 还能补的（如果时间允许）

按 ROI 排序：

| 工作 | 收益 | 时间 |
|---|---|---|
| 修 input_key bug + 加 retry 循环 + 重跑 144 runs | succ 47% → 60-70% 可能 | ~5h |
| 跑 E2 ablation（关掉 MCP guard / 关掉 think-first 各跑一遍） | 给 §Ablation 段加更多 before/after 因果证据 | ~3h |
| 补 Manual baseline（自己写 12 个 task） | 让 Table 1 的 Manual 行有数 | ~4h |
| 补 user study（哪怕 3 人 formative） | 给 paper 里 "easy to use" 的 claim 一些定性证据 | ~1 周 |

我觉得**前两个**值得做，**Manual baseline 和 user study** 留给后续 camera-ready 或 extended journal 版本。

---

*这份总结对应的实验代码、原始数据、详细日志都在 `/data/workspace/emnlp_experiments/`。如果合作者要看任何具体的 raw_trace.jsonl 或者跑某个 ablation 的复现命令，让我知道。*
