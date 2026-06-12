# Engine + Acceptance Fixes (mid-experiment)

Applied during E1_mcpguard run (2026-05-10).

## Fixes deployed

### 1. dataflow_engine.py: drop unsupported run-params
Agents commonly set `system_prompt` (a DYNAMIC param) on operators whose
`run()` doesn't accept it (e.g. `Text2MultiHopQAGenerator.run(self, storage,
input_key, output_key, output_meta_key)`). The engine then called
`operator.run(**run_params)` and crashed with
`TypeError: run() got an unexpected keyword argument 'system_prompt'`.

Fix: skip params that are neither in the operator's run signature nor
captured by **kwargs. Same logic added to init params (with whitelist for
the special-cased serving / prompt_template / etc.).

### 2. dataflow_engine.py: unwrap llm_serving dict
Agents sometimes pass `llm_serving={"id": "9b63c43bf..."}` instead of the
bare string id. Engine then crashed with `TypeError: unhashable type: 'dict'`
on `if serving_id not in serving_instance_map`.

Fix: if `param_value` is a dict, take `.get("id")` or `.get("serving_id")`.

### 3. runner/metrics.py: schema field synonyms
Task acceptance schemas declared field names like `qa_pairs` but agents
correctly chose `multi_hop_qa` as the operator's `output_key`. The schema
check rejected these legitimate outputs.

Fix: schema keys can now be a pipe-separated synonym set
(`"qa_pairs|multi_hop_qa|QA_pairs"`); at least one synonym must match.
Updated the 5 affected task definitions.

## Remaining issue (not fixed)

Agents sometimes leave `input_key` / `output_key` at default values even
when the user prompt says "field is called 'chunk'". Operator's default is
`cleaned_chunk`, so execution fails with `Missing required column(s):
['cleaned_chunk']`. This is an agent reasoning gap, not an infra bug.

Two possible follow-ups (both out of scope for this PR):
- Make `get_operator_detail_by_name` response highlight that `input_key`
  defaults to the operator's expected field, prompting the agent to compare.
- Add a runtime "missing column" error suggester in dataflow_engine that
  lists the actual columns present so the agent can self-correct on retry.

## Pipeline timeline

- 14:55: launched first E1_mcpguard (8 runs done, all exec=False due to
  TypeError on system_prompt)
- 15:24: relaunched after engine fix (5 runs done, 1 succeeded, 4 failed
  with llm_serving dict bug)
- 15:35: relaunched after llm_serving-dict + schema-synonyms fixes (running)
