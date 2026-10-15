"""Pipeline execution graph (串并联 / DAG support).

A pipeline config stays ``{input_dataset, operators: [...]}``. Two optional, purely
additive fields turn it into a DAG:

- ``operators[].id``  — stable node id (default ``op_{index}``)
- ``edges: [{"source": <node id>, "target": <node id>}]`` — operator → operator

Semantics:

- ``edges`` absent / ``None``  → legacy chain: operator ``i`` consumes operator ``i-1``.
  Every config saved before DAG support (and every agent-generated config that
  omits edges) therefore behaves exactly as before.
- ``edges`` present (even ``[]``) → explicit graph. A node with no predecessor reads
  the input dataset; a node with several predecessors reads their outputs merged
  (see ``node_io.materialize_merge``) on ``operators[].merge_key`` (default ``id``
  when every branch has it, else row order).

Pure functions only (no DataFlow import) so validation, executors and tests share it.
"""
from __future__ import annotations

import heapq
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence

DEFAULT_MERGE_KEY = "id"


@dataclass
class GraphNode:
    id: str
    index: int  # position in config["operators"]
    name: str
    preds: List[str] = field(default_factory=list)
    succs: List[str] = field(default_factory=list)
    merge_key: Optional[str] = None


@dataclass
class GraphIssue:
    level: str  # "error" | "warning"
    code: str
    message: str
    node_id: Optional[str] = None
    operator_index: Optional[int] = None
    operator_name: Optional[str] = None


@dataclass
class ExecutionGraph:
    nodes: Dict[str, GraphNode]
    order: List[str]  # topological order; [] when the graph is invalid
    explicit_edges: bool
    issues: List[GraphIssue] = field(default_factory=list)

    @property
    def valid(self) -> bool:
        return not any(issue.level == "error" for issue in self.issues)

    def by_index(self, index: int) -> Optional[GraphNode]:
        for node in self.nodes.values():
            if node.index == index:
                return node
        return None

    def roots(self) -> List[str]:
        return [nid for nid in self.order if not self.nodes[nid].preds]

    def leaves(self) -> List[str]:
        return [nid for nid in self.order if not self.nodes[nid].succs]

    def ancestors(self, node_id: str) -> List[str]:
        seen: List[str] = []
        stack = list(self.nodes[node_id].preds)
        while stack:
            nid = stack.pop()
            if nid in seen:
                continue
            seen.append(nid)
            stack.extend(self.nodes[nid].preds)
        return seen

    def paths(self, limit: int = 64) -> List[List[str]]:
        """Root→leaf paths (bounded; a DAG can have exponentially many)."""
        out: List[List[str]] = []

        def walk(nid: str, prefix: List[str]):
            if len(out) >= limit:
                return
            path = prefix + [nid]
            succs = self.nodes[nid].succs
            if not succs:
                out.append(path)
                return
            for succ in succs:
                walk(succ, path)

        for root in self.roots():
            walk(root, [])
        return out


def node_id_for(op: Dict[str, Any], index: int) -> str:
    raw = op.get("id") if isinstance(op, dict) else None
    return str(raw) if raw not in (None, "") else f"op_{index}"


def infer_chain_edges(operators: Sequence[Dict[str, Any]]) -> List[Dict[str, str]]:
    ids = [node_id_for(op, i) for i, op in enumerate(operators)]
    return [{"source": ids[i - 1], "target": ids[i]} for i in range(1, len(ids))]


def has_explicit_edges(config: Dict[str, Any]) -> bool:
    return isinstance(config, dict) and config.get("edges") is not None


def build_graph(config: Dict[str, Any]) -> ExecutionGraph:
    operators = list((config or {}).get("operators") or [])
    explicit = has_explicit_edges(config)
    issues: List[GraphIssue] = []
    nodes: Dict[str, GraphNode] = {}

    for idx, op in enumerate(operators):
        op = op if isinstance(op, dict) else {}
        nid = node_id_for(op, idx)
        name = op.get("name") or f"operator_{idx}"
        if nid in nodes:
            issues.append(GraphIssue(
                "error", "dag_duplicate_node_id",
                f"算子节点 id '{nid}' 重复（operators[{nodes[nid].index}] 与 operators[{idx}]）。每个算子的 id 必须唯一。",
                node_id=nid, operator_index=idx, operator_name=name,
            ))
            continue
        merge_key = op.get("merge_key")
        nodes[nid] = GraphNode(id=nid, index=idx, name=name, merge_key=merge_key or None)

    edges = config.get("edges") if explicit else infer_chain_edges(operators)
    if explicit and not isinstance(edges, list):
        issues.append(GraphIssue("error", "dag_invalid_edges", "edges 必须是 [{source, target}] 列表。"))
        edges = []

    seen_edges = set()
    for edge in edges:
        source = edge.get("source") if isinstance(edge, dict) else None
        target = edge.get("target") if isinstance(edge, dict) else None
        source = str(source) if source is not None else None
        target = str(target) if target is not None else None
        missing = [end for end in (source, target) if end not in nodes]
        if missing:
            issues.append(GraphIssue(
                "error", "dag_unknown_edge_endpoint",
                f"边 {source!r} → {target!r} 引用了不存在的算子节点 {missing}。可用节点：{list(nodes)}。",
                node_id=target if target in nodes else None,
            ))
            continue
        if source == target:
            issues.append(GraphIssue(
                "error", "dag_self_loop", f"节点 '{source}' 存在自环边。",
                node_id=source, operator_index=nodes[source].index, operator_name=nodes[source].name,
            ))
            continue
        if (source, target) in seen_edges:
            continue
        seen_edges.add((source, target))
        nodes[source].succs.append(target)
        nodes[target].preds.append(source)

    # Kahn's algorithm, ties broken by list position so a chain keeps list order and
    # independent branches run in the order the user listed them.
    indegree = {nid: len(node.preds) for nid, node in nodes.items()}
    heap = [(node.index, nid) for nid, node in nodes.items() if indegree[nid] == 0]
    heapq.heapify(heap)
    order: List[str] = []
    while heap:
        _, nid = heapq.heappop(heap)
        order.append(nid)
        for succ in nodes[nid].succs:
            indegree[succ] -= 1
            if indegree[succ] == 0:
                heapq.heappush(heap, (nodes[succ].index, succ))

    if len(order) != len(nodes):
        cyclic = [nid for nid in nodes if nid not in order]
        issues.append(GraphIssue(
            "error", "dag_cycle",
            f"算子图存在环，无法确定执行顺序。涉及节点：{cyclic}。",
            node_id=cyclic[0] if cyclic else None,
        ))
        order = []
    elif explicit and [nodes[nid].index for nid in order] != sorted(n.index for n in nodes.values()):
        issues.append(GraphIssue(
            "warning", "dag_reordered",
            "operators 列表顺序与边的依赖顺序不一致，执行时按拓扑序运行："
            + " → ".join(order),
        ))

    for node in nodes.values():
        if node.merge_key and len(node.preds) < 2:
            issues.append(GraphIssue(
                "warning", "merge_key_without_merge",
                f"节点 '{node.id}' 设置了 merge_key='{node.merge_key}'，但它只有 {len(node.preds)} 个上游，merge_key 不会生效。",
                node_id=node.id, operator_index=node.index, operator_name=node.name,
            ))

    return ExecutionGraph(nodes=nodes, order=order, explicit_edges=explicit, issues=issues)


def resolve_merge_key(merge_key: Optional[str], branch_fields: Sequence[Sequence[str]]) -> Optional[str]:
    """Join key used to merge branches: explicit merge_key, else ``id`` when every
    branch has it, else None (row-order alignment)."""
    if merge_key:
        return merge_key
    if branch_fields and all(DEFAULT_MERGE_KEY in fields for fields in branch_fields):
        return DEFAULT_MERGE_KEY
    return None


def conflict_column_name(column: str, branch_node_id: str) -> str:
    """Name a differing shared column from a non-first branch gets after a merge."""
    return f"{column}__{branch_node_id}"


def merge_fields(branch_fields: Sequence[Sequence[str]]) -> List[str]:
    """Field set visible after merging branches (static view: names are kept; differing
    values of shared columns additionally appear suffixed, see conflict_column_name)."""
    merged: List[str] = []
    for fields in branch_fields:
        for name in fields:
            if name not in merged:
                merged.append(name)
    return merged
