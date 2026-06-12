"""Core metric computations shared across E1 / E2 / E4.

Inputs:
  - task: TaskSpec (expected_chain, expected_fields_produced, acceptance)
  - parsed: ParsedTrace from trace_parser
  - final_pipeline: dict pulled via REST after the run (stored pipeline object)
  - execution_result: dict with status, logs, output jsonl paths

Outputs a single flat metrics dict per run.
"""
from __future__ import annotations
from typing import Any
from .trace_parser import ParsedTrace
from .config import TaskSpec


KNOWN_OPERATORS: set[str] | None = None


def _load_known_operators() -> set[str]:
    """Load the authoritative operator name set from the backend registry."""
    global KNOWN_OPERATORS
    if KNOWN_OPERATORS is not None:
        return KNOWN_OPERATORS
    try:
        from .backend_client import _ensure_backend_context

        ctx = _ensure_backend_context()
        op_list = ctx["container"].operator_registry.get_op_list(lang="zh")
        names = set()
        if isinstance(op_list, list):
            for item in op_list:
                if isinstance(item, dict) and "name" in item:
                    names.add(item["name"])
                elif isinstance(item, str):
                    names.add(item)
        KNOWN_OPERATORS = names
    except Exception:
        KNOWN_OPERATORS = set()
    return KNOWN_OPERATORS


def extract_final_chain(parsed: ParsedTrace,
                        final_pipeline: dict | None) -> list[str]:
    """Return the ordered list of operator class names the agent ultimately produced.

    Priority: stored pipeline object > last create/update_pipeline call args.
    """
    if final_pipeline:
        ops = final_pipeline.get("operators") or final_pipeline.get("pipeline", {}).get("operators")
        if ops:
            return [op.get("name", "?") for op in ops]
    # fallback: latest create/update_pipeline call args
    for tc in reversed(parsed.tool_calls):
        if "create_pipeline" in tc.name or "update_pipeline" in tc.name:
            args = tc.args or {}
            ops = (args.get("operators")
                   or args.get("pipeline", {}).get("operators")
                   or [])
            if ops:
                return [op.get("name", "?") for op in ops]
    return []


def compare_operator_chains(
    final_chain: list[str],
    expected: list[dict[str, Any]],
) -> dict[str, Any]:
    """Compare produced operator chain against expected pattern.

    expected format: list of {"op": str, "required": bool, "alt": [alt names]?,
                              "slot": str?, "slot_ops": [op names]?}
    - `slot` names a semantic role (e.g. "score", "filter", "refine")
    - `slot_ops` is the full set of operators that semantically satisfy the slot
    We compute two scores:
      - strict_match: exact op name (or in `alt`)
      - semantic_match: op name in `slot_ops` (superset of alt)
    Returns counts for both. Paper reports semantic_match as the headline metric.
    """
    known = _load_known_operators()
    final = list(final_chain)
    required = [e for e in expected if e.get("required", True)]

    def strict_matches(candidate: str, exp: dict) -> bool:
        if candidate == exp["op"]:
            return True
        return candidate in (exp.get("alt") or [])

    def semantic_matches(candidate: str, exp: dict) -> bool:
        if strict_matches(candidate, exp):
            return True
        return candidate in (exp.get("slot_ops") or [])

    hallucinated = [op for op in final if known and op not in known]

    # Strict pass
    idx = 0
    strict_matched: list[str] = []
    for op in final:
        if idx < len(required) and strict_matches(op, required[idx]):
            strict_matched.append(op)
            idx += 1
    strict_missing = len(required) - len(strict_matched)
    strict_wrong_order = 0
    if strict_missing > 0:
        if all(any(strict_matches(op, e) for op in final) for e in required):
            strict_wrong_order = strict_missing
            strict_missing = 0

    # Semantic pass
    idx = 0
    sem_matched: list[str] = []
    for op in final:
        if idx < len(required) and semantic_matches(op, required[idx]):
            sem_matched.append(op)
            idx += 1
    sem_missing = len(required) - len(sem_matched)
    sem_wrong_order = 0
    if sem_missing > 0:
        if all(any(semantic_matches(op, e) for op in final) for e in required):
            sem_wrong_order = sem_missing
            sem_missing = 0

    expected_names = (set(e["op"] for e in expected)
                      | set(a for e in expected for a in (e.get("alt") or []))
                      | set(a for e in expected for a in (e.get("slot_ops") or [])))
    # Deterministic "glue" operators are legitimate plumbing the agent may add
    # around the semantic chain (e.g. PandasOperator to explode a nested list,
    # average multi-dimension grades into one score, or rename a column so a
    # downstream filter lambda can reference it). They carry no LLM semantics and
    # do not change operator-selection correctness, so they never count as
    # "unexpected". This keeps the metric aligned with the skill, which now
    # recommends PandasOperator as glue for scoring/eval pipelines.
    GLUE_OPS = {"PandasOperator"}
    unexpected = [op for op in final
                  if op not in expected_names
                  and op not in hallucinated
                  and op not in GLUE_OPS]

    required_slots = [e.get("slot", e["op"]) for e in required]

    return {
        "hallucinated": len(hallucinated),
        "hallucinated_names": hallucinated,
        # strict
        "strict_missing": strict_missing,
        "strict_wrong_order": strict_wrong_order,
        # semantic (headline)
        "missing_required": sem_missing,
        "wrong_order": sem_wrong_order,
        "unexpected": len(unexpected),
        "unexpected_names": unexpected,
        "final_chain": final,
        "expected_required": [e["op"] for e in required],
        "expected_slots": required_slots,
    }


def count_field_flow_errors(
    final_pipeline: dict | None,
    expected_fields: list[str],
) -> int:
    """Static check: does the pipeline consume a field before it's produced?

    We walk operators in order; each op's input_key / input_keys must either
    exist in the original sample fields or be output by an earlier op.
    """
    if not final_pipeline:
        return 0
    ops = (final_pipeline.get("operators")
           or final_pipeline.get("pipeline", {}).get("operators")
           or [])
    produced: set[str] = set(expected_fields)  # permissive: assume sample has all
    errors = 0
    for op in ops:
        params = op.get("params") or op.get("run_params") or {}
        inputs: list[str] = []
        for k, v in params.items():
            if k.startswith("input_key") and isinstance(v, str):
                inputs.append(v)
            elif k.startswith("input_keys") and isinstance(v, list):
                inputs.extend([x for x in v if isinstance(x, str)])
        for f in inputs:
            if f not in produced:
                errors += 1
        for k, v in params.items():
            if k.startswith("output_key") and isinstance(v, str):
                produced.add(v)
            elif k.startswith("output_keys") and isinstance(v, list):
                produced.update(x for x in v if isinstance(x, str))
    return errors


def check_acceptance(output_jsonl_path: str | None,
                     acceptance: dict) -> dict:
    """Load the final JSONL and decide acceptance under one of two modes.

    Schema keys may be a plain field name or a pipe-delimited synonym set
    ``"qa_pairs|multi_hop_qa|QA_pairs"`` — at least one synonym must appear and
    pass the type check. This frees the agent to pick its own ``output_key``.

    Two modes (``acceptance["mode"]``):

    - ``"generate"`` (default): every input row should yield an output row, so
      we require at least ``min_valid_records`` rows that satisfy the schema.

    - ``"filter"``: the task's job is to drop rows, so the surviving row count
      depends on the model's scores, the data, and the user's threshold — none
      of which are the agent's responsibility. We therefore do NOT impose a
      minimum row count. e2e passes if the pipeline ran to completion AND any
      retained rows are well-formed (carry the required fields). An empty
      output (everything legitimately filtered out) is a PASS. Whether the
      agent actually built the scoring/filter step is already enforced by the
      structure tier.
    """
    import json, os
    mode = acceptance.get("mode", "generate")
    result = {"passed": False, "n_valid": 0, "n_total": 0, "reason": None, "mode": mode}
    if output_jsonl_path is None or not os.path.exists(output_jsonl_path):
        result["reason"] = "missing_output"
        return result
    schema = acceptance.get("schema", {})
    min_valid = acceptance.get("min_valid_records", 1)
    n_valid = 0
    n_total = 0
    with open(output_jsonl_path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            n_total += 1
            try:
                row = json.loads(line)
            except Exception:
                continue
            ok = True
            for k, typ in schema.items():
                candidates = [c.strip() for c in str(k).split("|") if c.strip()]
                hit = None
                for cand in candidates:
                    if cand in row:
                        hit = cand
                        break
                if hit is None:
                    ok = False
                    break
                if typ and not _type_ok(row[hit], typ):
                    ok = False
                    break
            if ok:
                n_valid += 1
    result["n_total"] = n_total
    result["n_valid"] = n_valid

    if mode == "filter":
        # Ran to completion (output file present & parseable). Surviving rows,
        # if any, must be well-formed; 0 rows is acceptable.
        if n_total == 0:
            result["passed"] = True
            result["reason"] = "filtered_to_empty_ok"
        else:
            result["passed"] = (n_valid == n_total)
            if not result["passed"]:
                result["reason"] = f"{n_total - n_valid}/{n_total} retained rows malformed"
    else:  # generate
        result["passed"] = n_valid >= min_valid
        if not result["passed"]:
            result["reason"] = f"only {n_valid}/{n_total} valid, need >= {min_valid}"

    # Anti-gaming value checks (applied to ALL methods, but most important for
    # pure_cc which has no structure tier to catch "ran but did the wrong
    # thing"). Each check is a dict; see _apply_value_checks. A failing check
    # overrides passed=False with a specific reason.
    if result["passed"]:
        vc_ok, vc_reason = _apply_value_checks(output_jsonl_path, acceptance.get("value_checks", []))
        if not vc_ok:
            result["passed"] = False
            result["reason"] = vc_reason
    return result


def _apply_value_checks(output_jsonl_path: str, checks: list) -> tuple[bool, str | None]:
    """Verify retained rows actually did the task (anti-gaming).

    Supported check kinds (each a dict):
      {"kind": "in_set", "field": "<f|syn>", "values": [...]}       — every row's field value in the allowed set
      {"kind": "range", "field": "<f|syn>", "min": x, "max": y}     — every row's numeric field in [x,y]
      {"kind": "not_constant", "field": "<f|syn>"}                  — field takes >1 distinct value across rows (catches all-same gaming)
      {"kind": "predicate", "field": "<f|syn>", "op": ">=|>|<=|<|==", "value": x}  — every row satisfies field OP value (e.g. filter condition met)
    Field may be a pipe-synonym set. Empty output trivially passes (handled upstream).
    """
    import json, os
    if not checks or not output_jsonl_path or not os.path.exists(output_jsonl_path):
        return True, None
    rows = []
    with open(output_jsonl_path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except Exception:
                pass
    if not rows:
        return True, None

    def resolve(row, field):
        for cand in [c.strip() for c in str(field).split("|")]:
            if cand in row:
                return row[cand]
        return None

    for chk in checks:
        kind = chk.get("kind")
        field = chk.get("field", "")
        vals = [resolve(r, field) for r in rows]
        present = [v for v in vals if v is not None]
        if not present:
            return False, f"value_check {kind}: field '{field}' absent in all rows"
        if kind == "in_set":
            allowed = set(str(x).lower() for x in chk.get("values", []))
            bad = [v for v in present if str(v).strip().lower() not in allowed]
            if bad:
                return False, f"value_check in_set: {len(bad)} rows have '{field}' outside {sorted(allowed)} (e.g. {bad[0]!r})"
        elif kind == "range":
            lo, hi = chk.get("min"), chk.get("max")
            try:
                nums = [float(v) for v in present]
            except Exception:
                return False, f"value_check range: '{field}' not numeric"
            bad = [n for n in nums if (lo is not None and n < lo) or (hi is not None and n > hi)]
            if bad:
                return False, f"value_check range: {len(bad)} rows have '{field}' outside [{lo},{hi}] (e.g. {bad[0]})"
        elif kind == "not_constant":
            if len(set(str(v) for v in present)) <= 1:
                return False, f"value_check not_constant: '{field}' is constant ({present[0]!r}) — likely not genuinely computed"
        elif kind == "predicate":
            op, val = chk.get("op"), chk.get("value")
            try:
                nums = [float(v) for v in present]
            except Exception:
                return False, f"value_check predicate: '{field}' not numeric"
            import operator as _op
            opf = {">=": _op.ge, ">": _op.gt, "<=": _op.le, "<": _op.lt, "==": _op.eq}[op]
            bad = [n for n in nums if not opf(n, val)]
            if bad:
                return False, f"value_check predicate: {len(bad)} rows fail '{field} {op} {val}' (e.g. {bad[0]})"
    return True, None



def _type_ok(v, typ: str) -> bool:
    if typ == "str":
        return isinstance(v, str)
    if typ == "int":
        return isinstance(v, int) and not isinstance(v, bool)
    if typ == "float":
        return isinstance(v, (int, float)) and not isinstance(v, bool)
    if typ == "bool":
        return isinstance(v, bool)
    if typ == "list":
        return isinstance(v, list)
    if typ == "dict":
        return isinstance(v, dict)
    if typ == "any":
        return True
    return True


def summarize_run(
    task: TaskSpec,
    parsed: ParsedTrace,
    final_pipeline: dict | None,
    execution_result: dict | None,
    t_first_user_msg: float | None,
    t_first_pipeline_created: float | None,
    t_first_successful_run: float | None,
    method: str,
    repetition: int,
) -> dict:
    final_chain = extract_final_chain(parsed, final_pipeline)
    chain_cmp = compare_operator_chains(final_chain, task.expected_chain)
    field_errors = count_field_flow_errors(final_pipeline, task.expected_fields_produced)
    accept = {}
    output_path = None
    if execution_result:
        output_path = execution_result.get("final_output_path")
        accept = check_acceptance(output_path, task.acceptance)

    # Two separate time metrics:
    #   time_to_pipeline_min: first user msg -> agent committed a pipeline (independent of exec)
    #   time_min: first user msg -> first successful Run (paper headline metric)
    time_to_pipeline_min = None
    if t_first_user_msg and t_first_pipeline_created:
        time_to_pipeline_min = (t_first_pipeline_created - t_first_user_msg) / 60.0

    time_min = None
    if t_first_user_msg and t_first_successful_run:
        time_min = (t_first_successful_run - t_first_user_msg) / 60.0

    # --- success tiers ---
    exec_status = (execution_result or {}).get("status")
    # Tier 1: pipeline structure correct (no missing required ops, no hallucinated ops)
    structure_ok = (chain_cmp["missing_required"] == 0 and chain_cmp["hallucinated"] == 0)
    # Tier 2: execution completed without error
    exec_ok = exec_status in ("completed", "success", "succeeded")
    # Tier 3: end-to-end output passes acceptance (affected by LLM quality)
    e2e_ok = bool(accept.get("passed"))

    return {
        "task_id": task.task_id,
        "method": method,
        "repetition": repetition,
        "time_min": time_min,
        "time_to_pipeline_min": time_to_pipeline_min,
        "exec_status": exec_status,
        # Tiered success metrics
        "structure_pass": structure_ok,
        "exec_pass": structure_ok and exec_ok,
        "e2e_pass": e2e_ok,
        # Legacy (kept for backward compat)
        "exec_success": e2e_ok,
        "n_valid_records": accept.get("n_valid", 0),
        "n_total_records": accept.get("n_total", 0),
        # semantic-slot error counts (headline)
        "operator_errors": chain_cmp["hallucinated"] + chain_cmp["missing_required"] + chain_cmp["wrong_order"] + chain_cmp["unexpected"],
        "hallucinated": chain_cmp["hallucinated"],
        "missing_required": chain_cmp["missing_required"],
        "wrong_order": chain_cmp["wrong_order"],
        "unexpected": chain_cmp["unexpected"],
        # strict chain match (skill compliance)
        "strict_missing": chain_cmp.get("strict_missing", 0),
        "strict_wrong_order": chain_cmp.get("strict_wrong_order", 0),
        "strict_errors": chain_cmp["hallucinated"] + chain_cmp.get("strict_missing", 0) + chain_cmp.get("strict_wrong_order", 0),
        "field_errors": field_errors,
        # lesson-specific signals
        "n_create_pipeline_calls": parsed.n_create_pipeline_calls,
        "n_render_pipeline_calls": parsed.n_render_pipeline_calls,
        "n_execute_pipeline_calls_by_agent": parsed.n_execute_pipeline_calls,
        # raw chain for debugging
        "final_chain": final_chain,
        "expected_required": chain_cmp["expected_required"],
        "expected_slots": chain_cmp.get("expected_slots", []),
        "hallucinated_names": chain_cmp["hallucinated_names"],
        "error": parsed.error,
    }
