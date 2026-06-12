# 实验中发现的问题与改进日志

## 2026-05-08 pilot run 1 (dataflow_agent)

### 现象
`2b_review_governance / dataflow_agent / rep0` 在 89 秒内被后端 guardrail 中止，报错 "模型上下文/KV 缓存已塞满"。
实际 tool calls 序列：
```
list_operator_categories {}
list_operators {}         x 5 次
```
Agent 虽然**先**调了 `list_operator_categories`，但随后**不带 category 参数**连调 5 次 `list_operators`，每次都返回全量 134 个算子定义，迅速塞满上下文。

### 根因分析
1. `DataFlow-Skills/generating-dataflow-pipeline/SKILL.md:16` 只写了 "use mcp__dataflow__list_operators"，**没有规定必须先 list categories 再按 category 过滤**。
2. `list_operators` 的 `category` 参数是可选，agent 的默认倾向是 "我先全量看看"。
3. 多次 list 的行为说明 agent 不信任单次结果，反复拉——可能是因为第一次返回被截断（看下一次 raw trace 验证）或 agent 自身检索策略。

### 对应论文 lesson 2
这正是论文 §6 讲的 lesson："Operator catalogs must be paginated for agents"。**后端 API 已分层**，但 **skill 还没强约束 agent 使用分层方式**。这是**论文声明与代码实现之间的 gap**，需要：
1. 改 skill，把"必须先 list categories，再按 category 拉 operators"写成 MANDATORY 规则；
2. 改 system prompt（agent_session.py:49-121）同样补上这条约束；
3. 可选：让 `list_operators` 在不带 category 时返回 truncated 摘要 + "use category=xxx to get full details" 提示，而不是直接返回 134 个全量对象。

### 行动
先做 1 + 2（skill + system prompt 补约束），重跑 pilot。第 3 项是后续优化。

---

## 2026-05-08 pilot run 3/4 observations

### 观察 2：agent 把 dataset name 当 id
rep2 现象：agent 写 `input_dataset.id = "expt_2b_review_governance"`（我 preamble 用的名字），但真实 id 是后端生成的短 hash `3bd0c917ad`。执行报"数据集未找到"。

**根因**：
- 我 preamble 没区分 name vs id
- 后端设计上对用户给的 name 和系统生成的 id 两者区分不严（API 返回 envelope 里只给 id）
- skill 里没明说 "register_dataset 返回 id，后续必须用该 id"

**修复**：preamble 改为明确 "use input_dataset.id = <real_id> verbatim"。**skill 待改**：在 generating-dataflow-pipeline 里加一段 "dataset id 不是你能起的，必须从 list_datasets / register_dataset 的返回值里拿"。

### 观察 3：agent 不填 llm_serving，导致执行失败
rep3 现象：agent 的 pipeline 里所有 LLM 算子的 `init.llm_serving = None`。执行时后端抱怨 "LLM serving: None"。

**根因**：system prompt 第 97-104 行写了"运行前必须检查 LLM Serving"，但：
- 这条规则是针对"运行前"（触发 Run 按钮前）
- **构建 pipeline 时 agent 根本没意识到 llm_serving 要填 serving id**
- skill generating-dataflow-pipeline 里算子签名示例**也没强调 llm_serving 必填 + 从 list_servings 拿**

**修复**：
- preamble 加 "Before wiring any LLM-based operator, call list_servings and pass the id into llm_serving"
- skill 待改：在每个 LLM 算子模板里明确 "llm_serving 必须是真实 serving id，从 list_servings 获取"

### 观察 4：agent 把 prompt_template 写成 dict 导致算子初始化失败
rep3 现象：`FormatStrPromptedGenerator init prompt_template = {"template": "..."}`，后端报 "处理参数失败: prompt_template"。

**根因**：
- `FormatStrPromptedGenerator` 的 prompt_template 参数期望一个 DIYPromptABC 类实例或 None（加 system_prompt）
- Agent 自作聪明以为 dict 形式可用
- **skill 里 FormatStrPromptedGenerator 的示例没有清楚说明 prompt_template 的正确形态**

**修复**：preamble 加 "supply system_prompt as plain string; do NOT fabricate a prompt_template dict"；skill 里应补充算子正确用法的反例。

---

## 2026-05-09 E1 mid-run observations (60/180 runs)

### 观察 5：1a_qa_basic 上 agent 选错类别，完全没访问 core_text
现象：所有方法（dataflow_agent × 3、mcp_only × 1 已检查）在 1a_qa_basic 都选 `SFTGeneratorSeed` 而不是 `Text2MultiHopQAGenerator`。

Tool call 序列典型：
```
list_operator_categories
list_operators(category=reasoning)
list_operators(category=general_text)
list_operators(category=text_sft)
get_operator_detail_by_name(SFTGeneratorSeed)   <-- 错
create_pipeline([SFTGeneratorSeed])
```

根因：agent 没访问 `core_text` 类别，而 `Text2MultiHopQAGenerator` 恰好归属 `core_text/generate`。`generating-dataflow-pipeline/SKILL.md` 的决策表提到 `Text2MultiHopQAGenerator` 但没写出它在哪个 category，agent 靠类别名字猜测（"QA 像是 SFT" / "QA 像是 reasoning"）。

### 对应论文：真实的"wrong operator choice"失败模式
这是正文 §1 "operator hallucination / invalid field flow / operational omission / UI disconnect" 四类失败之外的**第五类**："miscategorization" —— agent 挑了一个语义上相关但功能不同的算子。值得写进 Failure Analysis。

### 修复（E1 完成后再做，避免污染中期数据）
在 `generating-dataflow-pipeline/SKILL.md` 的决策表里，**每个算子名字后面带上 category**：
- `Text2MultiHopQAGenerator` (core_text/generate)
- `PromptedGenerator` (core_text/generate)
- `GeneralFilter` (core_text/filter)
- `PromptedRefiner` (core_text/refine)
- `FormatStrPromptedGenerator` (core_text/generate)
- KBC trio: `FileOrURLToMarkdownConverterFlash` (knowledge_cleaning/generate) → `KBCChunkGenerator` (knowledge_cleaning/generate) → `KBCTextCleaner` (knowledge_cleaning/refine)

这样 agent 先看到决策表 → 直接去 `core_text` 取 → 省一轮浏览。

### 观察 6：direct_cc 100% UI disconnect
20/20 direct_cc runs（1a, 1b, 2a, 2b）都是 `final_chain: []`（没在 WebUI 创建 pipeline）。这完美印证论文 §1 的 "UI disconnect" failure mode —— 纯 code agent 即使解决了任务也不会主动 register pipeline。

### 观察 7：mcp_only vs dataflow_agent 在 2a_single_language_sentiment 上的差异
目前 2a_single_language_sentiment 上：
- dataflow_agent rep4: success, time_min=1.28
- mcp_only rep0/1/2: all exec_success=False

这是 skill 对执行成功率有贡献的早期信号，需要等 5 reps 都完成再确认。

### 观察 8：context overflow 在 E1 里发生率 1.7%
60 runs 里 1 次 ctx_overflow（`1b_qa_with_filter / mcp_only / rep?`）。说明 skill+prompt 改进后这条 lesson 的发生频率已降低，但仍非零。论文 lesson 2 可以写成"X% 概率发生，改进后降到 Y%"的 before/after 数字——需要单独加一组"stripped skill"对比 run。


