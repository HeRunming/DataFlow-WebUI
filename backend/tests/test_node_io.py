"""Tests for node_io.py (pure functions, no dataflow import needed).

Tests cover:
- materialize_merge: key join, row-order fallback, column collision renaming
- normalize_node_input: detects and rewrites relative media paths
- node_output_file / node_cache_dir layout consistency
"""
import json
import os

import pytest

from app.services.node_io import (
    materialize_merge,
    node_cache_dir,
    node_output_file,
    normalize_node_input,
    read_jsonl_as_records,
    write_jsonl,
)


def _write(path, rows):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    path = str(path)
    with open(path, "w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row) + "\n")
    return path


def test_merge_key_join(tmp_path):
    a = _write(str(tmp_path / "a.jsonl"), [{"id": 1, "cap": "cat"}, {"id": 2, "cap": "dog"}])
    b = _write(str(tmp_path / "b.jsonl"), [{"id": 1, "text": "hi"}, {"id": 2, "text": "bye"}])
    out = str(tmp_path / "merged.jsonl")
    records, logs = materialize_merge([a, b], ["n1", "n2"], out, merge_key="id")
    assert [r["id"] for r in records] == [1, 2]
    assert records[0]["cap"] == "cat" and records[0]["text"] == "hi"
    assert records[1]["cap"] == "dog" and records[1]["text"] == "bye"
    assert any("[MERGE]" in lg for lg in logs)


def test_merge_key_join_auto_id(tmp_path):
    """When merge_key is None and every branch has 'id', it should join on 'id'."""
    a = _write(str(tmp_path / "a.jsonl"), [{"id": "x", "v": 1}])
    b = _write(str(tmp_path / "b.jsonl"), [{"id": "x", "w": 2}])
    out = str(tmp_path / "m.jsonl")
    records, _ = materialize_merge([a, b], ["n1", "n2"], out, merge_key=None)
    assert records[0]["v"] == 1 and records[0]["w"] == 2


def test_merge_row_order_fallback(tmp_path):
    a = _write(str(tmp_path / "a.jsonl"), [{"text": "a1"}, {"text": "a2"}])
    b = _write(str(tmp_path / "b.jsonl"), [{"audio": "b1"}, {"audio": "b2"}])
    out = str(tmp_path / "m.jsonl")
    records, logs = materialize_merge([a, b], ["n1", "n2"], out, merge_key=None)
    assert records[0] == {"text": "a1", "audio": "b1"}
    assert records[1] == {"text": "a2", "audio": "b2"}
    assert any("row order" in lg for lg in logs)


def test_merge_row_order_truncates_to_shortest(tmp_path):
    a = _write(str(tmp_path / "a.jsonl"), [{"x": 1}, {"x": 2}, {"x": 3}])
    b = _write(str(tmp_path / "b.jsonl"), [{"y": 1}])
    out = str(tmp_path / "m.jsonl")
    records, _ = materialize_merge([a, b], ["n1", "n2"], out, merge_key=None)
    assert len(records) == 1


def test_merge_column_collision_renamed(tmp_path):
    a = _write(str(tmp_path / "a.jsonl"), [{"id": 1, "score": 0.9}])
    b = _write(str(tmp_path / "b.jsonl"), [{"id": 1, "score": 0.7, "extra": "e"}])
    out = str(tmp_path / "m.jsonl")
    records, logs = materialize_merge([a, b], ["cap", "asr"], out, merge_key="id")
    assert records[0]["score"] == 0.9          # kept from first branch
    assert records[0]["score__asr"] == 0.7     # renamed collision
    assert records[0]["extra"] == "e"
    assert any("renamed" in lg for lg in logs)


def test_merge_missing_key_skipped(tmp_path):
    # branch b has id:1 but not id:2. The primary branch has both.
    # Left-join behaviour: id:2 keeps primary fields, no secondary fields, no WARN.
    # If a secondary key is absent from the primary, THAT triggers a WARN (see next case).
    a = _write(str(tmp_path / "a.jsonl"), [{"id": 1, "x": "a"}, {"id": 2, "x": "b"}])
    b = _write(str(tmp_path / "b.jsonl"), [{"id": 1, "y": "z"}])  # id:2 absent in secondary
    out = str(tmp_path / "m.jsonl")
    records, logs = materialize_merge([a, b], ["n1", "n2"], out, merge_key="id")
    # id:2 is kept from primary with x="b" but y is absent (left-join)
    ids = [r["id"] for r in records]
    assert 1 in ids and 2 in ids
    assert records[0]["y"] == "z"  # id:1 got y from branch b
    # No WARN expected here — id:2 just had no matching secondary row
    assert not any("WARN" in lg for lg in logs), f"Unexpected WARN: {logs}"


def test_merge_secondary_key_not_in_primary_warns(tmp_path):
    # A key in the secondary branch that is NOT in the primary → WARN
    a = _write(str(tmp_path / "a.jsonl"), [{"id": 1, "x": "a"}])
    b = _write(str(tmp_path / "b.jsonl"), [{"id": 99, "y": "z"}])  # 99 not in primary
    out = str(tmp_path / "m.jsonl")
    records, logs = materialize_merge([a, b], ["n1", "n2"], out, merge_key="id")
    assert any("WARN" in lg for lg in logs)


def test_normalize_node_input_rewrites_relative(tmp_path):
    media = tmp_path / "images"
    media.mkdir()
    (media / "cat.png").write_bytes(b"x")
    rows = [{"image": "images/cat.png", "text": "hello"}, {"image": "/abs/other.png", "text": "world"}]
    src = _write(str(tmp_path / "src.jsonl"), rows)
    dst = str(tmp_path / "norm.jsonl")
    path_used, changed = normalize_node_input(src, ["image"], str(tmp_path), dst)
    assert changed == 1
    assert path_used == dst
    norm_rows = read_jsonl_as_records(dst)
    assert norm_rows[0]["image"] == str(tmp_path / "images" / "cat.png")
    assert norm_rows[1]["image"] == "/abs/other.png"


def test_normalize_node_input_no_change_returns_original(tmp_path):
    rows = [{"image": "/abs/a.png", "text": "hi"}]
    src = _write(str(tmp_path / "src.jsonl"), rows)
    dst = str(tmp_path / "norm.jsonl")
    path_used, changed = normalize_node_input(src, ["image"], str(tmp_path), dst)
    assert changed == 0
    assert path_used == src


def test_node_output_file_layout(tmp_path):
    """node_output_file must be inside node_cache_dir and match FileStorage naming."""
    cache_root, task_id, node_id = str(tmp_path), "abc123", "cap"
    expected = os.path.join(node_cache_dir(cache_root, task_id, node_id), "dataflow_cache_step_step1.jsonl")
    assert node_output_file(cache_root, task_id, node_id) == expected
    assert node_output_file(cache_root, task_id, node_id).startswith(
        node_cache_dir(cache_root, task_id, node_id)
    )
