#!/usr/bin/env python3
"""Recompute chain-comparison metrics (operator_errors, unexpected, missing,
structure) for existing runs from their stored final_chain — without re-running
agents. Needed after changing compare_operator_chains (e.g. GLUE_OPS exemption)
or task slot_ops. Updates run_trace.json in place.
"""
import json, glob, os, sys
sys.path.insert(0, "/data/workspace/emnlp_experiments")
from runner.config import load_task
from runner.metrics import compare_operator_chains

RESULTS = "/data/workspace/emnlp_experiments/results"
EXPERIMENTS = sys.argv[1:] or ["E8_main", "E6_dcc_full"]


def main():
    changed = 0
    total = 0
    for exp in EXPERIMENTS:
        for rt in glob.glob(os.path.join(RESULTS, exp, "*", "run_trace.json")):
            d = json.load(open(rt))
            total += 1
            chain = d.get("final_chain") or []
            try:
                task = load_task(d["task_id"])
            except Exception:
                continue
            cmp = compare_operator_chains(chain, task.expected_chain)
            old_oe = d.get("operator_errors")
            new_oe = (cmp["hallucinated"] + cmp["missing_required"]
                      + cmp["wrong_order"] + cmp["unexpected"])
            d["hallucinated"] = cmp["hallucinated"]
            d["missing_required"] = cmp["missing_required"]
            d["wrong_order"] = cmp["wrong_order"]
            d["unexpected"] = cmp["unexpected"]
            d["operator_errors"] = new_oe
            d["strict_missing"] = cmp.get("strict_missing", 0)
            d["strict_wrong_order"] = cmp.get("strict_wrong_order", 0)
            json.dump(d, open(rt, "w"), ensure_ascii=False, indent=2)
            if old_oe != new_oe:
                changed += 1
    print(f"recomputed chain metrics for {total} runs ({changed} changed)")


if __name__ == "__main__":
    main()
