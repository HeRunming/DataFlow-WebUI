# 实验补充计划 (基于 review_advice.md)

## 决策汇总(已确认)
- Direct CC:用 AST parser 公平评测
- 重复次数:主方法(df_agent_v3 + mcp_only)补到 8 rep,direct_cc/ablation 各 3 rep
- 补四块:Direct CC baseline、组件消融、扩 rep+CI、失败归因 taxonomy

---

## 核心发现:旧数据不可比 + Direct CC 有两层公平性问题

### 1. 可比性问题
- `E5_full`(主结果,df_agent_v3 + mcp_only)= 5/28 跑,**最终版后端**
- `E1`(direct_cc 14 task)+ `E2_ablation`(no_skills/no_mcp/no_dag_sync/no_exec_gating 各 36)= 5/9-10 跑,**旧后端+旧 skill**
- 5/9 之后我们修了:pipeline_registry 参数解析、dataflow_engine VAR_KEYWORD、prompted_generator None+str、execute timeout;skill 升 v3
- 结论:**E1/E2_ablation 必须用当前后端重跑**,否则同一 method(dataflow_agent)在两张表数字打架,reviewer 必抓

### 2. Direct CC 两层公平性问题
**问题 A(评测链路)**:direct_cc 写 standalone `.py` 到 /tmp,不进 pipeline registry。但所有 metrics 从 registry+create_pipeline trace 读 → 旧 E1 的 68 run 全部 0/68、final_chain 全空。

**问题 B(更严重,任务不对等)**:看了旧脚本,direct_cc **没用真实 DataFlow 算子**,而是:
- hallucinate `from dataflow import DataFlow, FileStorage, Operator`(不存在的 API)
- 自己从头写 `TextCleaningOperator(Operator)`、`MultiHopQAGenerator(Operator)` 自定义类
- 完全绕过 DataFlow 算子库 → 和 Harness 不是同一个任务(正是 GPT review 第 243-247 行警告的)

如果不修 B,direct_cc 即使评测链路通了,chain 抽出来也是 `text_cleaner`、`qa_generator` 这种自定义名,对不上 expected chain,还是全 0,strawman。

---

## 公平 Direct CC 的设计(修 A+B)

按 GPT review 第 198-235 行的 "documentation-grounded" 设定:

**给 direct_cc**:
1. 相同 task prompt + JSONL sample
2. **静态算子文档快照**(真实算子名+签名,从 MCP 的 list_operators 导出成一个静态 .md/.json 文件放进 direct_cc cwd)
3. 约束 prompt:必须用文档里的真实算子,标准 idiom(`self.op = RealOperatorClass(...)` + `self.op.run(storage=...)`),不许自定义算子
4. 相同 serving(emnlp_gpt4o,已注册)
5. 允许读文件、运行调试

**不给**:MCP live tools、skills、typed validation、WebUI sync

**评测**:写完 .py → `PipelineFileAnalyzer` 抽 chain(structure 指标)→ 注册+执行走同一 backend executor(exec/e2e 指标)。这正好印证 paper 第 75 行 "three paths(agent/UI/code)converge on one executable object"。

---

## 实施步骤

### Step 1:生成静态算子文档快照
从当前 backend 导出真实算子目录(名字+类别+签名+简述)→ `runner/method_envs/direct_cc/OPERATOR_CATALOG.md`

### Step 2:改 direct_cc prompt(task_runner.py:38-48)
- 注入/指向 OPERATOR_CATALOG.md
- 约束:用文档真实算子 + 标准 idiom + 禁止自定义算子

### Step 3:改 direct_cc 评测链路(task_runner.py:235-260)
- direct_cc 跑完后:从 trace/约定路径找 .py
- `PipelineFileAnalyzer.from_file()` 抽 operators + init/run params
- 用抽出的 config 走 create_pipeline + execute(复用现有 backend 路径)
- 让 final_pipeline 非空 → metrics 正常算

### Step 4:跑实验(当前后端,后台分批)
| 实验 ID | methods | reps | runs |
|---|---|---|---|
| E6_direct_cc | direct_cc | 3 | 36 |
| E6_ablation | no_mcp, no_dag_sync, no_exec_gating | 3 | 108 |
| E6_rep_extend | df_agent_v3, mcp_only | +5 (rep3-7) | 120 |

注:no_skills ≈ mcp_only,直接复用;dataflow_agent(full)= df_agent_v3 复用。
组件消融的基线 = df_agent_v3(full system)。

### Step 5:汇总 + 分析
- 重算 tiered metrics(structure/exec/e2e)统一口径
- bootstrap CI(主方法 8 rep)
- 失败归因 taxonomy:每个 fail 归类 construction / execution / LLM-output-quality
- 时间指标措辞修正(create_pipeline commit,非 "valid render")

### Step 6:更新 paper
- 填 abstract 占位符(用真实 3-method 数字)
- 主表加 direct_cc 行
- 加组件消融表
- 加失败归因表
- 加 Deployment Model section(private/local,按 GPT review 第 386-392 行)
- 统一 operator 名(QAScorer → Text2QASampleEvaluator)
- 修 "consistently outperforms"(2b/4a 反例)

---

## 成本估算
- MCP 方法 ~57s/run;direct_cc 更慢(写完整脚本,~3-7min/run,且要执行)
- E6_direct_cc 36 + E6_ablation 108 + E6_rep_extend 120 = 264 runs
- 粗估 agent wall 6-10 小时,后台分批跑
