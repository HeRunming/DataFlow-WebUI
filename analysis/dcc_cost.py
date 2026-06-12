#!/usr/bin/env python3
"""Analyze direct_cc runs: tool usage, wall time, token cost, operators used."""
import json, sys, glob, re
from collections import Counter
from pathlib import Path


def analyze_trace(trace_path: Path):
    tool_calls = Counter()
    bash_cmds = []
    written_files = []
    # token accounting
    max_input = 0
    total_output = 0
    cumulative_input = 0  # sum of per-assistant-turn input (proxy for context growth)
    n_assistant_turns = 0
    final_result = None
    t_first = None
    t_last = None

    for line in open(trace_path):
        try:
            rec = json.loads(line)
        except Exception:
            continue
        t = rec.get("t")
        if t:
            t_first = t_first or t
            t_last = t
        p = rec.get("payload", {})
        ptype = p.get("type")
        msg = p.get("message", {}) if isinstance(p.get("message"), dict) else {}

        # token usage on assistant turns
        usage = msg.get("usage") if isinstance(msg, dict) else None
        if usage:
            inp = usage.get("input_tokens", 0) or 0
            out = usage.get("output_tokens", 0) or 0
            cache_r = usage.get("cache_read_input_tokens", 0) or 0
            cache_c = usage.get("cache_creation_input_tokens", 0) or 0
            max_input = max(max_input, inp + cache_r + cache_c)
            total_output += out
            cumulative_input += inp + cache_r + cache_c
            n_assistant_turns += 1

        # tool calls
        content = msg.get("content")
        if isinstance(content, list):
            for c in content:
                if isinstance(c, dict) and c.get("type") == "tool_use":
                    tool_calls[c["name"]] += 1
                    inp = c.get("input", {})
                    if c["name"] == "Bash":
                        bash_cmds.append(inp.get("command", "")[:100])
                    if c["name"] in ("Write", "Edit"):
                        fp = inp.get("file_path", "")
                        if fp:
                            written_files.append(fp)

        # final result event carries authoritative usage + cost
        if ptype == "result":
            final_result = p

    wall = (t_last - t_first) if (t_first and t_last) else None
    return {
        "tool_calls": dict(tool_calls),
        "n_tool_calls": sum(tool_calls.values()),
        "bash_cmds": bash_cmds,
        "written_files": written_files,
        "max_context_tokens": max_input,
        "total_output_tokens": total_output,
        "cumulative_input_tokens": cumulative_input,
        "n_assistant_turns": n_assistant_turns,
        "wall_sec": wall,
        "final_result": final_result,
    }


def find_operators_in_pyfile(py_path: str):
    """Extract real DataFlow operators (self.x = SomeOp(...)) and custom classes."""
    if not py_path or not Path(py_path).exists():
        return {"exists": False}
    src = open(py_path).read()
    # operator instantiations in __init__
    instantiations = re.findall(r"self\.\w+\s*=\s*(\w+)\s*\(", src)
    custom_ops = re.findall(r"class\s+(\w+)\s*\([^)]*Operator[^)]*\)", src)
    dataflow_imports = re.findall(r"from dataflow[.\w]*\s+import\s+([^\n]+)", src)
    return {
        "exists": True,
        "instantiations": instantiations,
        "custom_operator_classes": custom_ops,
        "dataflow_imports": [s.strip() for s in dataflow_imports],
    }


if __name__ == "__main__":
    exp = sys.argv[1] if len(sys.argv) > 1 else "E6_dcc_validate"
    base = Path("/data/workspace/emnlp_experiments/results") / exp
    for run_dir in sorted(base.glob("*/")):
        trace = run_dir / "raw_trace.jsonl"
        if not trace.exists():
            continue
        print(f"\n{'='*70}\n{run_dir.name}\n{'='*70}")
        a = analyze_trace(trace)
        print(f"wall_sec: {a['wall_sec']:.1f}" if a['wall_sec'] else "wall_sec: ?")
        print(f"tool calls ({a['n_tool_calls']} total): {a['tool_calls']}")
        print(f"assistant turns: {a['n_assistant_turns']}")
        print(f"max context tokens (single turn): {a['max_context_tokens']:,}")
        print(f"total output tokens: {a['total_output_tokens']:,}")
        print(f"cumulative input tokens (sum over turns): {a['cumulative_input_tokens']:,}")
        fr = a["final_result"]
        if fr:
            print(f"result.duration_ms: {fr.get('duration_ms')}")
            print(f"result.total_cost_usd: {fr.get('total_cost_usd')}")
            print(f"result.num_turns: {fr.get('num_turns')}")
            u = fr.get("usage", {})
            if u:
                print(f"result.usage: in={u.get('input_tokens')} out={u.get('output_tokens')} "
                      f"cache_r={u.get('cache_read_input_tokens')} cache_c={u.get('cache_creation_input_tokens')}")
        # which .py did it write
        for f in a["written_files"]:
            if f.endswith(".py") and "/tmp/" in f:
                ops = find_operators_in_pyfile(f)
                print(f"\nwrote pipeline: {f}")
                if ops.get("exists"):
                    print(f"  dataflow imports: {ops['dataflow_imports']}")
                    print(f"  operator instantiations: {ops['instantiations']}")
                    print(f"  CUSTOM operator classes (bad): {ops['custom_operator_classes'] or 'none ✓'}")
                else:
                    print("  (file not found on disk)")
