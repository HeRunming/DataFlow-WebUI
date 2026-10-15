"""DAG-aware and media-aware pipeline validation."""
import json

import pytest

from app.core.container import container
from app.services.dataset_registry import DatasetRegistry
from app.services.pipeline_registry import PipelineRegistry


def _run_op(name, run=None, **extra):
    return {"name": name, "params": {"init": [], "run": [{"name": k, "value": v} for k, v in (run or {}).items()]}, **extra}


@pytest.fixture
def env(tmp_path):
    media = tmp_path / "media"
    media.mkdir()
    (media / "1.png").write_bytes(b"x")
    (media / "1.wav").write_bytes(b"x")
    rows = [
        {"id": 1, "image": "media/1.png", "audio": "media/1.wav", "text": "t1"},
        {"id": 2, "image": "media/2.png", "audio": "media/1.wav", "text": "t2"},
    ]
    data = tmp_path / "mm.jsonl"
    data.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")

    def details(run_names):
        return {
            "allowed_prompts": [],
            "parameter": {
                "init": [],
                "run": [{"name": "storage", "kind": "POSITIONAL_OR_KEYWORD"}]
                + [{"name": n, "kind": "POSITIONAL_OR_KEYWORD"} for n in run_names],
            },
        }

    ops = {
        "Caption": details(["input_image_key", "output_key"]),
        "Asr": details(["input_audio_key", "output_key"]),
        "Fuse": details(["input_caption_key", "input_transcript_key", "output_key"]),
        "TextGen": details(["input_key", "output_key"]),
    }

    class Ops:
        def get_op_details(self, name, lang="zh"):
            return {"name": name, **ops[name]} if name in ops else None

    originals = (container.dataset_registry, container.operator_registry, container.serving_registry)
    container.dataset_registry = DatasetRegistry(path=str(tmp_path / "ds.yaml"), scan=False)
    ds = container.dataset_registry.add_or_update({"name": "mm", "root": str(data), "pipeline": "t", "meta": {}})
    container.operator_registry = Ops()
    container.serving_registry = None
    registry = PipelineRegistry(path=str(tmp_path / "p.json"))
    registry._write({"pipelines": {}})
    try:
        yield registry, ds["id"], media
    finally:
        container.dataset_registry, container.operator_registry, container.serving_registry = originals


def _dag_config(ds_id, caption_field="image"):
    return {
        "file_path": "",
        "input_dataset": {"id": ds_id},
        "operators": [
            _run_op("Caption", {"input_image_key": caption_field, "output_key": "caption"}, id="cap"),
            _run_op("Asr", {"input_audio_key": "audio", "output_key": "transcript"}, id="asr"),
            _run_op("Fuse", {"input_caption_key": "caption", "input_transcript_key": "transcript", "output_key": "fused"}, id="fuse"),
        ],
        "edges": [{"source": "cap", "target": "fuse"}, {"source": "asr", "target": "fuse"}],
    }


def test_merge_node_sees_fields_from_both_branches(env, tmp_path):
    registry, ds_id, media = env
    (media / "2.png").write_bytes(b"x")
    result = registry.validate_pipeline_config(_dag_config(ds_id))
    assert result.valid, [e.message for e in result.errors]
    assert result.execution_order == ["cap", "asr", "fuse"]
    assert {"caption", "transcript", "fused"} <= set(result.final_available_fields)
    assert result.field_modalities["image"] == "image"
    assert result.field_modalities["audio"] == "audio"


def test_parallel_branch_fields_are_not_visible_to_sibling(env):
    registry, ds_id, media = env
    (media / "2.png").write_bytes(b"x")
    config = _dag_config(ds_id)
    # Asr is a sibling of Caption, so it cannot read Caption's output.
    config["operators"][1] = _run_op("Asr", {"input_audio_key": "caption", "output_key": "transcript"}, id="asr")
    result = registry.validate_pipeline_config(config)
    issue = next(e for e in result.errors if e.code == "missing_input_field")
    assert issue.node_id == "asr" and issue.field_name == "caption"


def test_missing_media_file_is_an_error(env):
    registry, ds_id, _ = env  # media/2.png does not exist
    result = registry.validate_pipeline_config(_dag_config(ds_id))
    issue = next(e for e in result.errors if e.code == "media_file_not_found")
    assert issue.field_name == "image" and issue.node_id == "cap"
    assert "media/2.png" in issue.message


def test_media_param_bound_to_text_column_warns(env):
    registry, ds_id, media = env
    (media / "2.png").write_bytes(b"x")
    result = registry.validate_pipeline_config(_dag_config(ds_id, caption_field="text"))
    issue = next(w for w in result.warnings if w.code == "media_modality_mismatch")
    assert issue.suggested_fields == ["image"]


def test_cycle_is_reported(env):
    registry, ds_id, media = env
    (media / "2.png").write_bytes(b"x")
    config = _dag_config(ds_id)
    config["edges"].append({"source": "fuse", "target": "cap"})
    result = registry.validate_pipeline_config(config)
    assert not result.valid
    assert any(e.code == "dag_cycle" for e in result.errors)


def test_legacy_chain_unchanged(env):
    registry, ds_id, _ = env
    config = {
        "file_path": "",
        "input_dataset": {"id": ds_id},
        "operators": [
            _run_op("TextGen", {"input_key": "text", "output_key": "a"}),
            _run_op("TextGen", {"input_key": "a", "output_key": "b"}),
        ],
    }
    result = registry.validate_pipeline_config(config)
    assert result.valid
    assert result.execution_order == ["op_0", "op_1"]
    assert result.final_available_fields[-2:] == ["a", "b"]


def test_update_pipeline_persists_edges_and_ids(env):
    registry, ds_id, media = env
    (media / "2.png").write_bytes(b"x")
    created = registry.create_pipeline({"name": "dag", "config": _dag_config(ds_id)})
    assert created["config"]["edges"][0] == {"source": "cap", "target": "fuse"}
    assert [op["id"] for op in created["config"]["operators"]] == ["cap", "asr", "fuse"]

    new_config = _dag_config(ds_id)
    new_config["edges"] = [{"source": "cap", "target": "fuse"}]
    # Dropping asr→fuse hides `transcript` from Fuse: the update is rejected.
    with pytest.raises(ValueError, match="transcript"):
        registry.update_pipeline(created["id"], {"config": new_config})

    new_config["edges"] = [{"source": "asr", "target": "fuse"}, {"source": "cap", "target": "fuse"}]
    updated = registry.update_pipeline(created["id"], {"config": new_config})
    assert updated["config"]["edges"][0] == {"source": "asr", "target": "fuse"}

    # Clearing edges returns to legacy chain semantics (cap → asr → fuse).
    updated = registry.update_pipeline(created["id"], {"config": {**new_config, "edges": None}})
    assert "edges" not in updated["config"]
