# DataFlow 实验任务 Prompt 汇总

本文档汇总了 EMNLP 实验中使用的全部 12 个 pipeline 构建任务的 prompt 及元信息。

任务覆盖 6 大类别（每类一个 easy/medium 与一个 medium/hard 变体），用于评估
agent（df_agent / mcp_only 等方法）在不同难度下构建正确 DataFlow pipeline 的能力。

## 评估指标说明

每个任务用三层指标评估：

- **structure_pass** — pipeline 算子链是否正确（选对算子、顺序正确、无幻觉）
- **exec_pass** — pipeline 能否无错误执行
- **e2e_pass** — 输出是否满足验收标准（schema + 最少有效记录数）

---

## 任务清单

| ID | 类别 | 难度 | 期望算子链 |
|---|---|---|---|
| 1a | qa_generation | easy | `Text2MultiHopQAGenerator` |
| 1b | qa_generation | medium | `Text2MultiHopQAGenerator → Text2QASampleEvaluator → GeneralFilter` |
| 2a | review_governance | easy | `PromptedGenerator` |
| 2b | review_governance | medium | `LLMLanguageFilter → PromptedRefiner → FormatStrPromptedGenerator → GeneralFilter` |
| 3a | long_document_processing | medium | `ChunkedPromptedGenerator` |
| 3b | multistage_pipeline | hard | `HtmlUrlRemoverRefiner → RemoveEmojiRefiner → RemoveExtraSpacesRefiner → Text2MultiHopQAGenerator` |
| 4a | multifield_scoring | medium | `FormatStrPromptedGenerator → GeneralFilter` |
| 4b | multifield_scoring | hard | `FormatStrPromptedGenerator → GeneralFilter` |
| 5a | schema_normalization | easy | `PandasOperator` |
| 5b | schema_normalization | medium | `PandasOperator` |
| 6a | low_quality_filter | easy | `WordNumberFilter` |
| 6b | low_quality_filter | medium | `PromptedFilter` |

---

## 1. QA 生成（qa_generation）

### 1a · qa_basic（easy）

**用户 Prompt：**
> 我有一份 JSONL 数据，每行有一个 'chunk' 字段，包含一段百科或事实性文本。请帮我构建一个 pipeline，为每个 chunk 生成多跳（multi-hop）问答对。不需要后续过滤，只生成 QA 即可。

- **输入字段**：`chunk`（英文百科/事实性文本）
- **期望算子链**：`Text2MultiHopQAGenerator`
- **产出字段**：`chunk`, `qa_pairs`
- **验收**：min_valid_records=3；schema 含 `chunk` + QA 字段
- **考点**：必须选 `Text2MultiHopQAGenerator`（而非 `PromptedGenerator`）；`qa_pairs` 是嵌套 list of dicts。

### 1b · qa_with_filter（medium）

**用户 Prompt：**
> 我有一份 JSONL 数据，每行有一个 'chunk' 字段，包含一段百科或事实性文本。请帮我构建一个 pipeline：先为每个 chunk 生成多跳问答对，然后对 QA 质量进行打分，最后过滤掉低质量（分数 < 3）的记录。

- **输入字段**：`chunk`
- **期望算子链**：`Text2MultiHopQAGenerator → Text2QASampleEvaluator → GeneralFilter`
- **产出字段**：`chunk`, `qa_pairs`, `quality_score`
- **验收**：min_valid_records=1
- **考点**：意识到 `qa_pairs` 是嵌套 list，不能直接对其 question/answer 打分/过滤，必须先评估整组或 explode。

---

## 2. 评论治理（review_governance）

### 2a · single_language_sentiment（easy）

**用户 Prompt：**
> 我有一份英文电商评论 JSONL，每行有 product_name 和 review_text。请帮我构建一个 pipeline，为每条评论打一个情感标签（positive / neutral / negative）。只需要分类，不需要过滤。

- **输入字段**：`product_name`, `review_text`（英文）
- **期望算子链**：`PromptedGenerator`
- **产出字段**：`product_name`, `review_text`, `sentiment`
- **验收**：min_valid_records=5
- **考点**：最简单的 LLM-op 任务，单个 `PromptedGenerator` 即可。

### 2b · review_governance（medium）— 主 case study

**用户 Prompt：**
> 我有一份多语言（中英混合）的电商评论 JSONL，每行包含 product_name、category、review_text。请帮我构建一个评论治理 pipeline：(1) 检测每条评论的语言；(2) 对评论做规范化清洗；(3) 基于规范化后的文本给出 1-5 的整体质量分数并附一段简短解释；(4) 基于质量分数过滤掉低质量（分数 < 3）的评论。输入文件路径由已注册的数据集提供。

- **输入字段**：`product_name`, `category`, `review_text`（中英混合）
- **期望算子链**：`LLMLanguageFilter → PromptedRefiner → FormatStrPromptedGenerator → GeneralFilter`
- **产出字段**：`product_name`, `category`, `review_text`, `language`, `normalized_text`, `quality_score`, `score_explanation`
- **验收**：min_valid_records=3
- **考点**：四步链 detect → refine → score → filter。score 槽位放宽匹配（允许 `FormatStrPromptedGenerator` / `PromptedGenerator` / `PromptedEvaluator`）。

---

## 3. 长文档 / 多阶段处理

### 3a · long_doc_summary（medium）

**用户 Prompt：**
> 我有一份长文档 JSONL，每行有 'doc' 字段包含一段约 1000-2000 字的长英文段落。请帮我构建一个 pipeline：对每个 doc 生成一段简洁摘要（summary 字段，3-4 句话）。注意文档可能超出 LLM 上下文，需要选择合适的算子分段处理。

- **输入字段**：`doc`（1000-2000 字长英文）
- **期望算子链**：`ChunkedPromptedGenerator`
- **产出字段**：`doc`, `summary`
- **验收**：min_valid_records=3
- **考点**：长文档应选 `ChunkedPromptedGenerator`（分段处理），而非朴素用 `PromptedGenerator`（可能上下文溢出失败）。

### 3b · text_to_qa_chain（hard）

**用户 Prompt：**
> 我有一份 JSONL，每行有 'text' 字段，是百科类短文本但包含 HTML 标签、URL、emoji、多余空格等杂质。请帮我构建一个 pipeline：先把文本做规范化清洗（去 HTML、去 URL、去 emoji、合并多余空格），然后对清洗后的文本生成多跳问答对。

- **输入字段**：`text`（含 HTML/URL/emoji/多余空格）
- **期望算子链**：`HtmlUrlRemoverRefiner → RemoveEmojiRefiner → RemoveExtraSpacesRefiner → Text2MultiHopQAGenerator`
- **产出字段**：`text`, `qa_pairs`
- **验收**：min_valid_records=2
- **考点**：确定性 refiner（非 LLM）+ 正确的 QA 算子（core_text 而非 text_sft）。

---

## 4. 多字段打分（multifield_scoring）

### 4a · score_and_filter（medium）

**用户 Prompt：**
> 我有一份 QA 数据集 JSONL，每行有 question 和 answer 两个字段。请帮我构建一个 pipeline：基于 question 和 answer 同时对每条 QA 的答案质量打 1-5 分，然后过滤掉分数低于 3 的记录。

- **输入字段**：`question`, `answer`
- **期望算子链**：`FormatStrPromptedGenerator → GeneralFilter`
- **产出字段**：`question`, `answer`, `quality_score`
- **验收**：min_valid_records=2
- **考点**：多字段打分用 `FormatStrPromptedGenerator`（而非多个 `PromptedGenerator`）；filter 用 `GeneralFilter` 作用于计算出的数值字段。

### 4b · multidim_scoring（hard）

**用户 Prompt：**
> 我有一份 QA 数据集（question + answer）。请帮我构建一个 pipeline：对每条 QA 在 correctness（正确性）、completeness（完整性）、fluency（流畅度）三个维度分别打 1-5 分，然后只保留三个维度平均分大于等于 3.5 的记录。

- **输入字段**：`question`, `answer`
- **期望算子链**：`FormatStrPromptedGenerator → GeneralFilter`
- **产出字段**：`question`, `answer`, `correctness`, `completeness`, `fluency`
- **验收**：min_valid_records=2
- **考点**：单 prompt 多维度打分（JSON schema 输出）；`GeneralFilter` 作用于计算出的均值。

---

## 5. Schema 规范化（schema_normalization）

### 5a · field_rename（easy）

**用户 Prompt：**
> 我有一份用户 JSONL，字段是 first_name、last_name、email_addr、phone_num。请帮我构建一个 pipeline 把字段重命名为 firstName、lastName、email、phone，同时新增一个 fullName 字段拼接 first_name 和 last_name。这个任务只需要确定性变换，不需要 LLM。

- **输入字段**：`first_name`, `last_name`, `email_addr`, `phone_num`
- **期望算子链**：`PandasOperator`
- **产出字段**：`firstName`, `lastName`, `email`, `phone`, `fullName`
- **验收**：min_valid_records=3
- **考点**：确定性变换绝不用 LLM 算子，单个 `PandasOperator` 即可。

### 5b · nested_flatten（medium）

**用户 Prompt：**
> 我有一份嵌套的文档 JSONL，每行有 doc_id、metadata（title/tags/author）、content。请帮我构建一个 pipeline 把嵌套字段展平：metadata.title -> title，metadata.author.name -> author_name，metadata.author.email -> author_email，metadata.tags 保留为 tags 字段。不需要 LLM。

- **输入字段**：`doc_id`, `metadata`（嵌套 title/tags/author）, `content`
- **期望算子链**：`PandasOperator`
- **产出字段**：`doc_id`, `title`, `author_name`, `author_email`, `tags`, `content`
- **验收**：min_valid_records=3
- **考点**：同 5a 的"无需 LLM"原则，但带嵌套展平。

---

## 6. 低质量过滤（low_quality_filter）

### 6a · length_filter（easy）

**用户 Prompt：**
> 我有一份文本 JSONL（text 字段）。请帮我构建一个 pipeline，过滤掉单词数少于 10 的短文本。不需要 LLM。

- **输入字段**：`text`
- **期望算子链**：`WordNumberFilter`
- **产出字段**：`text`
- **验收**：min_valid_records=3
- **考点**：长度过滤用专用的 `WordNumberFilter`（而非 `PromptedFilter`），确定性过滤。

### 6b · llm_semantic_filter（medium）

**用户 Prompt：**
> 我有一份文本 JSONL（text 字段），里面混杂了有意义的句子和无意义的乱码/填充文本。请帮我构建一个 pipeline，用 LLM 判断每条文本是否'有信息量'，过滤掉没信息量的记录。

- **输入字段**：`text`（混杂有意义句子和乱码）
- **期望算子链**：`PromptedFilter`
- **产出字段**：`text`
- **验收**：min_valid_records=2
- **考点**：单字段 LLM 语义过滤用 `PromptedFilter`（而非作用于确定性规则的 `GeneralFilter`）。

---

## 附录：Prompt 包装结构

实验中，上述 `用户 Prompt` 并非直接发给 agent，而是被 `_method_preamble()`
（`runner/task_runner.py`）包装成完整 prompt。对于 agent-backed 方法
（df_agent / mcp_only 等），完整结构为：

```
Construct a DataFlow pipeline for the task below.
The input dataset is already registered: use `input_dataset.id = "<dataset_id>"` verbatim ...
Use the DataFlow MCP tools to browse operators and create the pipeline.

[skill_hint —— 仅当 load_skills=True 时注入]

LLM serving policy: ...
Field/schema policy: ...
Prompt-template policy: ...
Protocol:
- <gates，由 enable_dag_sync / enable_exec_gating / enable_think_first 控制>

Task: <用户 Prompt>
```

### skill_hint 内容（df_agent 方法独有）

仅当方法启用 `load_skills` 时注入以下 policy（这是 skills+MCP 相对 mcp_only 的关键差异）：

1. **语言检测 policy（CRITICAL）**：设置 `lang` 前必须调用 `get_dataset_preview`，从**实际数据内容**判断语言，而非从 prompt 语言推断。`lang="zh"` 作用于英文数据会产出 0 条记录。
2. **参数格式 policy（CRITICAL）**：`create_pipeline` 的 params 用 `{"init": {...}, "run": {...}}` 简单键值字典格式，不用 `get_operator_detail_by_name` 返回的 list-of-dicts 格式。
3. **参数绑定 policy**：覆盖所有默认值——`input_key` 设为真实数据列（不留 `raw_content`），`output_key` 设有意义的名字，`PromptedGenerator` 的 `user_prompt` 必须含 `{input_key}` 占位符。
4. **算子路由表**：QA 生成 → `Text2MultiHopQAGenerator`；情感分类 → `PromptedGenerator`；多字段打分+过滤 → `FormatStrPromptedGenerator + GeneralFilter`；LLM 质量过滤 → `PromptedFilter`；长度过滤 → `WordNumberFilter`；长文摘要 → `ChunkedPromptedGenerator`；字段重命名 → `PandasOperator`。

> **结论**：`mcp_only` 与 `df_agent` 的输入差异仅在于是否注入 `skill_hint`。
> 实验证明该差异是 skills 方法在 QA 类任务上语言检测正确率的主要来源
> （`lang="zh"` 误用是 mcp_only 在 QA 任务上的 #1 失败模式）。

### direct_cc 方法的特殊包装

`direct_cc`（裸 Claude Code，无 MCP/WebUI）使用不同包装——要求其直接写一个
standalone 的 DataFlow pipeline Python 文件并保存到 `/tmp/`，不经过 MCP 工具链。
