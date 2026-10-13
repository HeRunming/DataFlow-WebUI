from app.services.task_outputs import find_operator, legacy_step_file, resolve_output_file


def _write(path, text="{}\n"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_legacy_layout_uses_task_subdir(tmp_path):
    # Regression: get_execution_result used to look in CACHE_DIR directly and
    # never found the file. Operator i writes step i+1 inside {task_id}_output/.
    out = tmp_path / "t1_output" / "dataflow_cache_step_step2.jsonl"
    _write(out)
    output = {
        "operators_detail": {
            "A_0": {"name": "A", "index": 0, "status": "completed"},
            "B_1": {"name": "B", "index": 1, "status": "completed"},
        },
        "execution_results": [{"index": 0}, {"index": 1}],
    }
    path, step, info = resolve_output_file(str(tmp_path), "t1", output)
    assert path == str(out)
    assert step == 1
    assert info["name"] == "B"
    assert legacy_step_file(str(tmp_path), "t1", 2) == str(out)


def test_recorded_output_file_and_node_lookup(tmp_path):
    out = tmp_path / "t1_output" / "nodes" / "caption" / "dataflow_cache_step_step1.jsonl"
    _write(out)
    output = {
        "operators_detail": {
            "Cap_0": {"name": "Cap", "index": 0, "node_id": "caption", "output_file": str(out)},
        }
    }
    path, step, info = resolve_output_file(str(tmp_path), "t1", output, node="caption")
    assert path == str(out)
    assert step == 0
    assert find_operator(output["operators_detail"], node="Cap_0")[1] is info


def test_fallback_to_input_while_running(tmp_path):
    inp = tmp_path / "t1_output" / "dataflow_cache_step_step1.jsonl"
    _write(inp)
    output = {
        "operators_detail": {
            "A_0": {"name": "A", "index": 0, "status": "completed"},
            "B_1": {"name": "B", "index": 1, "status": "running"},
        }
    }
    path, step, _ = resolve_output_file(str(tmp_path), "t1", output)
    assert path is None and step == 1
    path, _, _ = resolve_output_file(str(tmp_path), "t1", output, fallback_to_input=True)
    assert path == str(inp)
