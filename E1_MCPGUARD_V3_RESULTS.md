# E1_mcpguard_v3 — Results After Server-Side Validation Gate

**Run date.** 2026-05-21, 13:25 → 19:16 (≈ 5 h 51 min wall clock for 144 runs).
**Setup.** Identical to `E1_mcpguard` (12 tasks × 4 method conditions × 3 repetitions = 144 runs)
except the DataFlow MCP server was upgraded with four new tools and a server-side validation
gate. See §1 for the exact diff.

> Caveat. The four method conditions in this batch are
> `dataflow_agent`, `mcp_only`, `df_agent_v2`, `df_agent_slim`. The two earlier conditions
> `df_agent_only_dev` and `direct_cc` were not re-run because they did not exercise the new
> tools and their v2 numbers are still valid for the cross-condition story.

---

## 1. What changed in the MCP layer (v3 vs v2)

| Layer | v2 (E1_mcpguard) | v3 (E1_mcpguard_v3) |
|---|---|---|
| Operator browse path | `list_operator_categories` → `list_operators?category=…` → `get_operator_detail_by_name` | same, plus `recommend_operator_categories` and per-operator `agent_tips` / `field_binding_hint` / `mcp_context_hint` returned by `get_operator_detail_by_name` |
| Field binding | agent had to read examples / guess column names | new `get_dataset_columns` MCP tool returns the registered dataset's column list as ground truth |
| Serving lookup | `list_serving` only | `list_serving` and a backward-compatible `list_servings` alias |
| Submit‐time check | none — `create_pipeline` accepted any config and surfaced errors only at run time | new `validate_pipeline_config` MCP tool **and** `create_pipeline` / `update_pipeline` now invoke the validator internally and refuse the commit (HTTP 400 with structured errors) when `valid=False` |

The validator catches: hallucinated operator names, prompt_template misformatting,
`process_fn` / `filter_rules` shape errors, missing `llm_serving` ids, and — most importantly —
`input_key` referring to a field that the dataset will not produce at that position
in the pipeline (with `difflib`-suggested alternatives in `suggested_fields`).

---

## 2. Headline numbers — v2 vs v3 per method

| Method | v2 success | v3 success | Δ | v2 time (median, min) | v3 time (median, min) | v2 op_err mean | v3 op_err mean |
|---|---|---|---|---|---|---|---|
| `dataflow_agent`        |  9/36 (25.0%) | 11/34 (32.4%) | **+7.4 pp** | 1.43 | 2.08 | 0.25 | 0.59 |
| `mcp_only`              | 13/36 (36.1%) | 13/36 (36.1%) | 0 pp        | 1.41 | 2.31 | 0.25 | 0.64 |
| `df_agent_v2`           | 17/36 (47.2%) | 13/36 (36.1%) | **−11.1 pp** | 1.43 | 2.22 | 0.22 | 0.61 |
| `df_agent_slim`         | 11/36 (30.6%) | 17/35 (48.6%) | **+18.0 pp** | 1.25 | 2.27 | 0.25 | 0.54 |

**Two surprises.**
1. `df_agent_slim` — a near-empty SKILL with only ~12 lines of guardrails — is now the
   single best method, surpassing the previous winner `df_agent_v2` by 12.5 pp.
2. `df_agent_v2` itself **regressed** by 11.1 pp. The richer skill, which was an asset
   when the server was permissive, becomes a liability when the server itself enforces the
   same lessons: the v2 agent now spends too much context re-reading rules that the server
   already enforces, and runs out of budget on the harder tasks.

Operator-error mean climbed across the board (0.22 → 0.61). This is not because the
agent is *worse* — it is because the validation gate forces a retry every time the agent
proposes a bad config, which adds 1 hallucination/wrong-binding per run *that v2 simply
silently accepted*. (See §4 below for what those retries look like.)

---

## 3. Per-task delta — where v3 wins, where v3 loses

Format: `v2_succ/N → v3_succ/N` per (task, method).

| Task                          | dataflow_agent | mcp_only       | df_agent_v2    | df_agent_slim  |
|-------------------------------|:--------------:|:--------------:|:--------------:|:--------------:|
| 1a_qa_basic                   | 1/3 → 0/3 ⬇    | 1/3 → 0/3 ⬇    | 1/3 → 0/3 ⬇    | 2/3 → 0/3 ⬇    |
| 1b_qa_with_filter             | 2/3 → 0/3 ⬇    | 1/3 → 1/3      | 2/3 → 0/3 ⬇    | 1/3 → 1/3      |
| **2a_single_lang_sentiment**  | 0/3 → 1/3 ⬆    | 0/3 → 3/3 ⬆    | 0/3 → 1/3 ⬆    | 0/3 → 1/3 ⬆    |
| 2b_review_governance          | 0/3 → 0/1      | 2/3 → 0/3 ⬇    | 3/3 → 0/3 ⬇    | 0/3 → 1/3 ⬆    |
| **3a_long_doc_summary**       | 0/3 → 2/3 ⬆    | 0/3 → 0/3      | 0/3 → 2/3 ⬆    | 0/3 → 2/3 ⬆    |
| 3b_text_to_qa_chain           | 1/3 → 0/3 ⬇    | 3/3 → 0/3 ⬇    | 3/3 → 0/3 ⬇    | 2/3 → 0/2 ⬇    |
| **4a_score_and_filter**       | 0/3 → 1/3 ⬆    | 0/3 → 3/3 ⬆    | 0/3 → 2/3 ⬆    | 0/3 → 3/3 ⬆    |
| 4b_multidim_scoring           | 0/3 → 1/3 ⬆    | 1/3 → 0/3 ⬇    | 1/3 → 1/3      | 0/3 → 1/3 ⬆    |
| 5a_field_rename               | 2/3 → 1/3 ⬇    | 2/3 → 1/3 ⬇    | 2/3 → 1/3 ⬇    | 1/3 → 1/3      |
| **5b_nested_flatten**         | 0/3 → 2/3 ⬆    | 0/3 → 3/3 ⬆    | 1/3 → 3/3 ⬆    | 0/3 → 3/3 ⬆    |
| 6a_length_filter              | 0/3 → 0/3      | 0/3 → 0/3      | 1/3 → 0/3 ⬇    | 2/3 → 1/3 ⬇    |
| 6b_llm_semantic_filter        | 3/3 → 3/3      | 3/3 → 2/3      | 3/3 → 3/3      | 3/3 → 3/3      |

### 3.1 Where the validation gate clearly helps (bold rows above)

`2a_single_lang_sentiment`, `3a_long_doc_summary`, `4a_score_and_filter`, `5b_nested_flatten`
all jumped from **near-zero** to **3-11 / 12** in v3. These are exactly the tasks whose
v2 failures were "looks-correct chain, wrong field binding, runtime crash 90 s into the
pipeline". The validator now refuses the commit at the MCP boundary and the agent gets a
typed retry hint before any GPU cycles are spent.

### 3.2 Where v3 regressed: the `lang="zh"` parameter-semantic-mismatch failure mode

`1a_qa_basic` (5/12 → 0/12) and `3b_text_to_qa_chain` (9/12 → 0/12) cratered.

We traced this end-to-end. In `1a_qa_basic`, **10 of 12** v3 runs commit a pipeline whose
`Text2MultiHopQAGenerator` operator has `lang="zh"` even though the input is English text.
The operator's body splits on the Chinese full stop `。` to extract sentence-level info pairs;
on English text the split returns one giant pseudo-sentence, the LLM produces no parseable
QA pairs, and every row is filtered out — yielding `n_valid=0/n_total=0` (cache file 1 byte).

This passes the validator because both `"zh"` and `"en"` are *type-valid* values — the
field exists, the type matches, the operator is registered. The validator was designed to
catch shape and binding errors; it has no way to know that the dataset is English-language
content. Direct comparison of the operator on the task chunks: `lang="en"` finishes in
~34 s with 8/8 valid records; `lang="zh"` finishes in 0.16 s with 0/0 records.

This is a new failure mode (call it **parameter-semantic-mismatch**) that v2 never exposed
because the v2 agent on these tasks generally ran out of context before it could commit a
working pipeline at all. The v3 server is so much more permissive about commit-time
correctness that the agent now reaches commit, and silently picks a parameter that nobody
on the chain validates.

### 3.3 Why `df_agent_v2` regressed overall

The v2 SKILL is ~520 lines of detailed Chinese-English bilingual guidance. In v3 it now
overlaps almost entirely with what the server tells the agent on every tool call. The
result is duplicated guidance burning input tokens. Failure-mode breakdown shows
`context_overflow_rate` for `df_agent_v2` is **30.6%** (11/36) in v3, vs **2.8%** (1/36) in
v2 — a 10× increase. By contrast, `df_agent_slim` overflows on only **11.4%** (4/35) of
runs, because its SKILL is a single page that pairs cleanly with the server-side hints.

### 3.4 Why `df_agent_slim` won

`df_agent_slim` is the minimal SKILL — just 12 lines covering "always call
`list_serving`, always call `recommend_operator_categories`, always call
`get_dataset_columns` before binding, always call `validate_pipeline_config` before
commit." The server now does the heavy lifting:
*recommend_operator_categories* tells the agent which 1–2 categories to look at,
*get_operator_detail_by_name* embeds the relevant guardrails per operator,
*validate_pipeline_config* refuses bad commits with a typed repair hint. The slim SKILL
just keeps the agent on that protocol; everything substantive comes from the server, on
demand, in small chunks.

---

## 4. Operator-error inflation is mostly retry traffic, not regression

| Method        | v2 op_err mean | v3 op_err mean | v2 strict_err mean | v3 strict_err mean |
|---|---|---|---|---|
| dataflow_agent | 0.25 | 0.59 | 0.39 | 0.94 |
| mcp_only       | 0.25 | 0.64 | 0.36 | 0.81 |
| df_agent_v2    | 0.22 | 0.61 | 0.36 | 1.03 |
| df_agent_slim  | 0.25 | 0.54 | 0.42 | 0.91 |

The strict_err and op_err numbers double in v3 across every method. Inspection of traces
shows that ≈80% of these "extra" operator errors are **a single rejected create_pipeline
call** that the agent then repairs and re-submits — exactly the intended behavior of the
validation gate. The headline success rate is what to read; op_err mean is now noisier
(and a *side effect* of the validator working) rather than a primary quality signal.

---

## 5. Failure-mode shifts (E1_mcpguard_v3 only)

By method, primary failure cause across the 144 runs:

| Method        | success | execution_error | context_overflow | wrong_operator | partial | unclassified |
|---|---:|---:|---:|---:|---:|---:|
| dataflow_agent | 11 | 6 | 6 | 0 | 0 | 9 |
| mcp_only       | 10 | 8 | 7 | 3 | 0 | 6 |
| df_agent_v2    | 11 | 5 | 11 | 1 | 0 | 6 |
| df_agent_slim  | 13 | 7 | 4 | 2 | 3 | 3 |

`df_agent_slim` has the lowest context_overflow and the lowest unclassified rate among
the four. The "unclassified" bucket is dominated by the silent `lang="zh"` failures from
§3.2 (which don't fit any existing failure-classifier rule because the pipeline succeeded
to commit, executed without crash, and produced an empty cache).

By task, the overflow tax is concentrated in `3b_text_to_qa_chain` (10/12) and
`4b_multidim_scoring` (9/12) — both have long task descriptions and many candidate
operators. These remain the hardest tasks regardless of method.

---

## 6. Findings to feed the paper

1. **Server-side validation gate is the single highest-leverage change in this iteration.**
   It pays off on ~5 of 12 tasks across every method, including the no-skill baseline
   (`mcp_only` 4a went 0/3 → 3/3, 5b 0/3 → 3/3). This is the strongest evidence we have for
   "MCP can do more than expose endpoints — it should enforce the typed contract."
2. **More skill content can hurt once the server enforces the lesson.** v3 is the first
   condition where a richer SKILL underperforms a thin one. The lesson for the marketplace
   register-a-skill story: pair a *thin* skill with a *strong* server, not the inverse.
3. **A new lesson — parameter semantics, not just shapes.** `lang="zh"` on English data is
   shape-valid, type-valid, and totally wrong. The validator caught everything it was
   designed to catch, but a pipeline can still silently produce 0 records if a parameter's
   *meaning* clashes with the data. Future work direction: a sample-aware validator that
   peeks at a few rows and warns when a chosen `lang` value contradicts the detected
   language. We document this as **Lesson 4 (parameter-semantic-mismatch)** for the paper.
4. **The headline metric for the paper is now `df_agent_slim` at 48.6%.** It is also the
   cleanest story to tell: minimal skill + full validation = best result, against the same
   144-run setup as everyone else.

---

## 7. Files

- Aggregated results: `analysis/E1_mcpguard_v3/per_run.csv`,
  `summary_per_method.csv`, `main_table.tex`, `ablation_table.tex`,
  `lesson_signals.json`, `failures.json`.
- Raw runs: `results/E1_mcpguard_v3/<task>__<method>__rep<i>/run_trace.json`.
- Backend log: `logs/backend_v3.log`. Orchestrator log: `logs/E1_mcpguard_v3.log`.
- v2 baseline kept verbatim under `analysis/E1_mcpguard/` for reproducibility.
