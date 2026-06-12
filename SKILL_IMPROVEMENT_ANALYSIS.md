# Direct CC 失败模式 + 算子选择分析 → Skill 改进信号

## direct_cc 成绩:34/36 e2e pass(收紧 acceptance 后)

只有 2 个失败,且都是**手写脚本的脆弱性**,不是构建判断错误。

## 失败模式分析

### 失败1: 2b rep1 — 过滤过严(6步链最后 GeneralFilter → 0行)
- 前5步全正常(20行),step6 GeneralFilter 过滤后 0 行
- 根因:过滤阈值/条件太严,或打分分布偏低
- **教训**:filter 任务普遍脆弱(score 分布 × 阈值),Harness 4a/4b 也栽在这

### 失败2: 3a rep1 — storage 路径管理错误(FileNotFoundError)
- 自写脚本引用了不存在的中间文件 `./cache/long_doc_summary_step2.jsonl`
- 根因:手写多 step storage.step() 路径管理出错
- **教训**:这正是结构化 pipeline(MCP)能避免的脆弱性 → **支持 harness 价值论点**(harness 帮你管 storage/字段流,手写易错)

## 算子选择金矿:direct_cc 探索 repo 后发现了 skill 没推荐的有效算子

### 信号1【最强】: 打分任务有更鲁棒的专用算子
4a (score_and_filter) 三个成功 run 都**没用** skill 推荐的 `FormatStrPromptedGenerator`,而是各自选了:
- `AlpagasusFilter`(打分+过滤一体)
- `Text2QASampleEvaluator` + GeneralFilter
- `AlpagasusSampleEvaluator` + GeneralFilter

这些专用评估算子 prompt 写死调好,打分稳定 → 3/3 通过。而 Harness 被 skill 路由到 `FormatStrPromptedGenerator` 自写 prompt → 0/8。
**skill 改进**:多字段/QA 打分任务,决策表应优先列专用 evaluator(Text2QASampleEvaluator/AlpagasusSampleEvaluator),`FormatStrPromptedGenerator` 作为 fallback。

### 信号2: PandasOperator 作为"胶水"很有用
1b、2b、3a、4b 的成功 run 频繁用 `PandasOperator` 做中间字段整形(explode QA_pairs、reshape、compute mean)。skill 目前只把它当"字段重命名/展平"用。
**skill 改进**:明确 PandasOperator 可做任意确定性中间变换(尤其打分前后的字段整形)。

### 信号3: PromptedEvaluator 是打分的通用选项
1b、2b、4b 都用了 `PromptedEvaluator`(skill 几乎没提)。它比 FormatStrPromptedGenerator 更专门用于"打分/评估"语义。
**skill 改进**:打分语义优先 PromptedEvaluator / 专用 evaluator,而非通用 generator。

## 核心结论:从"强路由"改为"货比三家"

当前 skill 决策表是**强路由**(每种任务 → 唯一推荐算子),这在简单任务(5a/6a/2a)上 100% 有效,但在打分任务上把 agent 锁死在次优的 FormatStrPromptedGenerator。

direct_cc 的优势恰恰来自**它没有强路由约束,自己探索 repo 货比三家**,于是发现了更鲁棒的专用 evaluator。

**改进方向**:对存在多个合理算子的任务类型(尤其打分/评估),skill 从"指定唯一算子"改为"列出候选 + 选择准则 + 鼓励用 MCP get_operator_detail 比较",让模型自己选最适配的。简单任务保留强路由(避免过度发挥)。

## 待办
1. 改 skill:打分任务列专用 evaluator 候选;放松强路由为"候选+准则";明确 PandasOperator 胶水用途
2. 重跑 4a/4b(+1b/2b)验证 Harness e2e 能否提升
3. no_mcp 数据废弃;复核 no_dag_sync/no_exec_gating 的 gate 在 WS 路径是否生效
