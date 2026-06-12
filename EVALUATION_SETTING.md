# DataFlow-Agent Evaluation Setting

> 一份独立的实验配置参考。本文件只描述**评测设计**——评测了什么、怎么算成功、有哪些指标。不涉及结果数据、不涉及代码改动。

## 1. 评测目标

测一个 code agent（Claude Code）在给定一段自然语言数据处理需求 + 一份 JSONL 样本时，**能否构造出一条可以执行、产出符合 schema 的有效记录的 DataFlow pipeline**。每个被评测的"配置"（method）的差异在于：是否暴露 MCP 工具、加载哪个 skill、是否启用 think-first / DAG sync / execution gating 等约束。

每次 run 的流程：
1. Runner 启动 agent 子进程（`claude-internal --print ... --mcp-config ... --append-system-prompt ...`）
2. 给 agent 一个 user prompt（来自 task 定义）+ 一句 boilerplate 说明输入数据集 ID 和文件路径
3. Agent 通过 MCP（如启用）查询算子目录、创建 pipeline、调用 `render_pipeline_in_editor` 同步到 WebUI
4. Agent 退出后，runner 自动调用后端 `execute_pipeline` 把这条 pipeline 跑一遍
5. Runner 下载执行的 step 输出（JSONL），按 acceptance schema 验证

LLM serving：所有 LLM 算子用同一个 `APILLMServing_request` 实例（OpenAI 兼容 API，model = `gpt-4o`，temperature=0.0），在所有 method 里共享同一个 serving id，避免引入 LLM 差异这个干扰变量。

## 2. 12 个评测任务

任务覆盖 6 大类（QA 生成 / 评论治理 / 长文档处理 / 多字段评分 / schema 规范化 / 低质过滤），每类 2 个变体（一般是 a=easy, b=harder）。

每个任务定义在 `tasks/<task_id>/`：
- `task.json` — 任务规约（user_prompt / expected_chain / expected_fields_produced / acceptance / difficulty / category / notes）
- `sample.jsonl` — 5–20 行小样本，作为输入数据

下面按 task 列出全部细节。

---

### 1a — `qa_basic`  (easy / qa_generation)

**用户 prompt（中文，原样喂给 agent）：**
> 我有一份 JSONL 数据，每行有一个 'chunk' 字段，包含一段百科或事实性文本。请帮我构建一个 pipeline，为每个 chunk 生成多跳（multi-hop）问答对。不需要后续过滤，只生成 QA 即可。

**输入样本（8 行）**：每行 `{"chunk": "<英文百科段落>"}`，例：
```json
{"chunk": "The Eiffel Tower was completed in 1889 for the World's Fair. ..."}
```

**期望算子链（1 步）：**
| slot | required op | semantic alternatives (slot_ops) |
|---|---|---|
| `multi_hop_qa` | `Text2MultiHopQAGenerator` | `Text2MultiHopQAGenerator`, `Text2QAGenerator` |

**期望产出字段：** `chunk`, `qa_pairs`

**Acceptance：**
- schema：每行必须有 `chunk` (str) + 一个落入同义词集 `qa_pairs|QA_pairs|multi_hop_qa|multi_hop_qa_pairs|qa|questions` 的字段（任意类型）
- `min_valid_records: 3`

**考点：** agent 必须选 `Text2MultiHopQAGenerator` 而不是泛用的 `PromptedGenerator`；记住 `qa_pairs` 是 nested list of dicts。

---

### 1b — `qa_with_filter`  (medium / qa_generation)

**用户 prompt：**
> 我有一份 JSONL 数据，每行有一个 'chunk' 字段，包含一段百科或事实性文本。请帮我构建一个 pipeline：先为每个 chunk 生成多跳问答对，然后对 QA 质量进行打分，最后过滤掉低质量（分数 < 3）的记录。

**输入样本：** 与 1a 相同（8 行）。

**期望算子链（3 步）：**
| slot | required op | slot_ops |
|---|---|---|
| `multi_hop_qa` | `Text2MultiHopQAGenerator` | `Text2MultiHopQAGenerator`, `Text2QAGenerator` |
| `qa_quality_score` | `Text2QASampleEvaluator` | `Text2QASampleEvaluator`, `PromptedEvaluator`, `FormatStrPromptedGenerator`, `PromptedGenerator` |
| `filter` | `GeneralFilter` | `GeneralFilter`, `PromptedFilter` |

**期望产出字段：** `chunk`, `qa_pairs`, `quality_score`

**Acceptance：**
- schema：`chunk` (str) + `qa_pairs|QA_pairs|multi_hop_qa|qa` (any)
- `min_valid_records: 1`

**考点：** agent 是否意识到 `qa_pairs` 是 nested list，不能直接对 QA 内的 question/answer 字段打分（必须先评估整组 qa_pairs 或 explode）。

---

### 2a — `single_language_sentiment`  (easy / review_governance)

**用户 prompt：**
> 我有一份英文电商评论 JSONL，每行有 product_name 和 review_text。请帮我构建一个 pipeline，为每条评论打一个情感标签（positive / neutral / negative）。只需要分类，不需要过滤。

**输入样本（15 行）**：
```json
{"product_name": "Wireless Bluetooth Earbuds", "review_text": "Amazing sound quality! ..."}
```

**期望算子链（1 步）：**
| slot | required op | slot_ops |
|---|---|---|
| `sentiment` | `PromptedGenerator` | `PromptedGenerator`, `FormatStrPromptedGenerator`, `PromptedEvaluator` |

**期望产出字段：** `product_name`, `review_text`, `sentiment`

**Acceptance：**
- schema：`product_name` (str), `review_text` (str)（注意：sentiment 字段不在 schema 里，因为 agent 选哪个 output_key 都可以）
- `min_valid_records: 5`

**考点：** 最简单的单 LLM-op 任务。

---

### 2b — `review_governance`  (medium / review_governance) — paper 主 case study

**用户 prompt：**
> 我有一份多语言（中英混合）的电商评论 JSONL，每行包含 product_name、category、review_text。请帮我构建一个评论治理 pipeline：(1) 检测每条评论的语言；(2) 对评论做规范化清洗；(3) 基于规范化后的文本给出 1-5 的整体质量分数并附一段简短解释；(4) 基于质量分数过滤掉低质量（分数 < 3）的评论。输入文件路径由已注册的数据集提供。

**输入样本（20 行）**：每行 `{product_name, category, review_text}`，混合中英。

**期望算子链（4 步）：**
| slot | required | required op | slot_ops |
|---|---|---|---|
| `language_detect` | optional | `LLMLanguageFilter` | `LLMLanguageFilter`, `LanguageFilter`, `PromptedGenerator`, `FormatStrPromptedGenerator` |
| `refine` | required | `PromptedRefiner` | `PromptedRefiner`, `PromptedGenerator`, `FormatStrPromptedGenerator`, 多个确定性 refiner（`RemoveEmojiRefiner`, `RemoveExtraSpacesRefiner`, `HtmlUrlRemoverRefiner`, `LowercaseRefiner`, `TextNormalizationRefiner`, ...） |
| `score` | required | `FormatStrPromptedGenerator` | `FormatStrPromptedGenerator`, `PromptedGenerator`, `PromptedEvaluator` |
| `filter` | required | `GeneralFilter` | `GeneralFilter`, `PromptedFilter` |

**期望产出字段：** `product_name`, `category`, `review_text`, `language`, `normalized_text`, `quality_score`, `score_explanation`

**Acceptance：**
- schema：`product_name` (str), `review_text` (str), `quality_score|score|quality|label` (any)
- `min_valid_records: 3`

**考点：** 最完整的多阶段 pipeline；要求 detect → refine → score → filter 四步；`score` 槽位放宽匹配（允许 FormatStrPromptedGenerator / PromptedGenerator / PromptedEvaluator）。

---

### 3a — `long_doc_summary`  (medium / long_document_processing)

**用户 prompt：**
> 我有一份长文档 JSONL，每行有 'doc' 字段包含一段约 1000-2000 字的长英文段落。请帮我构建一个 pipeline：对每个 doc 生成一段简洁摘要（summary 字段，3-4 句话）。注意文档可能超出 LLM 上下文，需要选择合适的算子分段处理。

**输入样本（5 行）**：每行 `{"doc": "<约 1000-2000 字英文段落>"}`。

**期望算子链（1 步）：**
| slot | required op | slot_ops |
|---|---|---|
| `chunked_summary` | `ChunkedPromptedGenerator` | `ChunkedPromptedGenerator`, `PromptedGenerator` |

**期望产出字段：** `doc`, `summary`

**Acceptance：**
- schema：`doc` (str), `summary|summaries|summarized_text|doc_summary` (str)
- `min_valid_records: 3`

**考点：** 长文本场景能否选 `ChunkedPromptedGenerator`，而不是一刀切用 `PromptedGenerator`（可能 overflow）。

---

### 3b — `text_to_qa_chain`  (hard / multistage_pipeline)

**用户 prompt：**
> 我有一份 JSONL，每行有 'text' 字段，是百科类短文本但包含 HTML 标签、URL、emoji、多余空格等杂质。请帮我构建一个 pipeline：先把文本做规范化清洗（去 HTML、去 URL、去 emoji、合并多余空格），然后对清洗后的文本生成多跳问答对。

**输入样本（8 行）**：每行 `{"text": "<p>...&#39;...👉..."}`，含 HTML 实体、emoji、URL。

**期望算子链（4 步）：**
| slot | required | required op | slot_ops |
|---|---|---|---|
| `clean_html_url` | required | `HtmlUrlRemoverRefiner` | `HtmlUrlRemoverRefiner`, `HtmlEntityRefiner`, `PromptedRefiner` |
| `clean_emoji` | optional | `RemoveEmojiRefiner` | `RemoveEmojiRefiner`, `RemoveEmoticonsRefiner`, `PromptedRefiner` |
| `clean_whitespace` | optional | `RemoveExtraSpacesRefiner` | `RemoveExtraSpacesRefiner`, `PromptedRefiner` |
| `multi_hop_qa` | required | `Text2MultiHopQAGenerator` | `Text2MultiHopQAGenerator`, `Text2QAGenerator` |

**期望产出字段：** `text`, `qa_pairs`

**Acceptance：**
- schema：`text` (str), `qa_pairs|QA_pairs|multi_hop_qa|qa` (any)
- `min_valid_records: 2`

**考点：** API-only 两阶段链：确定性 refine → LLM QA 生成；偏好确定性 refiner + 正确路由的 QA 算子（core_text 而非 text_sft）。

---

### 4a — `score_and_filter`  (medium / multifield_scoring)

**用户 prompt：**
> 我有一份 QA 数据集 JSONL，每行有 question 和 answer 两个字段。请帮我构建一个 pipeline：基于 question 和 answer 同时对每条 QA 的答案质量打 1-5 分，然后过滤掉分数低于 3 的记录。

**输入样本（10 行）**：`{"question": "...", "answer": "..."}`。

**期望算子链（2 步）：**
| slot | required op | slot_ops |
|---|---|---|
| `multi_field_score` | `FormatStrPromptedGenerator` | `FormatStrPromptedGenerator`, `PromptedGenerator`, `PromptedEvaluator` |
| `filter` | `GeneralFilter` | `GeneralFilter`, `PromptedFilter` |

**期望产出字段：** `question`, `answer`, `quality_score`

**Acceptance：**
- schema：`question` (str), `answer` (str)
- `min_valid_records: 2`

**考点：** 多字段评分必须用 `FormatStrPromptedGenerator`（一次喂多字段），而不是把它拆成多个 `PromptedGenerator`；filter 用 `GeneralFilter`（在已计算的数值字段上做确定性过滤）。

---

### 4b — `multidim_scoring`  (hard / multifield_scoring)

**用户 prompt：**
> 我有一份 QA 数据集（question + answer）。请帮我构建一个 pipeline：对每条 QA 在 correctness（正确性）、completeness（完整性）、fluency（流畅度）三个维度分别打 1-5 分，然后只保留三个维度平均分大于等于 3.5 的记录。

**输入样本（10 行）**：与 4a 相同。

**期望算子链（2 步）：**
| slot | required op | slot_ops |
|---|---|---|
| `multidim_score` | `FormatStrPromptedGenerator` | `FormatStrPromptedGenerator`, `PromptedGenerator`, `PromptedEvaluator` |
| `filter` | `GeneralFilter` | `GeneralFilter`, `PromptedFilter` |

**期望产出字段：** `question`, `answer`, `correctness`, `completeness`, `fluency`

**Acceptance：**
- schema：`question` (str), `answer` (str)
- `min_valid_records: 2`

**考点：** 多维度评分（一次 prompt 出 JSON 结构化结果）；过滤基于计算出的均值（`GeneralFilter` + 自定义 lambda）。

---

### 5a — `field_rename`  (easy / schema_normalization)

**用户 prompt：**
> 我有一份用户 JSONL，字段是 first_name、last_name、email_addr、phone_num。请帮我构建一个 pipeline 把字段重命名为 firstName、lastName、email、phone，同时新增一个 fullName 字段拼接 first_name 和 last_name。这个任务只需要确定性变换，不需要 LLM。

**输入样本（8 行）**：`{"first_name": ..., "last_name": ..., "email_addr": ..., "phone_num": ...}`。

**期望算子链（1 步）：**
| slot | required op | slot_ops |
|---|---|---|
| `deterministic_transform` | `PandasOperator` | `PandasOperator`（无替代） |

**期望产出字段：** `firstName`, `lastName`, `email`, `phone`, `fullName`

**Acceptance：**
- schema：`{}`（空）—— 这一类任务不通过字段验证，只靠 `min_valid_records` 是否达到（即至少有 N 行能解析）
- `min_valid_records: 3`

**考点：** **不要**用 LLM 做确定性变换。任何非 `PandasOperator` 的算子在语义匹配里会落入 `unexpected`。

---

### 5b — `nested_flatten`  (medium / schema_normalization)

**用户 prompt：**
> 我有一份嵌套的文档 JSONL，每行有 doc_id、metadata（title/tags/author）、content。请帮我构建一个 pipeline 把嵌套字段展平：metadata.title -> title，metadata.author.name -> author_name，metadata.author.email -> author_email，metadata.tags 保留为 tags 字段。不需要 LLM。

**输入样本（5 行）**：含两层嵌套字段。

**期望算子链（1 步）：**
| slot | required op | slot_ops |
|---|---|---|
| `deterministic_transform` | `PandasOperator` | `PandasOperator` |

**期望产出字段：** `doc_id`, `title`, `author_name`, `author_email`, `tags`, `content`

**Acceptance：**
- schema：`{}`
- `min_valid_records: 3`

**考点：** 与 5a 同——禁止 LLM；嵌套展平比 5a 更难。

---

### 6a — `length_filter`  (easy / low_quality_filter)

**用户 prompt：**
> 我有一份文本 JSONL（text 字段）。请帮我构建一个 pipeline，过滤掉单词数少于 10 的短文本。不需要 LLM。

**输入样本（10 行）**：text 字段长短不一。

**期望算子链（1 步）：**
| slot | required op | slot_ops |
|---|---|---|
| `length_filter` | `WordNumberFilter` | `WordNumberFilter`, `GeneralFilter`, `PandasOperator` |

**期望产出字段：** `text`

**Acceptance：**
- schema：`text` (str)
- `min_valid_records: 3`

**考点：** 用专用的 `WordNumberFilter`，不要用 `PromptedFilter`（确定性规则不需要 LLM）。

---

### 6b — `llm_semantic_filter`  (medium / low_quality_filter)

**用户 prompt：**
> 我有一份文本 JSONL（text 字段），里面混杂了有意义的句子和无意义的乱码/填充文本。请帮我构建一个 pipeline，用 LLM 判断每条文本是否'有信息量'，过滤掉没信息量的记录。

**输入样本（10 行）**：含 5 条有意义文本 + 5 条乱码/填充文本。

**期望算子链（1 步）：**
| slot | required op | slot_ops |
|---|---|---|
| `llm_filter` | `PromptedFilter` | `PromptedFilter`, `PromptedGenerator`, `FormatStrPromptedGenerator` |

**期望产出字段：** `text`

**Acceptance：**
- schema：`text` (str)
- `min_valid_records: 2`

**考点：** 语义过滤必须 LLM；选 `PromptedFilter`（不是 `GeneralFilter`，那是给确定性规则用的）。

---

## 3. 评测指标

每个 run（一次 task × method × repetition）跑完后，runner 算出一个 metrics dict 写入 `run_trace.json`。下面是论文 + 内部分析用到的指标。

### 3.1 时间指标

| 指标 | 定义 |
|---|---|
| `time_to_pipeline_min` | 第一条 user message → agent 调用 `create_pipeline` 成功（**不依赖**执行成功）。中位数报告。 |
| `time_min` | 第一条 user message → 第一次 execution 成功（i.e. acceptance 通过）。论文 headline 时间指标。 |

注意：`direct_cc` 配置下 agent 没有 MCP 也没有后端，**永远不产生 pipeline**，因此 `time_to_pipeline_min` 和 `time_min` 都为 `None`，表里以 `--` 表示。

### 3.2 算子选择正确性

我们对每条期望算子链定义两套匹配规则：

#### Strict match（严格匹配）
agent 产出的算子名 **完全等于** 期望 op，或在 `alt`（同义算子，名字层面）里。

#### Semantic match（语义槽位匹配）
agent 产出的算子名落入该槽位的 `slot_ops` 集合（包含 strict 的所有，外加按"语义角色"等价的算子）。

例：1b 的 `score` 槽位严格期望 `Text2QASampleEvaluator`，但语义上接受 `PromptedEvaluator` / `FormatStrPromptedGenerator` / `PromptedGenerator`——只要它实现了"对 QA 打分"这个角色就算对。

**论文 headline 用的是 semantic match**，因为我们关心 agent 是否做对了**任务**，不强求它精确选最专业的算子。

#### 算子错误数（headline 指标 `operator_errors`）

```
operator_errors = hallucinated + missing_required + wrong_order + unexpected
```

各项拆开：
- `hallucinated`：agent 生成了**不在 OPERATOR_REGISTRY 里的**算子名（凭空发明）
- `missing_required`：期望链里 `required=True` 的槽位没有任何 semantic-matching 算子出现
- `wrong_order`：所有 required 槽位都出现了，但顺序错（前后颠倒）
- `unexpected`：出现了既不在 expected 也不在 alt/slot_ops 里的多余算子

**对应论文 Table 1 的 `Op. err` 列**。例：mean op_err = 0.22 表示平均每个 run 出 0.22 次算子选择错误。

`hallucinated` 单独也作为一个 lesson signal 报告（用来量化"不接 MCP grounding 时算子凭空捏造"的程度）。

### 3.3 字段流错误（`field_errors`）

静态检查：按 pipeline 里的算子顺序遍历，每个算子的 `input_key` / `input_keys` 必须满足：要么是原始样本就有的字段，要么是某个**前序算子**的 `output_key` 产出。

如果某个算子引用了"还没存在"的字段 → 计入 `field_errors`。

例：`GeneralFilter(input_key="quality_score")` 出现在 `FormatStrPromptedGenerator(output_key="quality_score")` **之前** → +1 field_error。

### 3.4 端到端执行成功（`exec_success`）— 论文 headline `Succ.`

定义（按以下顺序判定）：
1. agent 必须产出 pipeline（`final_pipeline` 非空）。无 → `exec_success = False`
2. runner 调 `execute_pipeline` 跑这条 pipeline。返回 status 必须是 `completed` / `success` / `succeeded`。否则 `False`
3. 下载最后一步的 step 输出 JSONL，对**每行**按 `acceptance.schema` 验证：
   - 每个 schema 键检查行里是否存在该字段（或同义词集里任意一个）
   - 检查字段类型匹配（`str` / `int` / `float` / `bool` / `list` / `dict` / `any`）
   - 全部通过 → 该行 valid
4. 行内 valid 数 ≥ `acceptance.min_valid_records` → `exec_success = True`，否则 `False`

字段同义词机制（schema key 用 `|` 分隔）允许 agent 自由选 `output_key`：例如 1a 的 acceptance schema 是 `qa_pairs|QA_pairs|multi_hop_qa|multi_hop_qa_pairs|qa|questions`，agent 选哪个都接受。

`exec_success_rate`（method 维度的均值）= 论文 Table 1 的 **`Succ.`** 列。

### 3.5 Lesson-specific signals

为支持论文里几个 specific lesson 的 narrative，runner 还记下：

| 信号 | 含义 | 论文用法 |
|---|---|---|
| `n_create_pipeline_calls` | agent 在一次 run 里调了几次 `create_pipeline` | "premature pipelines" lesson：>1 表示 agent 边想边写、推送了多个不兼容草稿 |
| `n_render_pipeline_calls` | 调了几次 `render_pipeline_in_editor` | DAG-sync 是否被遵守 |
| `n_execute_pipeline_calls_by_agent` | agent **自己**调 `execute_pipeline` 的次数（不是 runner 调的） | execution-gating 是否被违反 |
| `context_overflow_rate` | 该 run 是否因 context 超限失败（无 `final_pipeline` 且无显式 error） | "operator catalogs must be paginated" lesson：MCP-guard 改动前 5.7–21.7%，改动后 0% |
| `pipeline_produced_rate` | 该 run 最终是否产出了 pipeline（不要求 exec 成功） | 跟 ctx_overflow 互补 |

### 3.6 Failure 类别（用于 `failures.json`）

每个 run 的 primary failure label，按以下优先级分类：
1. `success`：`exec_success = True`
2. `partial_success`：执行完成但 `n_valid > 0` 且 `< min_valid_records`
3. `context_overflow`：无 final_pipeline 且 trace 显示 context 超限
4. `wrong_operator_choice`：`missing_required > 0` 或 `unexpected > 0`
5. `operator_hallucination`：`hallucinated > 0`
6. `execution_error`：有 final_pipeline 但 `exec_status` 非成功
7. `ui_disconnect`：有 pipeline 但 `n_render_pipeline_calls = 0`
8. `unclassified`：其余

## 4. 重复与样本规模

**主实验 E1_mcpguard_v3（最新一轮）：** 12 任务 × 3 reps × 4 method（`dataflow_agent` / `mcp_only` / `df_agent_v2` / `df_agent_slim`）= **144 runs**。这一轮在 `E1_mcpguard` 的 MCP guard 之上又增加了 4 个新工具（见下文 §6）+ 创建/更新 pipeline 时的强制 validation gate。

**前序实验：**
- `E1_mcpguard`（v2 一轮）144 runs，含原始的 MCP guard（`list_operators` 强制 category + `list_operator_categories` 带 use_for/not_for）
- 老 `E1`（无 MCP guard）：`direct_cc` 68 runs + `dataflow_agent`/`mcp_only`/`df_agent_v2 (early)` 各 60 runs
- `skill_ablation`：`df_agent_only_dev` 30 runs（故意加错 skill 的反例）

每条 run 的元数据写入 `results/<experiment_id>/<task>__<method>__rep<i>/run_trace.json`，原始 raw_trace（JSONL，一行一个 stream-json 事件）写入同目录的 `raw_trace.jsonl`。

## 5. 重要的"约束变量"

为避免引入混淆变量，所有 method 共享：
- 同一个 LLM serving（gpt-4o, temperature=0, 同一个 API 端点）
- 同一份 12 task 定义（包括 user prompt、sample.jsonl、acceptance schema）
- 同一次 backend 启动（同一份 OPERATOR_REGISTRY、同一份 dataset registry、同一台机器）
- 同样的 timeout（600 秒/run）
- 同一个 Claude Code CLI 二进制版本（`claude-internal 1.1.3`）

method 间的差异**只在三个轴上**：
- `agent_cwd`（决定可见的 `.claude/skills/` 内容，以及是否能读 backend 文件）
- `mcp_config`（是 `.mcp.json` 还是 `None`）
- `enable_think_first` / `enable_dag_sync` / `enable_exec_gating`（这三个标志决定 system prompt 里是否注入对应约束句）

## 6. MCP 工具集合（按 v3 更新到位）

每条 method（除了 `direct_cc`）都通过 FastApiMCP 的 `/mcp` 暴露下列工具。从 E1_mcpguard 到 E1_mcpguard_v3 的演进列在最右侧。

| MCP 工具 | 作用 | E1（老） | E1_mcpguard（v2） | E1_mcpguard_v3（最新） |
|---|---|---|---|---|
| `list_operator_categories` | 列出顶级算子分类（含 count + use_for + not_for + examples） | 仅 count | 加 use_for/not_for/examples | 不变 |
| `list_operators` | 列某个 category 下的算子 | 不限 category，可拉全表 | **强制 category**，缺/非法 → 40010/40011 | 不变 |
| `recommend_operator_categories` | **新增**：吃任务描述+列名，返回 ≤2 个推荐 category（含 reason）+ avoid 列表 | 不存在 | 不存在 | 新加 |
| `get_operator_detail_by_name` | 算子详细参数签名 | 仅原始 schema | 仅原始 schema | 加 `agent_tips` / `field_binding_hint` / `mcp_context_hint` 提示 |
| `list_serving` / `list_servings` | 当前注册的 LLM serving | `list_serving` | 不变 | 加 `list_servings` 复数别名 |
| `register_dataset` | 注册数据集，返回真实 id | 同 | 同 | 同 |
| `list_datasets` | 已注册数据集 | 同 | 同 | 同 |
| `get_dataset_columns` | **新增**：返回某 dataset_id 的真实列名 | 不存在 | 不存在 | 新加 |
| `validate_pipeline_config` | **新增**：静态校验 pipeline，含 dataset/字段流/serving/prompt_template/process_fn 的所有错误，并对字段名错配返回 `suggested_fields`（基于 difflib） | 不存在 | 不存在 | 新加 |
| `create_pipeline` / `update_pipeline` | 创建/更新 pipeline | 直接落库 | 直接落库 | **强制先做 validation gate**：如果 `validate_pipeline_config` 不通过，server 端直接 raise 400，pipeline 不会落库 |
| `get_pipeline` / `list_pipelines` | 读取已存 pipeline | 同 | 同 | 同 |
| `execute_pipeline` / `execute_pipeline_async` | 执行 pipeline；agent 不应主动调（exec_gating） | 同 | 同 | 同 |
| `get_execution_status` / `get_task_result` | 执行状态 / 结果 | 同 | 同 | 同 |
| `render_pipeline_in_editor` | 把 stored pipeline 同步到 WebUI Vue Flow（WebSocket 推送） | 同 | 同 | 同 |

### 新增三件事说明（v3）

1. **`validate_pipeline_config` 是这一轮最关键的改动**：把"运行时报错"前移到"创建时报错"。比如 agent 把 `input_key='cleaned_chunk'` 设给一个真实列是 `chunk` 的数据集，validation 会立即拒绝并返回：

   ```json
   {
     "valid": false,
     "errors": [{
       "code": "missing_input_field",
       "field_name": "cleaned_chunk",
       "available_fields": ["chunk"],
       "suggested_fields": ["chunk"],
       "repair_hint": "将参数 'input_key' 从 'cleaned_chunk' 改成最接近的真实字段之一：chunk..."
     }]
   }
   ```

   并且 `create_pipeline` / `update_pipeline` 一侧也接同样的校验，所以**就算 agent 不主动调 validate，bad config 也会被拒绝**。

2. **`recommend_operator_categories`** 用关键词匹配（中英 ~30 个 token）+ 列名启发式打分，返回 max 2 个推荐 category。目的：让 agent 不要广撒网调多次 `list_operators`。

3. **`get_dataset_columns`** + 系统提示中的 §2.2 "在决定 input_key/input_keys 之前，优先调 get_dataset_columns" 把"字段名靠猜"这条路堵死。

### 新增的系统提示规则（agent_session.py）

- §2.2「提交前必须做静态校验」要求 `create_pipeline` 之前先调 `validate_pipeline_config`，并在 `update_pipeline` 任何字段流相关改动后再调一次。
- §2 query order 现在多一步：在 `list_operator_categories` 之后、`list_operators` 之前可以调 `recommend_operator_categories` 缩小搜索域。

### `dataflow-pipeline-v2` SKILL（df_agent_v2 所用）

- 新加 R0：在 R1 之前必须先 `get_dataset_columns(ds_id)`。
- 新加规则：如果 `validate_pipeline_config` 返回 `missing_input_field`，**优先采用 `suggested_fields`/`repair_hint` 修字段绑定，而不是再去广撒网换 category**。
- 9 步协议固定：list_servings → list_operator_categories → recommend_operator_categories（可选）→ list_operators → get_operator_detail_by_name → emit plan → validate_pipeline_config → create_pipeline → render_pipeline_in_editor。
