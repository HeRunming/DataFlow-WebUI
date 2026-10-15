from app.services.execution_graph import (
    build_graph,
    infer_chain_edges,
    merge_fields,
    resolve_merge_key,
)


def _ops(*names, ids=None):
    ids = ids or [None] * len(names)
    return [{"name": n, **({"id": i} if i else {})} for n, i in zip(names, ids)]


def test_legacy_config_is_list_order_chain():
    graph = build_graph({"operators": _ops("A", "B", "C")})
    assert graph.explicit_edges is False
    assert graph.valid
    assert graph.order == ["op_0", "op_1", "op_2"]
    assert graph.nodes["op_1"].preds == ["op_0"]
    assert graph.roots() == ["op_0"] and graph.leaves() == ["op_2"]
    assert infer_chain_edges(_ops("A", "B")) == [{"source": "op_0", "target": "op_1"}]


def test_fan_out_and_merge():
    config = {
        "operators": _ops("Cap", "Asr", "Fuse", "Gen", ids=["cap", "asr", "fuse", "gen"]),
        "edges": [
            {"source": "cap", "target": "fuse"},
            {"source": "asr", "target": "fuse"},
            {"source": "fuse", "target": "gen"},
        ],
    }
    graph = build_graph(config)
    assert graph.valid, graph.issues
    assert graph.order == ["cap", "asr", "fuse", "gen"]
    assert graph.roots() == ["cap", "asr"]
    assert graph.nodes["fuse"].preds == ["cap", "asr"]
    assert sorted(graph.ancestors("gen")) == ["asr", "cap", "fuse"]
    assert graph.paths() == [["cap", "fuse", "gen"], ["asr", "fuse", "gen"]]


def test_empty_edges_means_independent_roots():
    graph = build_graph({"operators": _ops("A", "B"), "edges": []})
    assert graph.explicit_edges is True
    assert graph.roots() == ["op_0", "op_1"]


def test_list_order_violating_dependencies_is_reordered_with_warning():
    config = {
        "operators": _ops("B", "A", ids=["b", "a"]),
        "edges": [{"source": "a", "target": "b"}],
    }
    graph = build_graph(config)
    assert graph.valid
    assert graph.order == ["a", "b"]
    assert [i.code for i in graph.issues] == ["dag_reordered"]


def test_cycle_unknown_endpoint_self_loop_duplicate_id():
    cyc = build_graph({
        "operators": _ops("A", "B", ids=["a", "b"]),
        "edges": [{"source": "a", "target": "b"}, {"source": "b", "target": "a"}],
    })
    assert not cyc.valid and cyc.order == []
    assert any(i.code == "dag_cycle" for i in cyc.issues)

    bad = build_graph({
        "operators": _ops("A", "A", ids=["a", "a"]),
        "edges": [{"source": "a", "target": "zzz"}, {"source": "a", "target": "a"}],
    })
    codes = {i.code for i in bad.issues}
    assert {"dag_duplicate_node_id", "dag_unknown_edge_endpoint", "dag_self_loop"} <= codes


def test_merge_key_warning_and_resolution():
    graph = build_graph({"operators": [{"name": "A", "merge_key": "uid"}]})
    assert [i.code for i in graph.issues] == ["merge_key_without_merge"]
    assert resolve_merge_key("uid", [["a"], ["b"]]) == "uid"
    assert resolve_merge_key(None, [["id", "x"], ["id", "y"]]) == "id"
    assert resolve_merge_key(None, [["id", "x"], ["y"]]) is None
    assert merge_fields([["id", "x"], ["id", "y"]]) == ["id", "x", "y"]
