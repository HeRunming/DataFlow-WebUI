# DataFlow-Agent EMNLP 实验 — 当前状态

**最后更新**：2026-05-09 13:49 CST

## 后台运行中的进程

| 进程 | pid | 角色 | 预计剩余 |
|---|---|---|---|
| uvicorn | 2468163 | DataFlow-WebUI 后端（8000） | — |
| skill_ablation runner | 3015212 | 72 runs slim + only_dev 实验 | ~2 小时 |
| orchestrator | 3153519 | 后续实验自动调度 | ~9 小时总 |

## 实验已完成的数据

| 实验 | runs | 用途 |
|---|---|---|
| pilot | 9 | infra 验证 |
| E1 main | 180 | 论文 Table 1 基线（但 3a/3b 需新任务替补） |
| v2 smoke | 1 | v2 skill 可行性 |

## 已部署的改动

1. `/data/workspace/df_web_and_skills/DataFlow-Skills/generating-dataflow-pipeline/SKILL.md` — MCP 三步分层规则
2. `/data/workspace/df_web_and_skills/DataFlow-WebUI/backend/app/services/agent_session.py` — system prompt MCP 分层
3. `/data/workspace/df_web_and_skills/DataFlow/dataflow/serving/__init__.py` — try/except 兜底 transformers 5.x
4. 装依赖：`chonkie trafilatura word2number rapidfuzz nltk`（后端重启后生效）
5. Shadow cwds：`method_envs/{df_agent_slim, df_agent_only_dev, df_agent_v2}`
6. 新 skill：`method_envs/df_agent_v2/.claude/skills/dataflow-pipeline-v2/SKILL.md`
7. 新任务：`tasks/{3a_long_doc_summary, 3b_text_to_qa_chain}`（替换原 3a/3b）

## 论文 tex 已回填

- Abstract / §5 Setup：N=12, reps=5
- Table 1 caption：N=12
- §Limitations：加 API-only 约束段

## 论文 tex 待填（等实验数据）

- Table 1 数字：Time, Succ, Op.err, Fld.err（每个 method 一行）
- §5 Main Results 段：从 X% 到 Y% 的提升数字
- §5 Ablation 段：4 个消融 delta
- §5 User Study：待人工实验（可先填 formative 占位）
- §5 Failure Analysis：等 failures.json 最终版

## 9 小时后会自动产生的文件

```
/data/workspace/emnlp_experiments/analysis/
├── E1/
│   ├── per_run.csv
│   ├── summary_per_method.csv
│   ├── main_table.tex       ← 可直接贴论文
│   ├── failures.json
│   └── lesson_signals.json
├── E2_ablation/
│   ├── per_run.csv
│   ├── summary_per_method.csv
│   └── ablation_table.tex   ← 可直接贴论文
└── skill_ablation/
    └── per_run.csv
```

## 快速状态检查命令

```bash
# skill_ablation 进度
ls /data/workspace/emnlp_experiments/results/skill_ablation/ | wc -l

# orchestrator 当前阶段
tail -3 /data/workspace/emnlp_experiments/logs/orchestrate.log

# 后端健康
curl -s http://localhost:8000/api/v1/datasets/ | head -c 50

# 全盘运行中的进程
ps -p 2468163,3015212,3153519 -o pid,etime,stat,cmd 2>/dev/null
```

## 如果出问题，该做什么

### 后端崩了
```bash
pkill -9 -f "uvicorn app.main:app"
cd /data/workspace/df_web_and_skills/DataFlow-WebUI/backend
setsid uvicorn app.main:app --port 8000 --host 0.0.0.0 \
  > /data/workspace/emnlp_experiments/logs/backend.log 2>&1 < /dev/null &
```

### orchestrator 停了
```bash
# 看它停到哪一步
tail -50 /data/workspace/emnlp_experiments/logs/orchestrate.log
# 如果还有后续步骤没跑，直接手动跑缺失的
cd /data/workspace/emnlp_experiments
python3 -m runner.batch_runner --experiment-id E1 --tasks all --methods df_agent_v2 --reps 5
```

### 如何回填论文
等 `analysis/E1/main_table.tex` 生成后：
```bash
cat /data/workspace/emnlp_experiments/analysis/E1/main_table.tex
```
直接替换 `/data/workspace/emnlp_revised/emnlp2023.tex` 的 Table 1 块即可。
