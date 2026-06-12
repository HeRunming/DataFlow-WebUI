#!/usr/bin/env python3
"""Unified aggregation across E5_full + E6_* experiments.

Computes the three tiers (structure/exec/e2e) with ONE consistent definition
from the run_trace.json files (works whether or not the trace stored the new
tiered fields), pools repetitions across experiments per (method, task), and
reports bootstrap 95% CIs for the headline rates.

Tier definitions (must match metrics.py):
  structure_pass = (missing_required == 0 and hallucinated == 0)
  exec_pass      = structure_pass and exec_status in {completed,success,succeeded}
  e2e_pass       = exec_success  (legacy field == acceptance.passed)
"""
import json
import glob
import random
from collections import defaultdict
from pathlib import Path

RESULTS = Path("/data/workspace/emnlp_experiments/results")

# experiment dirs that are mutually comparable (current backend + fixed runner + improved skill)
# E9_main supersedes E8_main: same clean config (download bug fixed, improved
# skill_hint) PLUS token/cost capture (backend adapter now emits a `usage` event).
# direct_cc comes from E6_dcc_full (unaffected by the download bug).
COMPARABLE_EXPERIMENTS = [
    "E9_main",        # df_agent_v3, mcp_only  (8 reps, clean, +token capture)
    "E6_dcc_full",    # direct_cc
]
# Ablation (E6_ablation) is held out pending gate-validity verification (no_mcp invalid).

# normalize method names for display
METHOD_DISPLAY = {
    "df_agent_v3": "DataFlow-Harness",
    "dataflow_agent": "DataFlow-Harness",
    "mcp_only": "MCP-only",
    "direct_cc": "Direct CC",
    "no_mcp": "-MCP (skills only)",
    "no_skills": "-Skills (==MCP-only)",
    "no_dag_sync": "-DAG sync",
    "no_exec_gating": "-Exec gating",
}

EXEC_OK = {"completed", "success", "succeeded"}


def tiers(r: dict) -> tuple[bool, bool, bool]:
    """Strictly nested tiers: E2E ⊆ Exec ⊆ Struct.

    - structure: operator chain matches the task's required slots with no
      hallucinated names.
    - exec_pass: structure AND the pipeline ran to completion without error.
    - e2e_pass: exec_pass AND the final output satisfies the task acceptance.

    Nesting is enforced so each tier is a subset of the one above; a run that
    "ran and produced acceptable output" but built the wrong chain is NOT an
    e2e pass — its construction failure is surfaced at the structure tier.

    pure_cc is special: it writes arbitrary Python with no DataFlow operators,
    so there is no operator chain to score. Structure is N/A (reported as None);
    exec_pass = ran to completion; e2e_pass = exec_pass AND output accepted.
    """
    if r.get("method") == "pure_cc":
        exec_ok = r.get("exec_status") in EXEC_OK
        e2e = exec_ok and bool(r.get("exec_success"))
        return (None, exec_ok, e2e)
    structure = (r.get("missing_required", 1) == 0 and r.get("hallucinated", 1) == 0)
    exec_ok = r.get("exec_status") in EXEC_OK
    exec_pass = structure and exec_ok
    e2e = exec_pass and bool(r.get("exec_success"))
    return structure, exec_pass, e2e


def load_runs(experiments=COMPARABLE_EXPERIMENTS) -> list[dict]:
    rows = []
    for exp in experiments:
        # prefer per-run run_trace.json (authoritative), fall back to summary.json
        run_traces = glob.glob(str(RESULTS / exp / "*" / "run_trace.json"))
        if run_traces:
            for rt in run_traces:
                try:
                    r = json.load(open(rt))
                    r["_exp"] = exp
                    rows.append(r)
                except Exception:
                    pass
        else:
            sj = RESULTS / exp / "summary.json"
            if sj.exists():
                for r in json.load(open(sj)):
                    r["_exp"] = exp
                    rows.append(r)
    return rows


def bootstrap_ci(bools: list[bool], n_boot=10000, alpha=0.05):
    if not bools:
        return (None, None, None)
    pt = sum(bools) / len(bools)
    boots = []
    n = len(bools)
    for _ in range(n_boot):
        s = sum(random.choice(bools) for _ in range(n))
        boots.append(s / n)
    boots.sort()
    lo = boots[int((alpha / 2) * n_boot)]
    hi = boots[int((1 - alpha / 2) * n_boot)]
    return (pt, lo, hi)


def main():
    random.seed(42)
    rows = load_runs()
    by_method = defaultdict(list)
    for r in rows:
        by_method[r["method"]].append(r)

    print(f"Loaded {len(rows)} runs across {len(by_method)} methods\n")
    print(f"{'method':<22}{'n':>4}  {'Struct (95% CI)':<26}{'Exec (95% CI)':<26}{'E2E (95% CI)':<26}{'OpErr':>7}")
    print("-" * 112)

    order = ["pure_cc", "direct_cc", "mcp_only", "no_mcp", "no_dag_sync", "no_exec_gating",
             "df_agent_v3", "dataflow_agent"]
    seen = set()
    for m in order + list(by_method.keys()):
        if m in seen or m not in by_method:
            continue
        seen.add(m)
        mr = by_method[m]
        st = [tiers(r)[0] for r in mr if tiers(r)[0] is not None]
        ex = [tiers(r)[1] for r in mr]
        e2 = [tiers(r)[2] for r in mr]
        operr = sum(r.get("operator_errors", 0) for r in mr) / len(mr)
        e_pt, e_lo, e_hi = bootstrap_ci(ex)
        z_pt, z_lo, z_hi = bootstrap_ci(e2)
        disp = METHOD_DISPLAY.get(m, m)
        if st:
            s_pt, s_lo, s_hi = bootstrap_ci(st)
            struct_str = f'{s_pt:.1%} [{s_lo:.1%},{s_hi:.1%}]'
            operr_str = f'{operr:>7.2f}'
        else:
            struct_str = 'N/A'          # pure_cc has no operator chain
            operr_str = f'{"N/A":>7}'
        print(f"{disp:<22}{len(mr):>4}  "
              f"{struct_str:<26}"
              f"{f'{e_pt:.1%} [{e_lo:.1%},{e_hi:.1%}]':<26}"
              f"{f'{z_pt:.1%} [{z_lo:.1%},{z_hi:.1%}]':<26}"
              f"{operr_str}")

    # per-task e2e for the three headline methods
    print("\n\nPer-task E2E pass (pooled reps):")
    methods_pt = ["direct_cc", "mcp_only", "df_agent_v3"]
    tasks = sorted(set(r["task_id"] for r in rows))
    hdr = f"{'task':<28}" + "".join(f"{METHOD_DISPLAY.get(m,m)[:12]:>14}" for m in methods_pt)
    print(hdr)
    for t in tasks:
        line = f"{t:<28}"
        for m in methods_pt:
            tr = [r for r in rows if r["task_id"] == t and r["method"] == m]
            p = sum(tiers(r)[2] for r in tr)
            line += f"{f'{p}/{len(tr)}':>14}"
        print(line)


if __name__ == "__main__":
    main()
