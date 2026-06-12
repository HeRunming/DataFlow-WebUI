"""Method-env shadow setup.

For methods that need a different cwd than DataFlow-WebUI root (e.g., stripped skills,
no .mcp.json), we build a minimal shadow directory that symlinks back to the real backend.
"""
from __future__ import annotations
from pathlib import Path
import json
import os
import shutil
import sys
from .config import WEBUI_ROOT, ORIGINAL_CLAUDE_DIR, ORIGINAL_MCP_JSON, METHOD_ENV_ROOT, METHODS


def _link(src: Path, dst: Path) -> None:
    if dst.exists() or dst.is_symlink():
        return
    dst.symlink_to(src)


def build_shadow(method_name: str, *, force: bool = False) -> Path:
    """Create or refresh a shadow cwd for a method. Returns the cwd path.

    If the shadow dir already exists and `force` is False, keep it as-is
    (so manually-curated slim/only_dev shadows are preserved).
    """
    cfg = METHODS[method_name]
    if cfg.agent_cwd is None:
        raise ValueError(f"method {method_name} has no agent_cwd")
    if cfg.agent_cwd == WEBUI_ROOT:
        return WEBUI_ROOT

    shadow = cfg.agent_cwd
    if shadow.exists() and not force:
        return shadow
    if shadow.exists():
        shutil.rmtree(shadow)
    shadow.mkdir(parents=True)

    # Link back essentials so CLI can still find tools (Read/Write/Edit rooted here)
    # but keep .claude and .mcp.json optional per method.
    for entry in ("backend", "frontend", "docs", "data", "scripts", "README.md"):
        src = WEBUI_ROOT / entry
        if src.exists():
            _link(src, shadow / entry)

    if cfg.load_skills:
        claude_dir = shadow / ".claude"
        claude_dir.mkdir(exist_ok=True)
        _link(ORIGINAL_CLAUDE_DIR / "skills", claude_dir / "skills")

    if cfg.mcp_config:
        mcp_json = shadow / ".mcp.json"
        server_script = (METHOD_ENV_ROOT.parent / "runner" / "stdio_mcp_server.py").resolve()
        mcp_payload = {
            "mcpServers": {
                "dataflow": {
                    "command": sys.executable,
                    "args": [str(server_script)],
                }
            }
        }
        mcp_json.write_text(json.dumps(mcp_payload, ensure_ascii=False, indent=2), encoding="utf-8")

    return shadow


def ensure_all_shadows() -> None:
    for name, cfg in METHODS.items():
        if cfg.agent_cwd is not None and cfg.agent_cwd != WEBUI_ROOT:
            build_shadow(name)
