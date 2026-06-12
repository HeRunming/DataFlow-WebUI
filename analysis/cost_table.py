#!/usr/bin/env python3
"""Aggregate per-run token/cost/time across methods.

Two trace shapes:
  - WS mode (df_agent_v3, mcp_only): a single terminal {"type":"usage",...}
    event emitted by the backend claude_adapter from the CLI `result` event.
  - CLI mode (direct_cc): per-assistant-turn usage + a final `result` event
    (parsed by dcc_cost.py). Here we read direct_cc's run_trace summary +
    its raw_trace `result` event.

Reports per method: median/mean total_cost_usd, total tokens (input+cache),
output tokens, num_turns, wall seconds.
"""
import json, glob, os, sys, statistics
from collections import defaultdict

RESULTS = "/data/workspace/emnlp_experiments/results"


def ws_usage(trace_path):
    """Return the terminal usage dict from a WS-mode raw_trace, or None."""
    found = None
    for line in open(trace_path):
        try:
            p = json.loads(line).get("payload", {})
        except Exception:
            continue
        if p.get("type") == "usage":
            found = p  # keep last
    return found


def dcc_result(trace_path):
    """Return cost/usage from a direct_cc CLI raw_trace `result` event."""
    cost = tokens_in = tokens_out = cache = turns = None
    for line in open(trace_path):
        try:
            p = json.loads(line).get("payload", {})
        except Exception:
            continue
        if p.get("type") == "result":
            cost = p.get("total_cost_usd")
            turns = p.get("num_turns")
            u = p.get("usage", {}) or {}
            tokens_in = u.get("input_tokens")
            tokens_out = u.get("output_tokens")
            cache = (u.get("cache_read_input_tokens", 0) or 0) + (u.get("cache_creation_input_tokens", 0) or 0)
    if cost is None:
        return None
    return {"total_cost_usd": cost, "num_turns": turns,
            "input_tokens": tokens_in, "output_tokens": tokens_out,
            "cache_read_input_tokens": cache, "cache_creation_input_tokens": 0}


def collect(experiments):
    by_method = defaultdict(list)
    for exp in experiments:
        for rt in glob.glob(os.path.join(RESULTS, exp, "*", "run_trace.json")):
            d = json.load(open(rt))
            rd = os.path.dirname(rt)
            raw = os.path.join(rd, "raw_trace.jsonl")
            if not os.path.exists(raw):
                continue
            m = d["method"]
            u = dcc_result(raw) if m == "direct_cc" else ws_usage(raw)
            if not u:
                continue
            wall = d.get("t_agent_wall_sec")
            total_in = (u.get("input_tokens") or 0) + (u.get("cache_read_input_tokens") or 0) + (u.get("cache_creation_input_tokens") or 0)
            by_method[m].append({
                "cost": u.get("total_cost_usd"),
                "in": total_in,
                "out": u.get("output_tokens") or 0,
                "turns": u.get("num_turns"),
                "wall": wall,
            })
    return by_method


def med(xs):
    xs = [x for x in xs if x is not None]
    return statistics.median(xs) if xs else None


if __name__ == "__main__":
    exps = sys.argv[1:] or ["E8_main", "E6_dcc_full"]
    bm = collect(exps)
    DISP = {"df_agent_v3": "DataFlow-Harness", "mcp_only": "MCP-only", "direct_cc": "Direct CC"}
    print(f"{'method':<20}{'n':>4}{'med $cost':>11}{'med in-tok':>12}{'med out-tok':>12}{'med turns':>10}{'med wall(s)':>12}")
    print("-" * 81)
    for m in ["direct_cc", "mcp_only", "df_agent_v3"]:
        if m not in bm:
            continue
        rows = bm[m]
        c = med([r["cost"] for r in rows])
        cstr = f"${c:.3f}" if c is not None else "n/a"
        print(f"{DISP.get(m,m):<20}{len(rows):>4}{cstr:>11}"
              f"{med([r['in'] for r in rows]) or 0:>12,.0f}"
              f"{med([r['out'] for r in rows]) or 0:>12,.0f}"
              f"{(med([r['turns'] for r in rows]) or 0):>10.0f}"
              f"{(med([r['wall'] for r in rows]) or 0):>12.1f}")
