"""Central configuration for all experiment runs.

Single source of truth for endpoints, paths, method definitions, and ablation flags.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# ---------- endpoints ----------
BACKEND_BASE = "http://localhost:8000"
WS_URL_TEMPLATE = "ws://localhost:8000/api/v1/agent/ws?user_id={user_id}"

# ---------- repo paths ----------
REPO_ROOT = Path("/data/workspace/df_web_and_skills")
WEBUI_ROOT = REPO_ROOT / "DataFlow-WebUI"
DATAFLOW_ROOT = REPO_ROOT / "DataFlow"
SKILLS_ROOT = REPO_ROOT / "DataFlow-Skills"
ORIGINAL_CLAUDE_DIR = WEBUI_ROOT / ".claude"
ORIGINAL_MCP_JSON = WEBUI_ROOT / ".mcp.json"

# ---------- experiment paths ----------
EXPERIMENT_ROOT = Path("/data/workspace/emnlp_experiments")
TASKS_DIR = EXPERIMENT_ROOT / "tasks"
RESULTS_ROOT = EXPERIMENT_ROOT / "results"
LOGS_DIR = EXPERIMENT_ROOT / "logs"
METHOD_ENV_ROOT = EXPERIMENT_ROOT / "method_envs"  # per-method agent cwd shadows

# ---------- defaults ----------
DEFAULT_TIMEOUT_SEC = 600
POLL_INTERVAL_SEC = 2
MAX_POLL_SEC = 600

# ---------- method definitions ----------
@dataclass
class MethodConfig:
    name: str
    # agent启动的工作目录。None = 不启动 agent 子进程 (manual)
    agent_cwd: Path | None
    # 传给 Claude CLI 的 --mcp-config 路径; None = 不挂 MCP
    mcp_config: Path | None
    # --allowedTools. None = CLI 默认
    allowed_tools: str | None
    # 是否在 system prompt 里启用 think-first / DAG-sync / exec-gating 要求
    enable_think_first: bool = True
    enable_dag_sync: bool = True
    enable_exec_gating: bool = True
    # 是否加载 DataFlow-Skills (通过 cwd 的 .claude/skills/ 存在与否控制)
    load_skills: bool = True
    # 附加描述，写入 run_trace
    notes: str = ""


METHODS: dict[str, MethodConfig] = {
    "dataflow_agent": MethodConfig(
        name="dataflow_agent",
        agent_cwd=WEBUI_ROOT,
        mcp_config=ORIGINAL_MCP_JSON,
        allowed_tools="mcp__dataflow__*,Read,Write,Edit",
        load_skills=True,
        notes="Full system: MCP + skills + DAG sync + exec gating",
    ),
    "mcp_only": MethodConfig(
        name="mcp_only",
        agent_cwd=METHOD_ENV_ROOT / "mcp_only",  # shadow webui root w/o skills
        mcp_config=ORIGINAL_MCP_JSON,
        allowed_tools="mcp__dataflow__*,Read,Write,Edit",
        load_skills=False,
        notes="MCP available, skills stripped",
    ),
    "direct_cc": MethodConfig(
        name="direct_cc",
        agent_cwd=METHOD_ENV_ROOT / "direct_cc",  # no .mcp.json, no skills
        mcp_config=None,
        allowed_tools="Read,Write,Edit,Grep,Glob,Bash",
        load_skills=False,
        notes="Bare Claude Code: no MCP, no skills, no UI sync; given DataFlow repo to read",
    ),
    "pure_cc": MethodConfig(
        name="pure_cc",
        agent_cwd=METHOD_ENV_ROOT / "pure_cc",  # no DataFlow repo, no MCP, no skills
        mcp_config=None,
        allowed_tools="Read,Write,Edit,Grep,Glob,Bash",
        load_skills=False,
        notes="Pure Claude Code: writes arbitrary Python (pandas/requests/openai), no DataFlow framework at all",
    ),
    "manual": MethodConfig(
        name="manual",
        agent_cwd=None,
        mcp_config=None,
        allowed_tools=None,
        notes="Human-written pipeline; no agent",
    ),
    # --- skill-content ablations ---
    "df_agent_slim": MethodConfig(
        name="df_agent_slim",
        agent_cwd=METHOD_ENV_ROOT / "df_agent_slim",
        mcp_config=ORIGINAL_MCP_JSON,
        allowed_tools="mcp__dataflow__*,Read,Write,Edit",
        load_skills=True,
        notes="Only the slim generating-dataflow-pipeline skill (category-annotated decision table, dogma-weakened).",
    ),
    "df_agent_only_dev": MethodConfig(
        name="df_agent_only_dev",
        agent_cwd=METHOD_ENV_ROOT / "df_agent_only_dev",
        mcp_config=ORIGINAL_MCP_JSON,
        allowed_tools="mcp__dataflow__*,Read,Write,Edit",
        load_skills=True,
        notes="Only the dataflow-dev skill; MCP layered rule via system prompt.",
    ),
    "df_agent_v2": MethodConfig(
        name="df_agent_v2",
        agent_cwd=METHOD_ENV_ROOT / "df_agent_v2",
        mcp_config=ORIGINAL_MCP_JSON,
        allowed_tools="mcp__dataflow__*,Read,Write,Edit",
        load_skills=True,
        notes="Single purpose-built skill (dataflow-pipeline-v2) designed from E1 agent-behavior observations.",
    ),
    "df_agent_v3": MethodConfig(
        name="df_agent_v3",
        agent_cwd=METHOD_ENV_ROOT / "df_agent_v3",
        mcp_config=ORIGINAL_MCP_JSON,
        allowed_tools="mcp__dataflow__*,Read,Write,Edit",
        load_skills=True,
        notes="Concise v3 skill: language detection emphasis, minimal decision table, no duplication with MCP v3 server.",
    ),
    # --- ablations on top of dataflow_agent ---
    "no_mcp": MethodConfig(
        name="no_mcp",
        agent_cwd=WEBUI_ROOT,
        mcp_config=None,
        allowed_tools="Read,Write,Edit",
        load_skills=True,
        notes="Skills but no MCP grounding",
    ),
    "no_skills": MethodConfig(
        name="no_skills",
        agent_cwd=METHOD_ENV_ROOT / "no_skills",
        mcp_config=ORIGINAL_MCP_JSON,
        allowed_tools="mcp__dataflow__*,Read,Write,Edit",
        load_skills=False,
        notes="MCP but no pipeline-generation skill",
    ),
    "no_dag_sync": MethodConfig(
        name="no_dag_sync",
        agent_cwd=WEBUI_ROOT,
        mcp_config=ORIGINAL_MCP_JSON,
        allowed_tools="mcp__dataflow__*,Read,Write,Edit",
        load_skills=True,
        enable_dag_sync=False,
        notes="Full system but WebSocket sync disabled",
    ),
    "no_exec_gating": MethodConfig(
        name="no_exec_gating",
        agent_cwd=WEBUI_ROOT,
        mcp_config=ORIGINAL_MCP_JSON,
        allowed_tools="mcp__dataflow__*,Read,Write,Edit",
        load_skills=True,
        enable_exec_gating=False,
        notes="Full system but agent may self-execute",
    ),
}


@dataclass
class TaskSpec:
    task_id: str
    user_prompt: str
    sample_path: Path
    expected_chain: list[dict[str, Any]]
    expected_fields_produced: list[str]
    acceptance: dict[str, Any]
    difficulty: str = "medium"
    category: str = "misc"
    notes: str = ""


def load_task(task_id: str) -> TaskSpec:
    """Load a task definition from tasks/<task_id>/task.json."""
    import json
    task_dir = TASKS_DIR / task_id
    data = json.loads((task_dir / "task.json").read_text())
    sample = task_dir / data.get("sample_path", "sample.jsonl")
    return TaskSpec(
        task_id=data["task_id"],
        user_prompt=data["user_prompt"],
        sample_path=sample,
        expected_chain=data["expected_chain"],
        expected_fields_produced=data.get("expected_fields_produced", []),
        acceptance=data["acceptance"],
        difficulty=data.get("difficulty", "medium"),
        category=data.get("category", "misc"),
        notes=data.get("notes", ""),
    )


def list_task_ids() -> list[str]:
    return sorted(p.name for p in TASKS_DIR.iterdir() if (p / "task.json").exists())
