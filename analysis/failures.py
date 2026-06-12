"""Classify each run into failure categories matching the paper's four types.

Categories (matching the paper §1):
  - operator_hallucination: final_chain contains operator(s) not in registry
  - wrong_operator_choice: chain maps to none/few of the expected slot_ops
  - invalid_field_flow: field_errors > 0
  - operational_omission: LLM op with no serving, or hardcoded creds detected
  - ui_disconnect: no pipeline object created (direct_cc)
  - context_overflow: agent ran out of context
  - execution_error: pipeline created but execution failed for runtime reason
  - success: exec_success True AND operator_errors (semantic) == 0

A run can be labeled with multiple categories; we pick a primary failure mode
priority order below.
"""
from __future__ import annotations
import json
import sys
from pathlib import Path
from collections import Counter, defaultdict

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from runner.config import RESULTS_ROOT


PRIMARY_ORDER = [
    "context_overflow",
    "ui_disconnect",
    "operator_hallucination",
    "wrong_operator_choice",
    "invalid_field_flow",
    "operational_omission",
    "execution_error",
    "partial_success",
    "success",
]


def classify_run(r: dict) -> list[str]:
    labels = []
    if r.get("error") and "上下文" in (r.get("error") or ""):
        labels.append("context_overflow")
    if not r.get("final_chain"):
        labels.append("ui_disconnect")
    if (r.get("hallucinated") or 0) > 0:
        labels.append("operator_hallucination")
    # wrong_operator_choice: semantic slot miss OR too many unexpected
    if (r.get("missing_required") or 0) > 0 or (r.get("unexpected") or 0) >= 2:
        labels.append("wrong_operator_choice")
    if (r.get("field_errors") or 0) > 0:
        labels.append("invalid_field_flow")
    # operational_omission: pipeline created but exec_status is failed with known causes
    exec_status = r.get("exec_status")
    if exec_status == "skipped_no_serving":
        labels.append("operational_omission")
    if exec_status == "failed" and (r.get("n_valid_records") or 0) < 1:
        if "operational_omission" not in labels:
            labels.append("execution_error")
    if r.get("exec_success"):
        # still count semantic errors separately
        if (r.get("operator_errors") or 0) == 0:
            labels.append("success")
        else:
            labels.append("partial_success")
    return labels


def primary_label(labels: list[str]) -> str:
    for p in PRIMARY_ORDER:
        if p in labels:
            return p
    return "unclassified"


def main(experiment_id: str = "E1") -> None:
    root = RESULTS_ROOT / experiment_id
    all_labels = []
    by_method_primary = defaultdict(Counter)
    by_task_primary = defaultdict(Counter)
    details = []
    for sub in sorted(root.iterdir()):
        rt = sub / "run_trace.json"
        if not rt.exists():
            continue
        try:
            r = json.loads(rt.read_text())
        except Exception:
            continue
        labels = classify_run(r)
        prim = primary_label(labels)
        all_labels.extend(labels)
        by_method_primary[r["method"]][prim] += 1
        by_task_primary[r["task_id"]][prim] += 1
        details.append({
            "task_id": r.get("task_id"),
            "method": r.get("method"),
            "rep": r.get("repetition"),
            "primary": prim,
            "all_labels": labels,
        })

    print(f"total runs analyzed: {len(details)}\n")
    print("--- global label frequency ---")
    for lab, cnt in Counter(all_labels).most_common():
        print(f"  {lab:30s} {cnt}")

    print("\n--- primary failure by method ---")
    methods = sorted(by_method_primary.keys())
    labs = PRIMARY_ORDER
    header = ["method"] + labs
    print("  " + " | ".join(f"{h[:16]:>16s}" for h in header))
    for m in methods:
        row = [m] + [str(by_method_primary[m].get(l, 0)) for l in labs]
        print("  " + " | ".join(f"{c[:16]:>16s}" for c in row))

    print("\n--- primary failure by task ---")
    for t in sorted(by_task_primary.keys()):
        counts = dict(by_task_primary[t])
        print(f"  {t:32s} {counts}")

    out_path = Path(f"/data/workspace/emnlp_experiments/analysis/{experiment_id}/failures.json")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps({
        "by_method_primary": {k: dict(v) for k, v in by_method_primary.items()},
        "by_task_primary": {k: dict(v) for k, v in by_task_primary.items()},
        "details": details,
    }, indent=2, ensure_ascii=False))
    print(f"\nwrote {out_path}")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "E1")
