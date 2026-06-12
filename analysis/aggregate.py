"""Aggregate run_trace.json files into summary tables for the paper.

Reads: results/<experiment_id>/*/run_trace.json
Writes: analysis/<experiment_id>/
    - per_run.csv              flat table of all runs
    - summary_per_method.csv   per-method aggregate (mean/median)
    - main_table.tex           LaTeX snippet for Table 1
    - ablation_table.tex       LaTeX snippet for ablation
    - lesson_signals.json      per-task signals for lessons
"""
from __future__ import annotations
import argparse
import csv
import json
import statistics
import sys
from pathlib import Path
from collections import defaultdict

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from runner.config import RESULTS_ROOT


METRIC_KEYS = [
    "time_min", "time_to_pipeline_min", "exec_success",
    "operator_errors", "strict_errors", "field_errors",
    "hallucinated", "missing_required", "wrong_order", "unexpected",
    "n_create_pipeline_calls", "n_render_pipeline_calls",
    "n_execute_pipeline_calls_by_agent",
    "n_valid_records", "n_total_records",
]


def load_runs(experiment_id: str) -> list[dict]:
    root = RESULTS_ROOT / experiment_id
    runs = []
    for sub in sorted(root.iterdir()):
        p = sub / "run_trace.json"
        if not p.exists():
            continue
        try:
            runs.append(json.loads(p.read_text()))
        except Exception:
            continue
    return runs


def _safe_mean(xs):
    xs = [x for x in xs if x is not None]
    return statistics.mean(xs) if xs else None


def _safe_median(xs):
    xs = [x for x in xs if x is not None]
    return statistics.median(xs) if xs else None


def _safe_rate(xs):
    xs = [1 if x else 0 for x in xs if x is not None]
    if not xs:
        return None
    return sum(xs) / len(xs)


def aggregate_per_method(runs: list[dict]) -> dict:
    grouped = defaultdict(list)
    for r in runs:
        grouped[r["method"]].append(r)
    out = {}
    for method, rs in grouped.items():
        n = len(rs)
        time_min_vals = [r.get("time_min") for r in rs]
        time_ptp = [r.get("time_to_pipeline_min") for r in rs]
        out[method] = {
            "n_runs": n,
            "time_median_min": _safe_median(time_min_vals),
            "time_to_pipeline_median_min": _safe_median(time_ptp),
            "exec_success_rate": _safe_rate([r.get("exec_success") for r in rs]),
            "operator_errors_mean": _safe_mean([r.get("operator_errors") for r in rs]),
            "strict_errors_mean": _safe_mean([r.get("strict_errors") for r in rs]),
            "field_errors_mean": _safe_mean([r.get("field_errors") for r in rs]),
            "n_create_pipeline_mean": _safe_mean([r.get("n_create_pipeline_calls") for r in rs]),
            "n_render_pipeline_mean": _safe_mean([r.get("n_render_pipeline_calls") for r in rs]),
            "n_execute_by_agent_mean": _safe_mean([r.get("n_execute_pipeline_calls_by_agent") for r in rs]),
            "n_pipelines_produced_rate": _safe_rate([bool(r.get("final_chain")) for r in rs]),
        }
    return out


def write_per_run_csv(runs: list[dict], out_path: Path) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["task_id", "method", "repetition"] + METRIC_KEYS + ["final_chain"])
        for r in runs:
            row = [r.get("task_id"), r.get("method"), r.get("repetition")]
            for k in METRIC_KEYS:
                v = r.get(k)
                if isinstance(v, bool):
                    v = int(v)
                row.append(v if v is not None else "")
            row.append(" | ".join(r.get("final_chain") or []))
            w.writerow(row)


def write_summary_csv(agg: dict, out_path: Path) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    cols = list(next(iter(agg.values())).keys()) if agg else []
    with out_path.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["method"] + cols)
        for m, d in agg.items():
            w.writerow([m] + [d.get(c, "") for c in cols])


def render_main_table_tex(agg: dict) -> str:
    """Render LaTeX for paper's main table."""
    order = [
        ("manual", "Manual"),
        ("direct_cc", "Direct CC"),
        ("mcp_only", "MCP-only"),
        ("df_agent_only_dev", "\\textsc{DF-Agent} (dev-only)"),
        ("df_agent_slim", "\\textsc{DF-Agent} (slim)"),
        ("df_agent_v2", "\\textsc{DF-Agent} (v2)"),
        ("dataflow_agent", "\\textsc{DF-Agent}"),
    ]

    def _fmt(v, pct=False, dec=1):
        if v is None:
            return "--"
        if pct:
            return f"{v*100:.{dec}f}"
        return f"{v:.{dec}f}"

    lines = [
        "\\begin{table}[t]",
        "\\centering",
        "\\small",
        "\\setlength{\\tabcolsep}{3pt}",
        "\\begin{tabular}{lcccccc}",
        "\\toprule",
        "Method & Time & Succ. & Op.\\ err & Fld.\\ err & Edits & Rat. \\\\",
        "\\midrule",
    ]
    for key, label in order:
        d = agg.get(key)
        if not d:
            row = [label] + ["--"] * 6
        else:
            time = _fmt(d.get("time_median_min"), dec=1)
            if time == "--":
                time = _fmt(d.get("time_to_pipeline_median_min"), dec=1) + "*"
            succ = _fmt(d.get("exec_success_rate"), pct=True, dec=1)
            ope = _fmt(d.get("operator_errors_mean"), dec=1)
            fle = _fmt(d.get("field_errors_mean"), dec=1)
            edits = "--"  # edit events: need a human UI event source; leave blank for now
            rate = "--"
            row = [label, time, succ, ope, fle, edits, rate]
        lines.append(" & ".join(row) + " \\\\")
    lines += [
        "\\bottomrule",
        "\\end{tabular}",
        "\\caption{Main evaluation. Time is median minutes to first successful Run (or time-to-pipeline * when execution was not reached); Succ. is the share of runs with valid output records; Op./Fld. err are mean operator and field-flow errors per task. Rating and manual Edits deferred.}",
        "\\label{tab:eval}",
        "\\end{table}",
    ]
    return "\n".join(lines) + "\n"


def render_ablation_table_tex(agg: dict) -> str:
    base = agg.get("dataflow_agent") or {}
    order = [
        ("no_mcp", "No MCP grounding", "hallucinated or stale operators", "operator_errors_mean"),
        ("no_skills", "No skills", "invalid field flow or wrong op", "operator_errors_mean"),
        ("no_dag_sync", "No DAG sync", "user cannot inspect workflow", "exec_success_rate"),
        ("no_exec_gating", "No execution gating", "self-initiated runs", "n_execute_by_agent_mean"),
    ]
    lines = [
        "\\begin{table}[t]",
        "\\centering",
        "\\small",
        "\\begin{tabular}{p{0.28\\linewidth}p{0.32\\linewidth}p{0.30\\linewidth}}",
        "\\toprule",
        "Setting & Expected failure mode & Measurement \\\\",
        "\\midrule",
    ]
    for key, label, fail, metric in order:
        d = agg.get(key) or {}
        b = base.get(metric) if base else None
        v = d.get(metric)
        if b is None or v is None:
            m = "--"
        elif metric.endswith("_rate"):
            m = f"{(b or 0)*100:.1f}\\%~$\\to$~{(v or 0)*100:.1f}\\%"
        else:
            m = f"{b:.1f}~$\\to$~{v:.1f}"
        lines.append(f"{label} & {fail} & {m} \\\\")
    lines += [
        "\\bottomrule",
        "\\end{tabular}",
        "\\caption{Ablation: removing each harness component. Baseline is \\textsc{DataFlow-Agent} full.}",
        "\\label{tab:ablation}",
        "\\end{table}",
    ]
    return "\n".join(lines) + "\n"


def compute_lesson_signals(runs: list[dict]) -> dict:
    """Lesson 1 (premature pipelines): compare n_create_pipeline across conditions.
    Lesson 2 (operator catalog paging): count runs where agent triggered 'context overflow' error.
    """
    by_method = defaultdict(list)
    for r in runs:
        by_method[r["method"]].append(r)

    result = {}
    for m, rs in by_method.items():
        ctx_overflow = sum(1 for r in rs if r.get("error") and "上下文" in (r.get("error") or ""))
        result[m] = {
            "n_runs": len(rs),
            "avg_create_pipeline": _safe_mean([r.get("n_create_pipeline_calls") for r in rs]),
            "avg_render_pipeline": _safe_mean([r.get("n_render_pipeline_calls") for r in rs]),
            "context_overflow_rate": ctx_overflow / max(1, len(rs)),
            "pipeline_produced_rate": _safe_rate([bool(r.get("final_chain")) for r in rs]),
        }
    return result


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--experiment-id", default="E1")
    ap.add_argument("--out-dir", default=None, help="override output dir")
    args = ap.parse_args()

    runs = load_runs(args.experiment_id)
    print(f"loaded {len(runs)} runs from {args.experiment_id}")
    if not runs:
        print("no runs found")
        return 1

    out_dir = Path(args.out_dir or f"/data/workspace/emnlp_experiments/analysis/{args.experiment_id}")
    out_dir.mkdir(parents=True, exist_ok=True)

    # per-run csv
    write_per_run_csv(runs, out_dir / "per_run.csv")
    print(f"  -> {out_dir}/per_run.csv")

    # per-method aggregate
    agg = aggregate_per_method(runs)
    write_summary_csv(agg, out_dir / "summary_per_method.csv")
    print(f"  -> {out_dir}/summary_per_method.csv")
    for m, d in agg.items():
        oe = d.get('operator_errors_mean')
        oe_s = f"{oe:.2f}" if oe is not None else "--"
        print(f"    {m}: n={d['n_runs']}  time={d['time_median_min']}  succ_rate={d['exec_success_rate']}  op_err={oe_s}")

    # LaTeX tables
    (out_dir / "main_table.tex").write_text(render_main_table_tex(agg))
    (out_dir / "ablation_table.tex").write_text(render_ablation_table_tex(agg))
    print(f"  -> {out_dir}/main_table.tex")
    print(f"  -> {out_dir}/ablation_table.tex")

    # lesson signals
    lesson = compute_lesson_signals(runs)
    (out_dir / "lesson_signals.json").write_text(json.dumps(lesson, indent=2, ensure_ascii=False))
    print(f"  -> {out_dir}/lesson_signals.json")
    for m, d in lesson.items():
        print(f"    {m}: ctx_overflow={d['context_overflow_rate']:.2%} avg_create_pipeline={d['avg_create_pipeline']}")

    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
