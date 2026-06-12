# 后续实验计划（skill_ablation 跑完后执行）

## 当前已完成

| 实验 | 状态 | runs | 耗时 |
|---|---|---|---|
| pilot (2b_review) | ✅ | 9 | ~30 min |
| E1 main (4 方法 × 12 任务 × 5 reps) | ✅ | 180 | 4.7h |
| v2 smoke (2b) | ✅ | 1 | 3 min |
| skill_ablation (slim + only_dev × 12 × 3) | 🏃 跑中 21/70 | ~3h |

## 数据问题：E1 的 3a/3b 任务已换

- 旧 `3a_url_to_chunks` / `3b_pdf_to_qa` 要求的算子（`FileOrURLToMarkdownConverterFlash`, `KBCChunkGenerator`）在当前 API-only 环境中**不可用**
- 已替换为 `3a_long_doc_summary`（ChunkedPromptedGenerator）和 `3b_text_to_qa_chain`（refine + QA）
- **E1 的 3a/3b 数据需要在新任务上重跑**：4 方法 × 2 任务 × 5 reps = **40 runs**

## 待跑实验（按优先级）

### E1-fill (高优先级，必跑)
- **目的**：用新的 3a/3b 任务补齐 E1 的 4 方法数据
- **规模**：`dataflow_agent, mcp_only, direct_cc` × `3a_long_doc_summary, 3b_text_to_qa_chain` × 5 reps = **30 runs**
- **耗时**：~1 小时

### E3-v2 (高优先级，verify skill rewrite)
- **目的**：验证 v2 skill 在 12 任务上的效果
- **规模**：`df_agent_v2` × 12 任务 × 5 reps = **60 runs**
- **耗时**：~2 小时

### E2 消融（中优先级，论文必要）
- **目的**：Table 2 消融数据
- **规模**：`no_mcp, no_skills, no_dag_sync, no_exec_gating` × 12 任务 × 3 reps = **144 runs**
- **耗时**：~4 小时

### E4 paginated operator lesson (中低优先级)
- **目的**：lesson 2 的 before/after 证据
- **规模**：用无 category rule 的 skill 版本 × 6 任务 × 3 reps = **18 runs**
- **耗时**：~1 小时

## 总时长估算

必跑：E1-fill + E3-v2 + E2 = **~7 小时** 批量实验
可选：E4 = **~1 小时**

## 后端重启检查

等当前 skill_ablation 跑完后，需要重启 uvicorn：
- 已装依赖 `chonkie, trafilatura, word2number, rapidfuzz, nltk`（KBC 系列算子会可用）
- 但**API-only 约束**下 KBC 仍不会被推荐（v2 skill 已明确）
- **重启理由**：使已装依赖生效，算子总数从 130 恢复到 ~145，`list_operator_categories` 返回更完整

## 论文 tex 状态

已填：
- N = 12（Abstract, §5）
- reps = 5（§5）
- caption 里的 N

待填（等实验数据）：
- Time/Succ/Op.err/Fld.err 等数字
- Ablation 段落
- User study 段落（这个是人工实验，论文里标 formative/pilot）
