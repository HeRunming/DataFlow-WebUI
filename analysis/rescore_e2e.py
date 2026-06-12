#!/usr/bin/env python3
"""Re-score e2e for existing runs using the (tightened) task acceptance, WITHOUT
re-running any agent. Finds each run's output JSONL and recomputes check_acceptance.

Output locations by experiment type:
  - WS methods (E5_full, E6_ablation, E6_rep_extend): step_<N>.jsonl in the run dir
  - direct_cc (E6_dcc_full): dcc_exec/**/*.jsonl (may be in hidden .cache dir)

Updates each run_trace.json in place with recomputed:
  acceptance_passed, n_valid_records, n_total_records, exec_success (=e2e)
and adds rescore_output_path for auditing.
"""
import json
import os
import sys
import glob
import re

sys.path.insert(0, "/data/workspace/emnlp_experiments")
from runner.config import load_task
from runner.metrics import check_acceptance

RESULTS = "/data/workspace/emnlp_experiments/results"
EXPERIMENTS = ["E8_main", "E6_dcc_full"]


def find_output(run_dir: str) -> str | None:
    """Locate the final-step output JSONL for a run."""
    # direct_cc: under dcc_exec/, possibly hidden dirs
    dcc = os.path.join(run_dir, "dcc_exec")
    if os.path.isdir(dcc):
        cands = []
        for root, _d, files in os.walk(dcc):
            for fn in files:
                if fn.endswith(".jsonl"):
                    cands.append(os.path.join(root, fn))
        cands = [c for c in cands if "step" in os.path.basename(c)] or cands
        if cands:
            def sn(p):
                m = re.findall(r"step(\d+)", os.path.basename(p))
                return (int(m[-1]) if m else -1, os.path.getmtime(p))
            cands.sort(key=sn)
            return cands[-1]
        return None
    # WS methods: the fixed runner writes final_output.jsonl (last completed
    # step). Older runs used step_<N>.jsonl. Prefer final_output.jsonl.
    fo = os.path.join(run_dir, "final_output.jsonl")
    if os.path.exists(fo) and os.path.getsize(fo) > 0:
        return fo
    steps = glob.glob(os.path.join(run_dir, "step_*.jsonl"))
    if steps:
        steps.sort(key=lambda p: int(re.findall(r"step_(\d+)", p)[0]))
        return steps[-1]
    return None


def main(write: bool):
    by_exp = {}
    for exp in EXPERIMENTS:
        rows = []
        for rt in sorted(glob.glob(os.path.join(RESULTS, exp, "*", "run_trace.json"))):
            run_dir = os.path.dirname(rt)
            d = json.load(open(rt))
            task_id = d.get("task_id")
            try:
                task = load_task(task_id)
            except Exception:
                continue
            out = find_output(run_dir)
            acc = check_acceptance(out, task.acceptance) if out else {"passed": False, "n_valid": 0, "n_total": 0, "reason": "no_output"}
            old_e2e = bool(d.get("exec_success"))
            new_e2e = bool(acc.get("passed"))
            d["exec_success"] = new_e2e
            d["n_valid_records"] = acc.get("n_valid", 0)
            d["n_total_records"] = acc.get("n_total", 0)
            d["rescore_output_path"] = out
            d["rescore_reason"] = acc.get("reason")
            if write:
                json.dump(d, open(rt, "w"), ensure_ascii=False, indent=2)
            rows.append((task_id, d.get("method"), old_e2e, new_e2e, acc.get("n_valid", 0)))
        by_exp[exp] = rows

    # report changes
    print(f"{'experiment':<16}{'flipped':>10}{'total':>8}")
    for exp, rows in by_exp.items():
        flipped = sum(1 for *_x, o, n, _v in [(r[0], r[1], r[2], r[3], r[4]) for r in rows] if o != n)
        print(f"{exp:<16}{flipped:>10}{len(rows):>8}")
    print(f"\n{'(write=' + str(write) + ')'}")


if __name__ == "__main__":
    write = "--write" in sys.argv
    main(write)
