"""Cross-method task-level comparison for E1.

For each task, compare the 4 methods (manual skipped) and identify:
  - tasks where dataflow_agent > mcp_only (skill helps)
  - tasks where dataflow_agent < mcp_only (skill hurts / over-generalizes)
  - tasks where both methods fail (infra or task-definition issue)

Produces per-task breakdown + aggregate "skill impact score" per task.
"""
from __future__ import annotations
import json
import sys
from pathlib import Path
from collections import defaultdict

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from runner.config import RESULTS_ROOT


def load(exp_id: str) -> list[dict]:
    rows = []
    for sub in (RESULTS_ROOT / exp_id).iterdir():
        p = sub / "run_trace.json"
        if p.exists():
            try:
                rows.append(json.loads(p.read_text()))
            except Exception:
                pass
    return rows


def main(exp_id: str = "E1") -> None:
    runs = load(exp_id)
    by_task_method = defaultdict(lambda: defaultdict(list))
    for r in runs:
        by_task_method[r["task_id"]][r["method"]].append(r)

    print(f"{'task':<32} {'method':<18} {'n':>3} {'succ':>6} {'op_err':>7} {'time_p_min':>12}")
    print("-" * 90)
    skill_effect = []
    for task in sorted(by_task_method):
        for m in ["dataflow_agent", "mcp_only", "direct_cc", "df_agent_slim", "df_agent_only_dev", "df_agent_v2"]:
            rs = by_task_method[task].get(m)
            if not rs:
                continue
            n = len(rs)
            succ = sum(1 for r in rs if r.get("exec_success"))
            op_err = sum(r.get("operator_errors") or 0 for r in rs) / n
            tpipes = [r.get("time_to_pipeline_min") for r in rs if r.get("time_to_pipeline_min") is not None]
            tp = sum(tpipes) / len(tpipes) if tpipes else None
            tp_s = f"{tp:.2f}" if tp is not None else "--"
            print(f"{task:<32} {m:<18} {n:>3} {succ}/{n:<4} {op_err:>7.2f} {tp_s:>12}")
        # compute skill effect (DF-Agent - MCP-only)
        df = by_task_method[task].get("dataflow_agent", [])
        mc = by_task_method[task].get("mcp_only", [])
        if df and mc:
            df_succ = sum(1 for r in df if r.get("exec_success"))
            mc_succ = sum(1 for r in mc if r.get("exec_success"))
            delta = (df_succ / len(df)) - (mc_succ / len(mc))
            skill_effect.append((task, delta, df_succ, len(df), mc_succ, len(mc)))
        print()

    print("\n--- skill effect (DF-Agent succ_rate - MCP-only succ_rate) per task ---")
    skill_effect.sort(key=lambda x: -x[1])
    for t, d, df_s, df_n, mc_s, mc_n in skill_effect:
        tag = "SKILL HELPS" if d > 0 else ("SKILL HURTS" if d < 0 else "tied")
        print(f"  {t:<32}  delta={d:+.2f}  DF={df_s}/{df_n}  MCP={mc_s}/{mc_n}   {tag}")

    print("\n--- summary ---")
    helps = sum(1 for _, d, *_ in skill_effect if d > 0.01)
    hurts = sum(1 for _, d, *_ in skill_effect if d < -0.01)
    tied = len(skill_effect) - helps - hurts
    print(f"  skill helps: {helps} tasks")
    print(f"  skill hurts: {hurts} tasks")
    print(f"  tied:        {tied} tasks")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "E1")
