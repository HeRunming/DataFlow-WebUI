"""End-to-end test for the mm_stubs multimodal operator package.

Skipped unless dataflow can be imported (the installed open-dataflow package and
the mm_stubs extension must both be available). In CI this requires running
``./install.sh --profile harness`` first; in the dev environment described in
memory/mm-branch-env.md the venv at /data/hrm/.venv-mm provides it.

The test exercises:
- Extension loading via load_dataflow_extensions() / _DATAFLOW_EXTENSIONS = ["mm_stubs"]
- All four stub operators register in OPERATOR_REGISTRY
- ImageCaptionStub + AudioTranscribeStub fan-out → merge → ImageGenStub via DataFlowEngine
- Merged output record contains both caption and transcript fields
- ImageGenStub writes a real PNG file and stores its absolute path
- operators_detail carries node_id, output_file, and conversion_rate
"""
import json
import os
import sys
from types import SimpleNamespace

import pytest

# Skip the whole module if dataflow is not importable.
dataflow = pytest.importorskip("dataflow", reason="open-dataflow not installed")


@pytest.fixture(autouse=True)
def _load_stubs():
    """Ensure mm_stubs are loaded into OPERATOR_REGISTRY for this test."""
    from app.core.extension_loader import load_dataflow_extensions
    load_dataflow_extensions()


def _make_dataset(tmp_path):
    media = tmp_path / "media"
    media.mkdir()
    png_bytes = (
        b"\x89PNG\r\n\x1a\n"
        b"\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x02\x00\x00\x00\x90wS\xde"
        b"\x00\x00\x00\x0cIDATx\x9cc\xf8\x0f\x00\x00\x01\x01\x00\x05\x18\xd8N"
        b"\x00\x00\x00\x00IEND\xaeB`\x82"
    )
    wav_bytes = b"RIFFfake"
    for i in range(3):
        (media / f"img{i}.png").write_bytes(png_bytes)
        (media / f"aud{i}.wav").write_bytes(wav_bytes)

    ds_file = tmp_path / "mm_demo.jsonl"
    rows = [
        {
            "id": i,
            "image_path": str(media / f"img{i}.png"),
            "audio_path": str(media / f"aud{i}.wav"),
            "prompt": f"prompt {i}",
            "text": f"text {i}",
        }
        for i in range(3)
    ]
    ds_file.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    return ds_file


def test_mm_stubs_registered():
    """All four stub operators appear in OPERATOR_REGISTRY after extension loading."""
    from dataflow.utils.registry import OPERATOR_REGISTRY
    registry_keys = set(OPERATOR_REGISTRY.get_obj_map().keys())
    for name in ("ImageCaptionStub", "ImageGenStub", "AudioTranscribeStub", "FieldPresentFilterStub"):
        assert name in registry_keys, f"{name} not in OPERATOR_REGISTRY"


def test_mm_stubs_have_bilingual_desc():
    from dataflow.utils.registry import OPERATOR_REGISTRY
    for name in ("ImageCaptionStub", "AudioTranscribeStub", "ImageGenStub", "FieldPresentFilterStub"):
        cls = OPERATOR_REGISTRY.get(name)
        assert cls is not None
        zh = cls.get_desc("zh")
        en = cls.get_desc("en")
        assert zh and "Stub" in zh, f"{name}.get_desc('zh') missing or no Stub marker"
        assert en and "Stub" in en, f"{name}.get_desc('en') missing or no Stub marker"


def test_fanout_merge_imagegen_e2e(tmp_path, monkeypatch):
    """Fan-out (cap, asr) → merge → gen pipeline runs end-to-end via DataFlowEngine."""
    import dataflow as _df  # noqa: F401 ensure importable

    ds_file = _make_dataset(tmp_path)
    task_id = "mm_e2e_test"
    task_dir = tmp_path / "cache"

    # The engine does os.chdir(DATAFLOW_CORE_DIR/api_pipelines) around operator.run().
    # Create that directory so the chdir succeeds (stub operators don't care about cwd).
    api_pipelines_dir = tmp_path / "dataflow_core" / "api_pipelines"
    api_pipelines_dir.mkdir(parents=True)

    # --- set up container registries ----------------------------------------
    from app.core.container import container
    from app.services.dataset_registry import DatasetRegistry
    from app.services.task_registry import TaskRegistry
    import app.core.config as _cfg

    original_ds_reg = container.dataset_registry
    original_task_reg = container.task_registry

    monkeypatch.setattr(_cfg.settings, "CACHE_DIR", str(task_dir), raising=False)
    monkeypatch.setattr(_cfg.settings, "DATAFLOW_CORE_DIR", str(tmp_path / "dataflow_core"), raising=False)
    monkeypatch.setattr(_cfg.settings, "BASE_DIR", str(tmp_path), raising=False)

    ds_reg = DatasetRegistry(path=str(tmp_path / "ds.yaml"), scan=False)
    ds = ds_reg.add_or_update({
        "name": "mm_demo", "root": str(ds_file), "pipeline": "test", "meta": {}
    })

    task_reg = TaskRegistry(path=str(tmp_path / "tasks.json"))
    container.dataset_registry = ds_reg
    container.task_registry = task_reg

    try:
        from app.services.dataflow_engine import DataFlowEngine
        engine = DataFlowEngine()

        pipeline_config = {
            "file_path": "",
            "input_dataset": {"id": ds["id"]},
            "operators": [
                {
                    "id": "cap", "name": "ImageCaptionStub",
                    "params": {
                        "init": [],
                        "run": [
                            {"name": "input_image_key", "value": "image_path"},
                            {"name": "output_key", "value": "caption"},
                        ],
                    },
                },
                {
                    "id": "asr", "name": "AudioTranscribeStub",
                    "params": {
                        "init": [],
                        "run": [
                            {"name": "input_audio_key", "value": "audio_path"},
                            {"name": "output_key", "value": "transcript"},
                        ],
                    },
                },
                {
                    "id": "gen", "name": "ImageGenStub",
                    "params": {
                        "init": [],
                        "run": [
                            {"name": "input_key", "value": "caption"},
                            {"name": "output_key", "value": "generated_image_path"},
                        ],
                    },
                },
            ],
            "edges": [
                {"source": "cap", "target": "gen"},
                {"source": "asr", "target": "gen"},
            ],
        }

        result = engine.run(
            pipeline_config=pipeline_config,
            task_id=task_id,
        )
    finally:
        container.dataset_registry = original_ds_reg
        container.task_registry = original_task_reg

    assert result["status"] == "completed", result.get("output", {}).get("error")

    ops_detail = result["output"]["operators_detail"]

    # Each node recorded its id and output file
    for node_id in ("cap", "asr", "gen"):
        node_entry = next((v for v in ops_detail.values() if v.get("node_id") == node_id), None)
        assert node_entry is not None, f"node_id '{node_id}' missing from operators_detail"
        assert node_entry["status"] == "completed"
        assert "output_file" in node_entry
        assert os.path.exists(node_entry["output_file"]), (
            f"output_file for '{node_id}' does not exist: {node_entry['output_file']}"
        )
        if node_entry.get("input_count"):
            assert "conversion_rate" in node_entry

    # gen output records contain both caption and transcript after merge
    gen_entry = next(v for v in ops_detail.values() if v.get("node_id") == "gen")
    with open(gen_entry["output_file"], encoding="utf-8") as f:
        gen_records = [json.loads(line) for line in f if line.strip()]

    assert len(gen_records) == 3
    for rec in gen_records:
        assert "caption" in rec, f"caption missing from gen output: {rec}"
        assert "transcript" in rec, f"transcript missing from gen output: {rec}"
        assert rec["caption"].startswith("[stub caption for")
        assert rec["transcript"].startswith("[stub transcript of")
        # ImageGenStub writes a real PNG at an absolute path
        gen_path = rec.get("generated_image_path")
        assert gen_path and os.path.isabs(gen_path), f"non-absolute gen path: {gen_path}"
        assert os.path.exists(gen_path), f"generated PNG missing: {gen_path}"
        assert open(gen_path, "rb").read(4) == b"\x89PNG", "generated file is not a PNG"
