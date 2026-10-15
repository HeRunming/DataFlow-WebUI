"""Node-level storage construction and merge materialisation for the DAG executor.

Each operator node in a DAG pipeline gets its own FileStorage anchored at:
  - the input dataset file (root nodes)
  - its single upstream's output file (chain nodes)
  - a merged file produced here (merge nodes with >1 predecessor)

The merge joins predecessor outputs on a configurable key column (defaults to
``id`` when every branch has it, else row-order alignment with a warning).
Colliding column names from non-first branches are renamed
``{column}__{branch_node_id}``.

Pure functions only (no DataFlow import) so they can be tested without torch.
The FileStorage import is guarded behind a TYPE_CHECKING block; callers pass the
class directly when needed.
"""
from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Optional, Sequence, Tuple

from app.core.logger_setup import get_logger
from app.services.execution_graph import (
    DEFAULT_MERGE_KEY,
    ExecutionGraph,
    GraphNode,
    conflict_column_name,
    resolve_merge_key,
)
from app.services import media_assets

logger = get_logger(__name__)

CACHE_FILE_TYPE = "jsonl"
CACHE_FILE_PREFIX = "dataflow_cache_step"


def node_cache_dir(cache_root: str, task_id: str, node_id: str) -> str:
    return os.path.join(cache_root, f"{task_id}_output", "nodes", node_id)


def node_output_file(cache_root: str, task_id: str, node_id: str) -> str:
    """Absolute path where the node's operator will write its output.

    ``FileStorage`` writes to ``{cache_path}/{prefix}_step{step}.{type}``
    for ``step = operator_step + 1 = 0 + 1 = 1`` (each node gets a fresh
    FileStorage with ``operator_step = -1``).
    """
    return os.path.join(
        node_cache_dir(cache_root, task_id, node_id),
        f"{CACHE_FILE_PREFIX}_step1.{CACHE_FILE_TYPE}",
    )


def read_jsonl_as_records(path: str) -> List[Dict[str, Any]]:
    records: List[Dict[str, Any]] = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            if line.strip():
                records.append(json.loads(line))
    return records


def write_jsonl(path: str, records: List[Dict[str, Any]]) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for record in records:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")


def materialize_merge(
    branch_paths: List[str],
    branch_node_ids: List[str],
    output_path: str,
    merge_key: Optional[str],
) -> Tuple[List[Dict[str, Any]], List[str]]:
    """Join branch outputs and write a merged jsonl file.

    Returns ``(merged_records, log_lines)``.

    Join strategy: if ``merge_key`` is not None and the key exists in every
    branch, the first branch is the left side and subsequent branches are
    left-joined on the key.  Otherwise, rows are aligned by position (shortest
    branch wins, with a warning).  Colliding column names from non-first
    branches are renamed ``{col}__{branch_node_id}``.
    """
    branches: List[List[Dict[str, Any]]] = [read_jsonl_as_records(p) for p in branch_paths]
    logs: List[str] = []

    if not any(branches):
        write_jsonl(output_path, [])
        return [], logs

    # Determine all column names per branch
    branch_cols: List[List[str]] = []
    for b in branches:
        seen: List[str] = []
        for record in b:
            for k in record:
                if k not in seen:
                    seen.append(k)
        branch_cols.append(seen)

    actual_key = resolve_merge_key(merge_key, branch_cols)
    if actual_key:
        logs.append(f"[MERGE] joining {len(branches)} branches on key='{actual_key}' (rows: {[len(b) for b in branches]})")
        # Build index from first branch
        index: Dict[Any, Dict[str, Any]] = {r.get(actual_key): dict(r) for r in branches[0]}
        for branch_idx, (branch, node_id) in enumerate(zip(branches[1:], branch_node_ids[1:]), 1):
            cols = branch_cols[branch_idx]
            base_cols = branch_cols[0]
            for col in cols[:branch_idx]:
                _ = col  # already handled
            for record in branch:
                key_val = record.get(actual_key)
                if key_val not in index:
                    logs.append(f"[MERGE] WARN key {key_val!r} from branch '{node_id}' not in primary branch — row skipped")
                    continue
                for col, value in record.items():
                    if col == actual_key:
                        continue
                    dest = conflict_column_name(col, node_id) if col in base_cols else col
                    index[key_val][dest] = value
        merged = list(index.values())
    else:
        # Row-order alignment
        lengths = [len(b) for b in branches]
        if len(set(lengths)) > 1:
            logs.append(
                f"[MERGE] WARN no common key found; aligning by row order — branch lengths differ: {list(zip(branch_node_ids, lengths))}; using min={min(lengths)}"
            )
        n = min(lengths)
        merged = []
        for i in range(n):
            row = dict(branches[0][i])
            for branch_idx, (branch, node_id) in enumerate(zip(branches[1:], branch_node_ids[1:]), 1):
                for col, value in branch[i].items():
                    dest = conflict_column_name(col, node_id) if col in branch_cols[0] else col
                    row[dest] = value
            merged.append(row)
        logs.append(f"[MERGE] aligned by row order ({n} rows, {len(branches)} branches)")

    # Log collision info
    all_cols: List[str] = []
    for record in (merged[:1] or [{}]):
        all_cols = list(record.keys())
    renamed = [c for c in all_cols if "__" in c]
    if renamed:
        logs.append(f"[MERGE] renamed colliding columns: {renamed}")

    write_jsonl(output_path, merged)
    return merged, logs


def normalize_node_input(
    input_file: str,
    media_fields: List[str],
    base_dir: str,
    normalized_path: str,
) -> Tuple[str, int]:
    """Return ``(path_to_use, changed_count)``.

    If no normalization is needed, returns the original ``input_file`` and 0.
    Otherwise writes a normalized copy to ``normalized_path`` and returns that.
    """
    if not media_fields:
        return input_file, 0
    try:
        records = read_jsonl_as_records(input_file)
        if not media_assets.needs_normalization(records, media_fields):
            return input_file, 0
        changed = media_assets.normalize_input_file(input_file, normalized_path, media_fields, base_dir)
        return normalized_path, changed
    except Exception as exc:
        logger.warning(f"Media path normalization failed for {input_file}: {exc} — proceeding with original file")
        return input_file, 0


def build_node_storage_class():
    """Import FileStorage lazily so this module can be imported in tests without torch."""
    from dataflow.utils.storage import FileStorage  # type: ignore[import]
    return FileStorage


def node_input_file(
    graph: ExecutionGraph,
    node: GraphNode,
    dataset_root: str,
    done_outputs: Dict[str, str],  # node_id -> absolute output file
    cache_root: str,
    task_id: str,
    media_fields: List[str],
    dataset_base_dir: str,
) -> Tuple[str, List[str]]:
    """Return ``(input_file, log_lines)``.

    For root nodes: the dataset file (with optional media normalization).
    For single-predecessor nodes: that predecessor's output file.
    For merge nodes: a materialized merged file under this node's cache dir.
    """
    logs: List[str] = []

    if not node.preds:
        # Root: reads input dataset; normalise media paths if needed.
        cache_dir = node_cache_dir(cache_root, task_id, node.id)
        os.makedirs(cache_dir, exist_ok=True)
        normalized_path = os.path.join(cache_dir, "input_normalized.jsonl")
        src, changed = normalize_node_input(dataset_root, media_fields, dataset_base_dir, normalized_path)
        if changed:
            logs.append(f"[MEDIA] normalized {changed} relative media paths in input for node '{node.id}'")
        return src, logs

    if len(node.preds) == 1:
        return done_outputs[node.preds[0]], logs

    # Merge node
    cache_dir = node_cache_dir(cache_root, task_id, node.id)
    os.makedirs(cache_dir, exist_ok=True)
    merged_path = os.path.join(cache_dir, "merged_input.jsonl")
    branch_paths = [done_outputs[pred] for pred in node.preds]
    _, merge_logs = materialize_merge(branch_paths, node.preds, merged_path, node.merge_key)
    logs.extend(merge_logs)
    return merged_path, logs


def build_node_storage(
    input_file: str,
    cache_root: str,
    task_id: str,
    node_id: str,
):
    """Construct a FileStorage anchored at ``input_file``, writing to per-node cache dir."""
    FileStorage = build_node_storage_class()
    cache_dir = node_cache_dir(cache_root, task_id, node_id)
    os.makedirs(cache_dir, exist_ok=True)
    return FileStorage(
        first_entry_file_name=input_file,
        cache_path=cache_dir,
        file_name_prefix=CACHE_FILE_PREFIX,
        cache_type=CACHE_FILE_TYPE,
    )
