# EMNLP Experiments — DataFlow Agent Evaluation Harness

Evaluation harness comparing code-agent methods for constructing DataFlow
pipelines, used for the EMNLP Industry Track submission.

## Methods compared

| Method | What it gets | How it builds |
|---|---|---|
| `pure_cc` | Nothing DataFlow-specific | Writes arbitrary Python (pandas / requests / openai) |
| `direct_cc` | The DataFlow source repo to read | Writes a standard DataFlow pipeline `.py` (no MCP, no skills) |
| `mcp_only` | Live MCP tool surface (operator registry) | Calls MCP tools; **no** construction skill |
| `df_agent_v3` (DataFlow-Harness) | MCP + `DataFlow-Skills` + validation + UI sync | Full governed harness |
| `no_mcp` / `no_dag_sync` / `no_exec_gating` | Ablations of the full harness | — |

## Tiered metric (strictly nested: E2E ⊆ Exec ⊆ Struct)

- **Struct** — operator chain matches the task's required slots, no hallucinated ops. (N/A for `pure_cc`, which has no operators.)
- **Exec** — Struct **and** the pipeline runs to completion without error.
- **E2E** — Exec **and** the final JSONL satisfies the task acceptance (schema + value checks).

Acceptance has two modes: `generate` (every input should yield an output row)
and `filter` (surviving-row count depends on the model/threshold, not the
agent — so 0 rows is acceptable). `value_checks` guard against gaming
(e.g. classification labels must be in the valid set and non-constant).

## Layout

- `runner/` — per-run orchestration (`task_runner.py`), method configs (`config.py`), backend client, WS/CLI agent drivers, metrics.
- `analysis/` — aggregation (`aggregate_v2.py`), cost table (`cost_table.py`), re-scoring utilities.
- `tasks/` — 12 pipeline-construction tasks (`task.json` + `sample.jsonl`), 6 categories × 2 variants.

## Config

LLM serving is configured via environment variables (no secrets in code):

```bash
export DF_API_KEY=sk-...                                  # your API key
export DF_API_URL=https://api.openai.com/v1/chat/completions   # or your gateway
```

## Run

```bash
# one method × all tasks × N reps
python -m runner.batch_runner --experiment-id E1 --tasks all --methods df_agent_v3 --reps 8

# aggregate + cost
python analysis/aggregate_v2.py
python analysis/cost_table.py
```
