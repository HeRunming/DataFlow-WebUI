"""Build the paper's main Table 1 by merging across experiments.

Sources (post v3 MCP — server-side validation gate + 4 new tools):
- E1_mcpguard_v3: dataflow_agent, mcp_only, df_agent_v2, df_agent_slim
                  (current headline; df_agent_slim is the new winner at 48.6%)
- E1:             direct_cc                  (old, no MCP guard available)
- skill_ablation: df_agent_only_dev          (slim variant baseline)

Manual is intentionally left as `--` because we never ran a controlled
human baseline — it's deferred to the formative study.

Writes:
  analysis/main_table_merged.tex
  analysis/main_table_merged.csv
"""
from __future__ import annotations
import csv
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from runner.config import RESULTS_ROOT
from analysis.aggregate import (  # type: ignore
    load_runs, aggregate_per_method, render_main_table_tex
)

# (method, source-experiment-id)
SOURCES = [
    ("dataflow_agent", "E1_mcpguard_v3"),
    ("mcp_only",       "E1_mcpguard_v3"),
    ("df_agent_v2",    "E1_mcpguard_v3"),
    ("df_agent_slim",  "E1_mcpguard_v3"),
    ("df_agent_only_dev", "skill_ablation"),
    ("direct_cc",      "E1"),
]


def main() -> int:
    merged: dict[str, dict] = {}
    for method, expt in SOURCES:
        runs = [r for r in load_runs(expt) if r.get("method") == method]
        if not runs:
            print(f"!! {method} from {expt}: 0 runs found")
            continue
        agg = aggregate_per_method(runs)
        merged[method] = agg.get(method) or {}
        print(f"   {method:20} from {expt:15}: n={merged[method].get('n_runs')}  "
              f"succ_rate={merged[method].get('exec_success_rate')}  "
              f"op_err={merged[method].get('operator_errors_mean')}")

    out_dir = Path("/data/workspace/emnlp_experiments/analysis")
    out_dir.mkdir(parents=True, exist_ok=True)

    # Render LaTeX
    tex = render_main_table_tex(merged)
    tex_path = out_dir / "main_table_merged.tex"
    tex_path.write_text(tex)
    print(f"\nwrote {tex_path}")

    # Also dump CSV for sanity
    csv_path = out_dir / "main_table_merged.csv"
    if merged:
        cols = list(next(iter(merged.values())).keys())
        with csv_path.open("w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["method"] + cols)
            for m, d in merged.items():
                w.writerow([m] + [d.get(c, "") for c in cols])
        print(f"wrote {csv_path}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
