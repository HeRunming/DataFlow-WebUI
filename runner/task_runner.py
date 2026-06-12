"""Run a single (task × method × repetition) and produce a run_trace JSON.

Orchestration:
  1. Register dataset from task.sample_path (idempotent; id = task_id)
  2. Snapshot existing pipelines so we can detect which one the agent created
  3. Build user prompt from task + inject method-specific preamble
  4. Drive agent via WS (for agent methods) or generate pipeline directly (manual/direct_cc)
  5. Identify newest pipeline -> this is the agent's output
  6. Trigger execution via REST, poll, download final step output
  7. Compute metrics via metrics.summarize_run
  8. Persist run_trace.jsonl
"""
from __future__ import annotations
import asyncio
import json
import os
import time
import uuid
from dataclasses import asdict
from pathlib import Path

from loguru import logger

from .config import (
    METHODS, MethodConfig, TaskSpec, RESULTS_ROOT, LOGS_DIR,
    DEFAULT_TIMEOUT_SEC, load_task, BACKEND_BASE,
)
from .backend_client import DataFlowBackend
from .ws_client import run_ws_chat
from .trace_parser import parse_raw_trace


# ---------- direct_cc: locate the .py the agent wrote + parse its operator chain ----------

def _extract_direct_cc_pyfile(raw_trace_path: Path) -> str | None:
    """Find the pipeline .py path direct_cc produced.

    Priority: (1) a Write tool call to a /tmp/direct_cc_pipeline_*.py, then
    (2) a /tmp/direct_cc_pipeline_*.py mentioned in the final assistant text.
    Returns the most recent existing path, or None.
    """
    import re
    candidates: list[str] = []
    pat = re.compile(r"/tmp/direct_cc_pipeline_\w+\.py")
    if not raw_trace_path.exists():
        return None
    for line in raw_trace_path.open(encoding="utf-8"):
        try:
            p = json.loads(line).get("payload", {})
        except Exception:
            continue
        msg = p.get("message", {}) if isinstance(p.get("message"), dict) else {}
        content = msg.get("content")
        if isinstance(content, list):
            for c in content:
                if not isinstance(c, dict):
                    continue
                if c.get("type") == "tool_use" and c.get("name") in ("Write", "Edit"):
                    fp = (c.get("input") or {}).get("file_path", "")
                    if pat.fullmatch(fp or ""):
                        candidates.append(fp)
                elif c.get("type") == "text":
                    candidates += pat.findall(c.get("text", ""))
        if p.get("type") == "result":
            candidates += pat.findall(str(p.get("result", "")))
    # keep last existing candidate
    for fp in reversed(candidates):
        if Path(fp).exists():
            return fp
    return None


def _extract_pure_cc_output(raw_trace_path: Path) -> str | None:
    """Find the result JSONL path pure_cc wrote.

    pure_cc is told to write to /tmp/pure_cc_output_*.jsonl. We scan the trace
    for that path (tool calls, assistant text, result) and also fall back to
    globbing /tmp for the newest such file produced during the run.
    """
    import re, glob
    pat = re.compile(r"/tmp/pure_cc_output_\w+\.jsonl")
    candidates: list[str] = []
    if raw_trace_path.exists():
        for line in raw_trace_path.open(encoding="utf-8"):
            try:
                p = json.loads(line).get("payload", {})
            except Exception:
                continue
            msg = p.get("message", {}) if isinstance(p.get("message"), dict) else {}
            content = msg.get("content")
            if isinstance(content, list):
                for c in content:
                    if not isinstance(c, dict):
                        continue
                    if c.get("type") == "tool_use":
                        candidates += pat.findall(json.dumps(c.get("input") or {}))
                    elif c.get("type") == "text":
                        candidates += pat.findall(c.get("text", ""))
            if p.get("type") == "result":
                candidates += pat.findall(str(p.get("result", "")))
    for fp in reversed(candidates):
        if Path(fp).exists() and Path(fp).stat().st_size > 0:
            return fp
    return None


def _direct_cc_chain_from_pyfile(py_path: str) -> list[str]:
    """Extract the ordered operator class names from a direct_cc .py via the
    backend's AST analyzer (same parser the WebUI uses to import a hand-written
    pipeline file). Used for the structure-pass metric."""
    import sys
    webui_backend = "/data/workspace/df_web_and_skills/DataFlow-WebUI/backend"
    if webui_backend not in sys.path:
        sys.path.insert(0, webui_backend)
    from app.services.pipeline_registry import PipelineFileAnalyzer  # type: ignore
    try:
        analyzer = PipelineFileAnalyzer.from_file(py_path)
        return analyzer.execution_order()
    except Exception:
        return []


def _df_api_key() -> str | None:
    """Read the serving api_key the backend was configured with, so a
    direct_cc script (which initializes its own APILLMServing_request) can
    authenticate against the SAME endpoint as the MCP methods."""
    import os, yaml
    if os.environ.get("DF_API_KEY"):
        return os.environ["DF_API_KEY"]
    reg = "/data/workspace/df_web_and_skills/DataFlow-WebUI/backend/data/serving_registry.yaml"
    try:
        d = yaml.safe_load(open(reg))
        for _sid, s in (d or {}).items():
            for p in s.get("params", []):
                if p.get("name") == "api_key" and p.get("value"):
                    return p["value"]
    except Exception:
        pass
    return None


def _run_direct_cc_pyfile(py_path: str, run_dir: Path, timeout_sec: int) -> dict:
    """Execute a direct_cc pipeline .py as a standalone script and locate its
    final-step output JSONL.

    The backend executor rebuilds operators from a structured config and cannot
    round-trip a direct_cc script's Python-level params (e.g. a GeneralFilter
    ``filter_rules=[lambda df: ...]``). So for direct_cc we run the script the
    way its author intended — directly — in an isolated cwd, then read the
    highest-numbered ``dataflow_cache_step_step*.jsonl`` it wrote. This is the
    most faithful measure of what a documentation-grounded bare agent achieves.
    """
    import os, re, subprocess, glob
    work = run_dir / "dcc_exec"
    work.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    key = _df_api_key()
    if key:
        env["DF_API_KEY"] = key
    result: dict = {"status": None, "final_output_path": None}
    try:
        proc = subprocess.run(
            ["python", py_path],
            cwd=str(work),
            env=env,
            capture_output=True,
            text=True,
            timeout=timeout_sec,
        )
        (work / "exec_stdout.log").write_text(proc.stdout[-20000:], encoding="utf-8")
        (work / "exec_stderr.log").write_text(proc.stderr[-20000:], encoding="utf-8")
        if proc.returncode != 0:
            result["status"] = "error"
            # last stderr line is usually the exception
            tail = (proc.stderr.strip().splitlines() or ["<no stderr>"])[-1]
            result["error"] = tail[:300]
            return result
        # Find step outputs anywhere under the isolated cwd. Use os.walk (not
        # glob '**', which skips dotted dirs) because scripts often write to a
        # hidden cache dir like ./.cache/. Exclude our own log files.
        all_jsonl: list[str] = []
        for root_dir, _dirs, files in os.walk(work):
            for fn in files:
                if fn.endswith(".jsonl"):
                    all_jsonl.append(os.path.join(root_dir, fn))
        steps = [p for p in all_jsonl if "step" in os.path.basename(p)]
        if not steps:
            steps = all_jsonl  # some scripts write a flat output file
        if steps:
            def _step_num(p: str) -> int:
                m = re.findall(r"step(\d+)", os.path.basename(p))
                return int(m[-1]) if m else -1
            # prefer highest step number; tie-break by mtime (latest written)
            steps.sort(key=lambda p: (_step_num(p), os.path.getmtime(p)))
            result["final_output_path"] = steps[-1]
            result["status"] = "completed"
        else:
            result["status"] = "completed_no_output"
        return result
    except subprocess.TimeoutExpired:
        result["status"] = "timeout"
        return result
    except Exception as e:  # pragma: no cover
        result["status"] = "error"
        result["error"] = str(e)[:300]
        return result


from .metrics import summarize_run
from .method_env import ensure_all_shadows


# ---------- prompt templates ----------

def _method_preamble(cfg: MethodConfig, task: TaskSpec, dataset_id: str) -> str:
    """Build the method-specific prefix to prepend to the user's task prompt."""
    if cfg.name == "direct_cc":
        # Bare CC: no MCP, no WebUI — but we give it the DataFlow repo to read,
        # mirroring a real user who points a coding agent at the framework source.
        # This avoids a strawman where the agent hallucinates operators because
        # it was never shown where the operator library lives.
        repo_dir = str((cfg.agent_cwd / "dataflow_repo").resolve())
        return (
            f"You are asked to construct a DataFlow pipeline for the task below.\n"
            f"The JSONL sample is at {task.sample_path}.\n\n"
            f"The DataFlow framework source code is available at `./dataflow_repo/` "
            f"(operators live under `./dataflow_repo/dataflow/operators/`, organized by "
            f"category such as core_text, general_text, reasoning). "
            f"FIRST explore the repository to discover the real operators available and their "
            f"interfaces: read `./dataflow_repo/README.md` for the canonical pipeline idiom, and "
            f"grep/read the operator source files under `./dataflow_repo/dataflow/operators/` to "
            f"find the operators relevant to this task and their `__init__` / `run` signatures.\n\n"
            f"Then write a standard DataFlow pipeline Python file that processes this sample, "
            f"using FileStorage, LLM serving initialization (APILLMServing_request), the REAL "
            f"DataFlow operators you found in the repository (instantiated in `__init__`), and "
            f"sequential `self.<op>.run(storage=self.storage.step(), ...)` calls in `forward()`. "
            f"Do NOT implement your own operator classes — use only the operators that exist in "
            f"the DataFlow library. Configure the LLM serving with "
            f"api_url=\"{os.environ.get('DF_API_URL', 'https://api.openai.com/v1/chat/completions')}\" and model_name=\"gpt-4o\".\n\n"
            f"Save the pipeline to /tmp/direct_cc_pipeline_{uuid.uuid4().hex[:6]}.py and print its path.\n\n"
            f"Task: {task.user_prompt}"
        )

    if cfg.name == "pure_cc":
        # Pure Claude Code: NO DataFlow framework at all. The agent writes an
        # arbitrary standalone Python script (pandas / requests / openai) that
        # reads the sample JSONL and writes a result JSONL. This is the extreme
        # "throwaway script" baseline — no operators, no framework, no UI.
        out_path = f"/tmp/pure_cc_output_{uuid.uuid4().hex[:6]}.jsonl"
        return (
            f"You are asked to accomplish the data-processing task below by writing a "
            f"standalone Python script (no special framework).\n"
            f"The input JSONL sample is at {task.sample_path} (one JSON object per line).\n\n"
            f"You may use any standard libraries — e.g. pandas, json, re — and for any "
            f"LLM-based step use the OpenAI-compatible chat API at "
            f"`{os.environ.get('DF_API_URL', 'https://api.openai.com/v1/chat/completions')}` with model `gpt-4o` "
            f"(read the API key from the `DF_API_KEY` environment variable; it is already set). "
            f"Use the `requests` or `openai` library to call it.\n\n"
            f"Write the final result as JSONL (one JSON object per line) to EXACTLY this path: "
            f"`{out_path}`. Each output row must contain the fields the task asks for. "
            f"Then run your script so it actually produces that output file, and print the path.\n\n"
            f"Task: {task.user_prompt}"
        )

    # agent-backed methods (dataflow_agent, mcp_only, no_*)
    gates = []
    if not cfg.enable_dag_sync:
        gates.append("Do NOT call render_pipeline_in_editor; only create the pipeline.")
    if not cfg.enable_exec_gating:
        gates.append(
            "After creating the pipeline you MAY proactively call execute_pipeline "
            "without waiting for user confirmation.")
    else:
        gates.append(
            "Do NOT call execute_pipeline; leave execution for the user to trigger.")
    if not cfg.enable_think_first:
        gates.append(
            "You are free to call create_pipeline as soon as you have any candidate operator chain.")
    else:
        gates.append(
            "Think through the full operator chain first, then make a single create_pipeline call.")
    gate_text = "\n".join(f"- {g}" for g in gates)

    skill_hint = ""
    if cfg.load_skills:
        skill_hint = (
            "Language detection policy (CRITICAL): Before setting any `lang` parameter, call `get_dataset_preview` "
            "and determine the language from the ACTUAL DATA CONTENT — not from this prompt's language. "
            "lang=\"zh\" on English data produces 0 records (splits on Chinese period which doesn't exist).\n"
            "Parameter format policy (CRITICAL): When calling `create_pipeline`, use this EXACT params format for each operator:\n"
            "  ```\n"
            "  \"params\": {\n"
            "    \"init\": {\"llm_serving\": \"<serving_id>\", \"lang\": \"en\", ...},\n"
            "    \"run\": {\"input_key\": \"<column>\", \"output_key\": \"<name>\", ...}\n"
            "  }\n"
            "  ```\n"
            "  Use simple key-value dicts for init and run. Do NOT use the list-of-dicts format from get_operator_detail_by_name.\n"
            "  For `input_dataset`, use the string ID directly: `\"input_dataset\": \"<dataset_id>\"`.\n"
            "  For GeneralFilter, `filter_rules` must be a list of code strings: `\"filter_rules\": [\"lambda df: df['col'] > 3\"]`.\n"
            "  For FormatStrPromptedGenerator, `input_keys` in run params must be a dict mapping template placeholders to dataset columns: "
            "`\"input_keys\": {\"question\": \"question\", \"answer\": \"answer\"}`. The filter_rules lambda must reference the SAME field name as the generator's `output_key`.\n"
            "Parameter binding policy: After calling `get_operator_detail_by_name`, override ALL defaults — "
            "set `input_key` to an actual dataset column (never leave as `raw_content`), "
            "set `output_key` to a meaningful name, "
            "and for multi-field ops set `input_keys` to all fields referenced in the prompt template.\n"
            "  For PromptedGenerator: `user_prompt` MUST contain `{input_key}` as a placeholder so the operator injects the actual data. "
            "Example: `\"user_prompt\": \"Review: {input_key}\\nSentiment:\"`. If user_prompt is empty, the LLM never sees the input data and the operator fails.\n"
            "Operator routing:\n"
            "  - QA generation → Text2MultiHopQAGenerator or Text2QAGenerator (core_text)\n"
            "  - Sentiment/classify/label → PromptedGenerator (core_text)\n"
            "  - Score/evaluate QA pairs (question+answer) → PREFER the purpose-built evaluator "
            "Text2QASampleEvaluator (fixed, tuned multi-dimension prompt) + GeneralFilter. "
            "Candidates also: AlpagasusSampleEvaluator, PromptedEvaluator (core_text/eval).\n"
            "  - Score/evaluate generic rows (one quality score) → PREFER PromptedEvaluator "
            "(writes a numeric score, keeps rows) + GeneralFilter; or PromptedFilter (scores+filters in one step).\n"
            "  - Multi-field score with a CUSTOM rubric no evaluator provides → FormatStrPromptedGenerator + GeneralFilter "
            "(LAST resort: a hand-written scoring prompt gives high-variance scores that often filter out every row).\n"
            "  - LLM-based quality/relevance filter → PromptedFilter (core_text)\n"
            "  - Word count/length filter → WordNumberFilter (general_text)\n"
            "  - Summarize long text → ChunkedPromptedGenerator (core_text)\n"
            "  - Field rename/derive/reshape (explode list, compute mean, rename score col) → PandasOperator (core_text); no LLM cost, use freely as glue\n"
            "  NEVER use Text2MultiHopQAGenerator for non-QA tasks.\n"
            "Compare-before-committing (scoring/eval tasks): do NOT default to the first plausible operator. "
            "Browse core_text/eval via list_operators + get_operator_detail_by_name and pick the evaluator whose "
            "built-in prompt best fits the task; a purpose-built evaluator is more robust than a generator you prompt by hand. "
            "Justify the choice in your reasoning.\n"
            "Evaluator output-schema handoff (CRITICAL — evaluators do NOT produce a single score column):\n"
            "  - Text2QASampleEvaluator writes FOUR integer grade columns: question_quality_grades, "
            "answer_alignment_grades, answer_verifiability_grades, downstream_value_grades (no `quality_score`). "
            "Its run() default input keys are `generated_question`/`generated_answer` — set input_question_key/"
            "input_answer_key to your ACTUAL columns (e.g. \"question\"/\"answer\").\n"
            "  - To filter by quality after a multi-dimension evaluator, FIRST add a PandasOperator that combines the "
            "grade columns into one numeric column, e.g. df['quality_score'] = df[['question_quality_grades',"
            "'answer_alignment_grades','answer_verifiability_grades','downstream_value_grades']].mean(axis=1), "
            "THEN GeneralFilter on that new column: [\"lambda df: df['quality_score'] >= 3\"]. "
            "A GeneralFilter lambda that references a non-existent column matches 0 rows and silently drops everything.\n"
            "  - General rule: after ANY evaluator, call get_operator_detail_by_name to learn its exact output column "
            "names, then make the filter lambda reference a column that actually exists (add a PandasOperator to "
            "derive a single score if the evaluator emits several).\n"
        )

    return (
        f"Construct a DataFlow pipeline for the task below. "
        f"The input dataset is already registered: use `input_dataset.id = \"{dataset_id}\"` verbatim "
        f"(this is the backend-generated dataset id; do NOT invent a new one, do NOT re-register). "
        f"Use the DataFlow MCP tools to browse operators and create the pipeline.\n"
        f"{skill_hint}"
        f"LLM serving policy:\n"
        f"  - First call `list_serving` (or the backward-compatible alias `list_servings`). If at least one serving instance is available, pick one and pass its id "
        f"into every LLM-based operator's `llm_serving` init param. Prefer specialized prompt-based operators "
        f"(PromptedGenerator, FormatStrPromptedGenerator, PromptedRefiner, PromptedEvaluator, GeneralFilter, ...) "
        f"over unrelated SFT-specific quality operators (e.g. Alpagasus*) when the task is not about instruction quality.\n"
        f"  - If `list_serving` returns empty, do NOT create the pipeline; instead report to the user that a "
        f"serving instance must be registered first, and stop.\n"
        f"Field/schema policy:\n"
        f"  - Before choosing a category, call `list_operator_categories`. If more than one category looks plausible, call `recommend_operator_categories` with the task description and dataset columns, and follow its top 1-2 suggestions.\n"
        f"  - Before choosing `input_key` / `input_keys`, call `get_dataset_columns` on the registered dataset id and use the returned columns as ground truth.\n"
        f"  - Do not browse more than 2 categories with `list_operators` unless the first pass clearly fails. Use `get_operator_detail_by_name` for the final shortlist instead of more category scans.\n"
        f"  - Before each create/update, call `validate_pipeline_config` on the exact config you plan to submit. If it returns `valid=false`, repair the config before committing.\n"
        f"Prompt-template policy: for prompt-based operators, supply `system_prompt` (and `user_prompt` if needed) as plain strings when possible. "
        f"If you must use `prompt_template`, only use a class-name string or a structured object containing `cls_name` (and optional `params`); do NOT fabricate an ad-hoc dict.\n"
        f"Protocol:\n{gate_text}\n\n"
        f"Task: {task.user_prompt}"
    )


# ---------- dataset registration ----------

def _register_or_get_dataset(be: DataFlowBackend, task: TaskSpec) -> str:
    """Register the task's sample JSONL as a dataset and return the backend-assigned id."""
    existing = {d.get("name"): d for d in be.list_datasets()}
    ds_name = f"expt_{task.task_id}"
    if ds_name in existing:
        return existing[ds_name].get("id") or ds_name
    try:
        resp = be.register_dataset(name=ds_name, root=str(task.sample_path), pipeline="default")
        data = resp.get("data") or {}
        ds_id = data.get("id")
        if not ds_id:
            ds_id = next((d.get("id") for d in be.list_datasets() if d.get("name") == ds_name), None)
        return ds_id or ds_name
    except Exception as e:
        logger.warning(f"register_dataset failed for {ds_name}: {e}")
        return ds_name


# ---------- direct_cc: run Claude Code CLI standalone ----------

async def _run_direct_cc(user_prompt_full: str, raw_trace_path: Path,
                         cfg: MethodConfig, timeout_sec: int) -> dict:
    """Spawn Claude Code CLI outside the WebUI, capture stream-json into raw_trace."""
    import shutil
    cli = shutil.which("claude")
    if not cli:
        raise RuntimeError("No claude CLI found in PATH")
    cwd = cfg.agent_cwd
    cwd.mkdir(parents=True, exist_ok=True)
    cmd = [
        cli, "--print", user_prompt_full,
        "--output-format", "stream-json",
        "--verbose",
        "--allowedTools", cfg.allowed_tools or "Read,Write,Edit",
        "--permission-mode", "dontAsk",
    ]
    t_first = time.time()
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=str(cwd),
        limit=10 * 1024 * 1024,  # 10 MB: direct_cc reads large repo files; default 64KB overflows
    )
    fp = raw_trace_path.open("w", encoding="utf-8")
    deadline = t_first + timeout_sec
    timed_out = False
    try:
        assert proc.stdout is not None
        while True:
            remaining = deadline - time.time()
            if remaining <= 0:
                timed_out = True
                break
            # Use an OVERALL deadline, not a per-line cap: the agent may think
            # for a while between stream-json lines (e.g. after reading a large
            # file), and that silence must not be mistaken for completion.
            try:
                line = await asyncio.wait_for(proc.stdout.readline(),
                                              timeout=remaining)
            except asyncio.TimeoutError:
                timed_out = True
                break
            if not line:
                break
            try:
                payload = json.loads(line.decode("utf-8", errors="replace"))
            except Exception:
                continue
            fp.write(json.dumps({"t": time.time(), "dir": "in", "payload": payload}) + "\n")
            fp.flush()
        if timed_out and proc.returncode is None:
            try:
                proc.kill()
            except Exception:
                pass
        await proc.wait()
    finally:
        fp.close()
    return {"t_first_msg": t_first, "t_done": time.time(), "timed_out": timed_out}


# ---------- main ----------

def run_one(task_id: str, method: str, repetition: int,
            experiment_id: str = "pilot",
            timeout_sec: int = DEFAULT_TIMEOUT_SEC) -> dict:
    """Execute one (task, method, rep) and write a run_trace file.

    Returns the summary metrics dict.
    """
    task = load_task(task_id)
    cfg = METHODS[method]

    ensure_all_shadows()

    run_key = f"{task_id}__{method}__rep{repetition}"
    out_dir = RESULTS_ROOT / experiment_id / run_key
    out_dir.mkdir(parents=True, exist_ok=True)
    raw_trace = out_dir / "raw_trace.jsonl"
    summary_path = out_dir / "run_trace.json"

    log_file = LOGS_DIR / f"{experiment_id}__{run_key}.log"
    log_file.parent.mkdir(parents=True, exist_ok=True)
    logger.add(log_file, level="INFO", enqueue=True)

    with DataFlowBackend() as be:
        # 1) register dataset
        ds_name = _register_or_get_dataset(be, task)
        logger.info(f"[{run_key}] dataset={ds_name}")

        # 2) snapshot pipelines
        before_ids = {p["id"]: p for p in be.list_pipelines()}

        # 3) build prompt
        full_prompt = _method_preamble(cfg, task, ds_name)

        # 4) drive agent
        t_first_user = time.time()
        if cfg.name == "manual":
            # skip; manual pipelines are built by humans and recorded separately
            logger.info(f"[{run_key}] manual method is recorded offline; skipping agent loop")
            result = {}
        elif cfg.name == "direct_cc":
            result = asyncio.run(_run_direct_cc(full_prompt, raw_trace, cfg, timeout_sec))
        elif cfg.name == "pure_cc":
            result = asyncio.run(_run_direct_cc(full_prompt, raw_trace, cfg, timeout_sec))
        else:
            # WS-backed methods
            result = asyncio.run(run_ws_chat(full_prompt, raw_trace, timeout_sec=timeout_sec, method=method))
        t_after_agent = time.time()

        # 4b) direct_cc: the agent wrote a standalone .py instead of calling
        # create_pipeline. We (a) extract its operator chain via the backend's
        # AST analyzer for the structure metric, and (b) RUN the .py directly
        # for exec/e2e — the backend executor rebuilds operators from a config
        # and cannot round-trip a script's Python-level params (e.g. a
        # GeneralFilter lambda), so running the script as written is the
        # faithful measure of a documentation-grounded bare agent.
        direct_cc_pyfile = None
        direct_cc_chain: list[str] = []
        direct_cc_exec: dict = {}
        if cfg.name == "direct_cc":
            direct_cc_pyfile = _extract_direct_cc_pyfile(raw_trace)
            if direct_cc_pyfile:
                direct_cc_chain = _direct_cc_chain_from_pyfile(direct_cc_pyfile)
                logger.info(
                    f"[{run_key}] direct_cc .py={direct_cc_pyfile} "
                    f"chain={direct_cc_chain}"
                )
                direct_cc_exec = _run_direct_cc_pyfile(direct_cc_pyfile, out_dir, timeout_sec)
                logger.info(
                    f"[{run_key}] direct_cc exec status={direct_cc_exec.get('status')} "
                    f"out={direct_cc_exec.get('final_output_path')}"
                )
            else:
                logger.warning(f"[{run_key}] direct_cc produced no /tmp pipeline .py")

        # 4c) pure_cc: the agent wrote an arbitrary Python script that already
        # ran and produced a result JSONL. There is no operator chain (no
        # framework), so structure is N/A; we only locate its output for e2e.
        pure_cc_output = None
        if cfg.name == "pure_cc":
            pure_cc_output = _extract_pure_cc_output(raw_trace)
            logger.info(f"[{run_key}] pure_cc output={pure_cc_output}")

        # 5) find newest pipeline created
        final_pipeline = None
        t_first_pipeline_created = None
        if cfg.name == "pure_cc":
            # No DataFlow pipeline / operator chain exists. Structure tier is N/A.
            t_first_pipeline_created = t_after_agent if pure_cc_output else None
        elif cfg.name == "direct_cc":
            # Synthesize a pseudo-pipeline so the metrics layer can compute the
            # structure tier from the AST-extracted chain. exec/e2e come from
            # the direct script run below.
            if direct_cc_chain:
                final_pipeline = {
                    "id": "direct_cc_local",
                    "config": {"operators": [{"name": n, "params": {}} for n in direct_cc_chain]},
                }
                t_first_pipeline_created = t_after_agent
        else:
            after = {p["id"]: p for p in be.list_pipelines()}
            new_ids = [pid for pid in after if pid not in before_ids]
            if new_ids:
                # pick newest
                pid = new_ids[-1]
                final_pipeline = be.get_pipeline(pid)
                # t_first_pipeline_created: time of first create_pipeline tool call (or fallback to wall time after agent)
                from .trace_parser import parse_raw_trace as _prp
                _tmp = _prp(raw_trace) if raw_trace.exists() else None
                if _tmp is not None:
                    for tc in _tmp.tool_calls:
                        if "create_pipeline" in tc.name and tc.t_start:
                            t_first_pipeline_created = tc.t_start
                            break
                if t_first_pipeline_created is None:
                    t_first_pipeline_created = t_after_agent
            logger.info(f"[{run_key}] new_pipelines={new_ids}")

        # 6) execute
        execution_result: dict = {}
        t_first_successful_run = None
        if cfg.name == "pure_cc":
            # The script ran itself during the agent turn; we just point e2e at
            # the JSONL it produced. If no output file, it failed to complete.
            if pure_cc_output:
                execution_result = {"status": "completed", "final_output_path": pure_cc_output}
                t_first_successful_run = t_after_agent
            else:
                execution_result = {"status": "error", "error": "no output JSONL produced"}
        elif cfg.name == "direct_cc":
            # direct_cc already ran its own script in step 4b.
            execution_result = direct_cc_exec
            if execution_result.get("status") == "completed":
                t_first_successful_run = t_after_agent
        elif final_pipeline and cfg.name != "manual":
            pid = final_pipeline["id"]
            # Skip execute if no serving is registered — this mirrors the UX: agent should have stopped itself
            servings = be.list_serving()
            has_llm_op = any(
                op.get("name", "").startswith(("Prompted", "FormatStr", "LLM", "Text2", "QuestionAnswerAligner"))
                for op in (final_pipeline.get("config") or {}).get("operators", [])
            )
            if has_llm_op and not servings:
                execution_result["status"] = "skipped_no_serving"
                logger.info(f"[{run_key}] skipping execute: LLM ops present but no serving registered")
            else:
                try:
                    exec_resp = be.execute_pipeline(pid, asynchronous=False)
                    task_exec_id = exec_resp.get("task_id") or exec_resp.get("id")
                    if task_exec_id:
                        final_status = be.wait_for_task(task_exec_id)
                        execution_result["status"] = final_status.get("status") or final_status.get("state")
                        # Download the LAST completed step. Passing step=None lets
                        # the backend pick the final completed step; guessing from
                        # n_ops overshoots when fewer steps completed and returns a
                        # 400 "Invalid step index" body that pollutes the output.
                        out = out_dir / "final_output.jsonl"
                        got = False
                        try:
                            be.download_result(task_exec_id, None, out)
                            if out.exists() and out.stat().st_size > 0:
                                execution_result["final_output_path"] = str(out)
                                got = True
                        except Exception:
                            pass
                        if not got:
                            # fallback: walk known completed step indices high→low
                            steps = be.list_execution_steps(task_exec_id)
                            idxs = sorted((s.get("index", i) for i, s in enumerate(steps)), reverse=True)
                            for step in idxs:
                                try:
                                    out = out_dir / f"step_{step}.jsonl"
                                    be.download_result(task_exec_id, step, out)
                                    if out.exists() and out.stat().st_size > 0:
                                        execution_result["final_output_path"] = str(out)
                                        break
                                except Exception:
                                    continue
                        if execution_result["status"] in ("completed", "success", "succeeded"):
                            t_first_successful_run = time.time()
                        execution_result["task_exec_id"] = task_exec_id
                except Exception as e:
                    logger.warning(f"[{run_key}] execute failed: {e}")
                    execution_result["status"] = "error"
                    execution_result["error"] = str(e)

        # 7) parse & summarize
        parsed = parse_raw_trace(raw_trace) if raw_trace.exists() else None
        if parsed is None:
            from .trace_parser import ParsedTrace
            parsed = ParsedTrace()

        summary = summarize_run(
            task=task,
            parsed=parsed,
            final_pipeline=(final_pipeline or {}).get("config"),
            execution_result=execution_result,
            t_first_user_msg=t_first_user,
            t_first_pipeline_created=t_first_pipeline_created,
            t_first_successful_run=t_first_successful_run,
            method=method,
            repetition=repetition,
        )
        summary["experiment_id"] = experiment_id
        if cfg.name == "direct_cc":
            summary["new_pipeline_ids"] = []
            summary["direct_cc_pyfile"] = direct_cc_pyfile
            summary["direct_cc_exec_status"] = direct_cc_exec.get("status")
        elif cfg.name == "pure_cc":
            summary["new_pipeline_ids"] = []
            summary["pure_cc_output"] = pure_cc_output
        else:
            summary["new_pipeline_ids"] = new_ids
        summary["t_agent_wall_sec"] = t_after_agent - t_first_user
        summary["raw_trace_path"] = str(raw_trace)

        summary_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False))
        logger.info(f"[{run_key}] summary -> {summary_path}")
        return summary
