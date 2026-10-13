"""Resolve which cache file holds an operator's output for a task.

Shared by the task result / download / media endpoints so they cannot disagree
about cache layout.

Layouts, in lookup order:
1. ``operators_detail[op_key]["output_file"]`` — absolute path recorded by the
   executor (DAG executor, per-node cache dirs).
2. Legacy linear layout: ``{CACHE_DIR}/{task_id}_output/dataflow_cache_step_step{N}.jsonl``
   where operator index ``i`` writes step ``i + 1``.
"""
from __future__ import annotations

import os
from typing import Any, Dict, Optional, Tuple

CACHE_FILE_PREFIX = "dataflow_cache_step"


def task_cache_dir(cache_root: str, task_id: str) -> str:
    return os.path.join(cache_root, f"{task_id}_output")


def legacy_step_file(cache_root: str, task_id: str, step_file_index: int) -> str:
    return os.path.join(task_cache_dir(cache_root, task_id), f"{CACHE_FILE_PREFIX}_step{step_file_index}.jsonl")


def find_operator(
    operators_detail: Dict[str, Dict[str, Any]],
    step: Optional[int] = None,
    node: Optional[str] = None,
) -> Tuple[Optional[str], Optional[Dict[str, Any]]]:
    """Find an operator entry by node id (preferred) or 0-based operator index."""
    for op_key, info in (operators_detail or {}).items():
        if node is not None and (info.get("node_id") == node or op_key == node):
            return op_key, info
    if step is not None:
        for op_key, info in (operators_detail or {}).items():
            if info.get("index") == step:
                return op_key, info
    return None, None


def default_step(output: Dict[str, Any]) -> int:
    """Last completed operator, else the running one, else 0."""
    execution_results = output.get("execution_results") or []
    if execution_results:
        return execution_results[-1].get("index", 0)
    for info in (output.get("operators_detail") or {}).values():
        if info.get("status") == "running":
            return info.get("index", 0)
    return 0


def resolve_output_file(
    cache_root: str,
    task_id: str,
    output: Dict[str, Any],
    step: Optional[int] = None,
    node: Optional[str] = None,
    fallback_to_input: bool = False,
) -> Tuple[Optional[str], Optional[int], Optional[Dict[str, Any]]]:
    """Return ``(file_path_or_None, step, operator_info)``.

    ``file_path`` is None when no candidate exists on disk. With
    ``fallback_to_input`` the operator's input (previous step) is returned when its
    output is not written yet, e.g. while the operator is still running.
    """
    operators_detail = output.get("operators_detail") or {}
    if step is None and node is None:
        step = default_step(output)

    _, info = find_operator(operators_detail, step=step, node=node)
    if info is not None and step is None:
        step = info.get("index")

    candidates = []
    if info and info.get("output_file"):
        candidates.append(info["output_file"])
    if step is not None:
        candidates.append(legacy_step_file(cache_root, task_id, step + 1))
    if fallback_to_input:
        if info and info.get("input_file"):
            candidates.append(info["input_file"])
        if step is not None and step > 0:
            candidates.append(legacy_step_file(cache_root, task_id, step))

    for path in candidates:
        if path and os.path.exists(path):
            return path, step, info
    return None, step, info
