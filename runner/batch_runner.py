"""Batch run scheduler with resume/skip-done support.

Usage:
    python -m runner.batch_runner --experiment-id E1 --tasks all --methods all --reps 5

Each (task, method, rep) produces
    results/<experiment_id>/<task>__<method>__rep<rep>/{raw_trace.jsonl, run_trace.json}
If run_trace.json already exists and --resume is set (default), the cell is skipped.
"""
from __future__ import annotations
import argparse
import json
import sys
import time
from pathlib import Path

from loguru import logger

from .config import METHODS, RESULTS_ROOT, list_task_ids
from .task_runner import run_one


def _already_done(experiment_id: str, task_id: str, method: str, rep: int) -> bool:
    p = RESULTS_ROOT / experiment_id / f"{task_id}__{method}__rep{rep}" / "run_trace.json"
    return p.exists() and p.stat().st_size > 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--experiment-id", default="E1")
    ap.add_argument("--tasks", default="all",
                    help="'all' or comma-separated task ids")
    ap.add_argument("--methods", default="all",
                    help="'all', 'main4' (dataflow_agent/mcp_only/direct_cc/manual), 'ablation', or comma-separated")
    ap.add_argument("--reps", type=int, default=5)
    ap.add_argument("--start-rep", type=int, default=0)
    ap.add_argument("--no-resume", action="store_true", help="Re-run even if trace exists")
    ap.add_argument("--timeout-sec", type=int, default=600)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    # resolve tasks
    if args.tasks == "all":
        tasks = list_task_ids()
    else:
        tasks = [t.strip() for t in args.tasks.split(",") if t.strip()]

    # resolve methods
    if args.methods == "all":
        methods = list(METHODS.keys())
    elif args.methods == "main4":
        methods = ["dataflow_agent", "mcp_only", "direct_cc", "manual"]
    elif args.methods == "ablation":
        methods = ["dataflow_agent", "no_mcp", "no_skills", "no_dag_sync", "no_exec_gating"]
    else:
        methods = [m.strip() for m in args.methods.split(",") if m.strip()]

    reps = list(range(args.start_rep, args.start_rep + args.reps))

    plan = []
    for t in tasks:
        for m in methods:
            for r in reps:
                if m == "manual":
                    continue  # manual baseline produced offline
                if not args.no_resume and _already_done(args.experiment_id, t, m, r):
                    continue
                plan.append((t, m, r))

    logger.info(f"experiment={args.experiment_id} tasks={len(tasks)} methods={len(methods)} "
                f"reps={args.reps} -> {len(plan)} runs to execute (after resume filtering)")

    if args.dry_run:
        for t, m, r in plan[:20]:
            logger.info(f"  [plan] {t} / {m} / rep{r}")
        if len(plan) > 20:
            logger.info(f"  ... and {len(plan)-20} more")
        return 0

    done, failed = 0, 0
    t0 = time.time()
    for i, (t, m, r) in enumerate(plan):
        elapsed = time.time() - t0
        avg = elapsed / max(1, i) if i else 0
        eta = avg * (len(plan) - i)
        logger.info(f"[{i+1}/{len(plan)}] {t}/{m}/rep{r}  elapsed={elapsed/60:.1f}min  eta={eta/60:.1f}min")
        try:
            s = run_one(t, m, r, experiment_id=args.experiment_id, timeout_sec=args.timeout_sec)
            ok = (s.get("exec_success") is True) or (s.get("new_pipeline_ids") and not s.get("error"))
            if ok:
                done += 1
            else:
                failed += 1
            logger.info(f"  -> exec_success={s.get('exec_success')} "
                        f"ops_err={s.get('operator_errors')} "
                        f"time_min={s.get('time_min')}")
        except Exception as e:
            failed += 1
            logger.error(f"  -> run_one raised: {e}")

    logger.info(f"DONE: succeeded={done} failed={failed} total={len(plan)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
