"""Parse raw WebSocket chunks into structured events.

The agent endpoint emits chunks with types like:
  - text_chunk: streamed assistant text
  - tool_call_start / tool_call_end: one per MCP tool invocation
  - done / error
  - sync_pipeline (from render_pipeline_in_editor broadcast)

We normalize these into a flat event list for downstream metrics.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
import json
import re


@dataclass
class ToolCall:
    name: str  # e.g. "mcp__dataflow__create_pipeline"
    args: dict
    result: Any | None = None
    t_start: float | None = None
    t_end: float | None = None


@dataclass
class ParsedTrace:
    text_segments: list[str] = field(default_factory=list)
    tool_calls: list[ToolCall] = field(default_factory=list)
    sync_pipeline_events: list[dict] = field(default_factory=list)
    pipeline_ids_created: list[str] = field(default_factory=list)
    pipeline_ids_executed: list[str] = field(default_factory=list)
    t_first_tool_call: float | None = None
    t_first_pipeline_rendered: float | None = None
    t_done: float | None = None
    error: str | None = None

    @property
    def final_text(self) -> str:
        return "".join(self.text_segments)

    @property
    def n_create_pipeline_calls(self) -> int:
        return sum(1 for c in self.tool_calls if "create_pipeline" in c.name)

    @property
    def n_render_pipeline_calls(self) -> int:
        return sum(1 for c in self.tool_calls if "render_pipeline_in_editor" in c.name)

    @property
    def n_execute_pipeline_calls(self) -> int:
        return sum(1 for c in self.tool_calls
                   if "execute_pipeline" in c.name)


def parse_raw_trace(raw_trace_path: Path) -> ParsedTrace:
    """Load raw_trace.jsonl (one JSON per line: {t, dir, payload}) and parse."""
    pt = ParsedTrace()
    pending_calls: dict[str, ToolCall] = {}
    with raw_trace_path.open() as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                evt = json.loads(line)
            except Exception:
                continue
            if evt.get("dir") != "in":
                continue
            t = evt.get("t")
            p = evt.get("payload", {})
            mtype = p.get("type")
            if mtype == "text_chunk":
                pt.text_segments.append(p.get("text", ""))
            elif mtype == "tool_call_start":
                call_id = p.get("id") or p.get("tool_use_id") or str(len(pending_calls))
                tc = ToolCall(name=p.get("name", "<unknown>"),
                              args=p.get("input", {}) or p.get("args", {}) or {},
                              t_start=t)
                pending_calls[call_id] = tc
                pt.tool_calls.append(tc)
                if pt.t_first_tool_call is None:
                    pt.t_first_tool_call = t
            elif mtype == "tool_call_end":
                call_id = p.get("id") or p.get("tool_use_id")
                if call_id in pending_calls:
                    tc = pending_calls.pop(call_id)
                    tc.t_end = t
                    tc.result = p.get("result") or p.get("output")
                    _extract_pipeline_ids(tc, pt)
            elif mtype == "sync_pipeline":
                pt.sync_pipeline_events.append(p)
                if pt.t_first_pipeline_rendered is None:
                    pt.t_first_pipeline_rendered = t
                pid = p.get("pipeline", {}).get("id") or p.get("pipeline_id")
                if pid and pid not in pt.pipeline_ids_created:
                    pt.pipeline_ids_created.append(pid)
            elif mtype == "done":
                pt.t_done = t
            elif mtype == "error":
                pt.t_done = t
                pt.error = str(p)
    return pt


def _extract_pipeline_ids(tc: ToolCall, pt: ParsedTrace) -> None:
    name = tc.name
    result = tc.result
    # try to find a pipeline_id-looking thing in result
    if result is None:
        return
    blob = result if isinstance(result, (str, dict, list)) else str(result)
    text = json.dumps(blob, ensure_ascii=False) if not isinstance(blob, str) else blob
    # Heuristic: "pipeline_id":"...", or ObjectId-like
    m = re.findall(r'"(?:pipeline_id|id)"\s*:\s*"([a-f0-9\-]{8,})"', text)
    if "create_pipeline" in name or "update_pipeline" in name:
        for pid in m:
            if pid not in pt.pipeline_ids_created:
                pt.pipeline_ids_created.append(pid)
    if "execute_pipeline" in name:
        for pid in m:
            if pid not in pt.pipeline_ids_executed:
                pt.pipeline_ids_executed.append(pid)
