"""Chat transport for the experiment runner.

Default path uses the in-product WebSocket endpoint. When localhost networking is
blocked, we fall back to spawning the Claude CLI directly in the method-specific
cwd while preserving the normalized raw_trace format expected by downstream
analysis.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import os
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any

import websockets  # type: ignore
from loguru import logger

from .config import DEFAULT_TIMEOUT_SEC, METHODS, WS_URL_TEMPLATE


def _truncate(obj: Any, limit: int = 400) -> str:
    try:
        s = json.dumps(obj, ensure_ascii=False)
    except Exception:
        s = str(obj)
    return s if len(s) <= limit else s[:limit] + "…"


class AgentChatSession:
    def __init__(
        self,
        user_id: str,
        raw_trace_path: Path,
        timeout_sec: int = DEFAULT_TIMEOUT_SEC,
    ) -> None:
        self.user_id = user_id
        self.raw_trace_path = raw_trace_path
        self.timeout_sec = timeout_sec
        self._ws = None
        self._fp = None
        self.chunks: list[dict] = []
        self.t_first_msg: float | None = None
        self.t_done: float | None = None

    async def __aenter__(self):
        url = WS_URL_TEMPLATE.format(user_id=self.user_id)
        self._ws = await websockets.connect(url, max_size=None, ping_interval=20)
        self._fp = self.raw_trace_path.open("w", encoding="utf-8")
        return self

    async def __aexit__(self, *exc):
        if self._ws is not None:
            with contextlib.suppress(Exception):
                await self._ws.close()
        if self._fp is not None:
            self._fp.close()

    def _record(self, direction: str, payload: dict) -> None:
        evt = {"t": time.time(), "dir": direction, "payload": payload}
        self._fp.write(json.dumps(evt, ensure_ascii=False) + "\n")
        self._fp.flush()
        if direction == "in":
            self.chunks.append(payload)

    async def send_chat(self, message: str) -> None:
        payload = {"type": "chat", "message": message}
        self.t_first_msg = time.time()
        await self._ws.send(json.dumps(payload))
        self._record("out", payload)

    async def receive_until_done(self) -> None:
        deadline = time.time() + self.timeout_sec
        while True:
            remain = deadline - time.time()
            if remain <= 0:
                logger.warning("ws session timed out")
                break
            try:
                raw = await asyncio.wait_for(self._ws.recv(), timeout=min(remain, 30))
            except asyncio.TimeoutError:
                continue
            try:
                msg = json.loads(raw)
            except Exception:
                logger.warning(f"non-json chunk: {raw[:200]}")
                continue
            self._record("in", msg)
            mtype = msg.get("type")
            if mtype == "done":
                self.t_done = time.time()
                return
            if mtype == "error":
                self.t_done = time.time()
                logger.warning(f"agent error: {msg}")
                return


def _build_stdio_mcp_config() -> Path:
    """Build an MCP config that connects to the running backend via SSE."""
    payload = {
        "mcpServers": {
            "dataflow": {
                "type": "sse",
                "url": "http://localhost:8000/mcp"
            }
        }
    }
    tmp = tempfile.NamedTemporaryFile(prefix="dataflow-mcp-", suffix=".json", delete=False)
    with tmp:
        tmp.write(json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8"))
    return Path(tmp.name)


def _emit_normalized(fp, payload: dict, *, chunks: list[dict]) -> bool:
    """Translate CLI stream-json into the normalized event format used by parser.py.

    Returns True iff a terminal `done` was written.
    """
    emitted_done = False

    def emit(msg: dict) -> None:
        evt = {"t": time.time(), "dir": "in", "payload": msg}
        fp.write(json.dumps(evt, ensure_ascii=False) + "\n")
        fp.flush()
        chunks.append(msg)

    ptype = payload.get("type")
    if ptype == "stream_event":
        event = payload.get("event", {})
        delta = event.get("delta", {})
        if delta.get("type") == "text_delta":
            emit({"type": "text_chunk", "content": delta.get("text", "")})
    elif ptype == "assistant":
        message_obj = payload.get("message", {})
        for content_block in message_obj.get("content", []):
            block_type = content_block.get("type")
            if block_type == "text":
                text = content_block.get("text", "")
                if text:
                    emit({"type": "text_chunk", "content": text})
            elif block_type == "tool_use":
                emit({
                    "type": "tool_call_start",
                    "tool_use_id": content_block.get("id", ""),
                    "name": content_block.get("name", ""),
                    "input": content_block.get("input", {}) or {},
                    "input_preview": _truncate(content_block.get("input", {})),
                })
    elif ptype == "user":
        message_obj = payload.get("message", {})
        for content_block in message_obj.get("content", []):
            if content_block.get("type") != "tool_result":
                continue
            output = content_block.get("content", "")
            if isinstance(output, list):
                output = "".join(
                    (b.get("text", "") if isinstance(b, dict) else str(b))
                    for b in output
                )
            emit({
                "type": "tool_call_end",
                "tool_use_id": content_block.get("tool_use_id", ""),
                "is_error": bool(content_block.get("is_error", False)),
                "output": output,
                "output_preview": _truncate(output),
            })
    elif ptype == "result":
        subtype = payload.get("subtype", "")
        stop_reason = payload.get("stop_reason", "")
        is_error = bool(payload.get("is_error", False))
        result_text = payload.get("result", "") or ""
        if result_text.strip():
            emit({"type": "text_chunk", "content": result_text})
        if is_error or stop_reason in ("kvcache_no_enough", "max_turns_exceeded"):
            hint = {
                "kvcache_no_enough": "模型上下文/KV 缓存已塞满，Agent 中止。",
                "max_turns_exceeded": "Agent 工具调用轮次达到上限。",
            }.get(stop_reason, result_text or f"Agent ended with stop_reason={stop_reason}")
            emit({"type": "error", "message": hint})
        emit({"type": "done"})
        emitted_done = True
    return emitted_done


async def _run_cli_chat(
    user_prompt: str,
    raw_trace_path: Path,
    *,
    method: str,
    timeout_sec: int,
) -> dict:
    import shutil

    cfg = METHODS[method]
    cli = shutil.which("claude")
    if not cli:
        raise RuntimeError("No claude CLI found in PATH")
    if cfg.agent_cwd is None:
        raise RuntimeError(f"method {method} has no agent cwd")

    cmd = [
        cli,
        "--print", user_prompt,
        "--output-format", "stream-json",
        "--verbose",
        "--allowedTools", cfg.allowed_tools or "Read,Write,Edit",
        "--permission-mode", "dontAsk",
    ]
    temp_mcp: Path | None = None
    if cfg.mcp_config:
        temp_mcp = _build_stdio_mcp_config()
        cmd += ["--mcp-config", str(temp_mcp)]

    raw_trace_path.parent.mkdir(parents=True, exist_ok=True)
    user_id = f"expt_{uuid.uuid4().hex[:8]}"
    t_first = time.time()
    chunks: list[dict] = []
    done_seen = False

    with raw_trace_path.open("w", encoding="utf-8") as fp:
        fp.write(json.dumps({"t": t_first, "dir": "out", "payload": {"type": "chat", "message": user_prompt}}, ensure_ascii=False) + "\n")
        fp.flush()

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(cfg.agent_cwd),
            limit=10 * 1024 * 1024,  # 10 MB buffer for large tool results
        )
        stderr_chunks: list[bytes] = []
        try:
            assert proc.stdout is not None
            assert proc.stderr is not None
            while True:
                try:
                    line = await asyncio.wait_for(proc.stdout.readline(), timeout=min(30, timeout_sec))
                except asyncio.TimeoutError:
                    if proc.returncode is not None:
                        break
                    continue
                if not line:
                    break
                try:
                    payload = json.loads(line.decode("utf-8", errors="replace"))
                except Exception:
                    continue
                if _emit_normalized(fp, payload, chunks=chunks):
                    done_seen = True
            while True:
                err = await proc.stderr.readline()
                if not err:
                    break
                stderr_chunks.append(err)
            await asyncio.wait_for(proc.wait(), timeout=5)
        finally:
            if proc.returncode is None:
                with contextlib.suppress(Exception):
                    proc.kill()
            if temp_mcp is not None:
                with contextlib.suppress(Exception):
                    temp_mcp.unlink()

        if not done_seen:
            stderr_text = b"".join(stderr_chunks).decode("utf-8", errors="replace").strip()
            if stderr_text:
                evt = {"t": time.time(), "dir": "in", "payload": {"type": "error", "message": stderr_text[:2000]}}
                fp.write(json.dumps(evt, ensure_ascii=False) + "\n")
                fp.flush()
                chunks.append(evt["payload"])
            evt = {"t": time.time(), "dir": "in", "payload": {"type": "done"}}
            fp.write(json.dumps(evt, ensure_ascii=False) + "\n")
            fp.flush()
            chunks.append(evt["payload"])

    return {
        "user_id": user_id,
        "t_first_msg": t_first,
        "t_done": time.time(),
        "n_chunks": len(chunks),
        "raw_trace_path": str(raw_trace_path),
        "transport": "cli",
    }


def _chat_mode() -> str:
    return os.environ.get("DATAFLOW_CHAT_MODE", "auto").strip().lower() or "auto"


async def run_ws_chat(
    user_prompt: str,
    raw_trace_path: Path,
    user_id: str | None = None,
    timeout_sec: int = DEFAULT_TIMEOUT_SEC,
    method: str = "dataflow_agent",
) -> dict:
    mode = _chat_mode()
    if mode == "cli":
        return await _run_cli_chat(user_prompt, raw_trace_path, method=method, timeout_sec=timeout_sec)

    if mode == "ws":
        user_id = user_id or f"expt_{uuid.uuid4().hex[:8]}"
        async with AgentChatSession(user_id, raw_trace_path, timeout_sec) as s:
            await s.send_chat(user_prompt)
            await s.receive_until_done()
        return {
            "user_id": user_id,
            "t_first_msg": s.t_first_msg,
            "t_done": s.t_done,
            "n_chunks": len(s.chunks),
            "raw_trace_path": str(raw_trace_path),
            "transport": "ws",
        }

    try:
        user_id = user_id or f"expt_{uuid.uuid4().hex[:8]}"
        async with AgentChatSession(user_id, raw_trace_path, timeout_sec) as s:
            await s.send_chat(user_prompt)
            await s.receive_until_done()
        return {
            "user_id": user_id,
            "t_first_msg": s.t_first_msg,
            "t_done": s.t_done,
            "n_chunks": len(s.chunks),
            "raw_trace_path": str(raw_trace_path),
            "transport": "ws",
        }
    except Exception as exc:
        logger.warning(f"WebSocket transport unavailable; falling back to CLI transport: {exc}")
        return await _run_cli_chat(user_prompt, raw_trace_path, method=method, timeout_sec=timeout_sec)
