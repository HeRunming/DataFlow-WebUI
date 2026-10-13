import json
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.v1.endpoints import media
from app.core.config import settings
from app.core.container import container
from app.services.dataset_registry import DatasetRegistry

PNG_BYTES = b"\x89PNG\r\n\x1a\nfake"


@pytest.fixture
def media_env(tmp_path, monkeypatch):
    media_dir = tmp_path / "media"
    media_dir.mkdir()
    (media_dir / "cat.png").write_bytes(PNG_BYTES)
    (media_dir / "talk.wav").write_bytes(b"RIFFfake")
    (tmp_path / "secret.txt").write_text("secret")

    data_file = tmp_path / "mm.jsonl"
    rows = [
        {"id": 1, "image": "media/cat.png", "audio": str(media_dir / "talk.wav"), "text": "a cat"},
        {"id": 2, "image": "media/../secret.txt", "audio": "media/missing.wav", "text": "b"},
        {"id": 3, "image": "https://example.com/x.png", "audio": "", "text": "c"},
    ]
    data_file.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")

    registry = DatasetRegistry(path=str(tmp_path / "registry.yaml"), scan=False)
    ds = registry.add_or_update({"name": "mm", "root": str(data_file), "pipeline": "test", "meta": {}})

    cache_root = tmp_path / "cache"
    task_dir = cache_root / "task1_output"
    task_dir.mkdir(parents=True)
    out_rows = [{"id": 1, "image": str(media_dir / "cat.png"), "caption": "a cat"}]
    (task_dir / "dataflow_cache_step_step1.jsonl").write_text(json.dumps(out_rows[0]) + "\n", encoding="utf-8")
    tasks = {
        "tasks": {
            "task1": {
                "pipeline_config": {"input_dataset": {"id": ds["id"]}},
                "output": {
                    "operators_detail": {"Captioner_0": {"name": "Captioner", "index": 0, "status": "completed"}},
                    "execution_results": [{"operator": "Captioner", "index": 0, "status": "completed"}],
                },
            }
        }
    }

    original = (container.dataset_registry, container.task_registry)
    container.dataset_registry = registry
    container.task_registry = SimpleNamespace(_read=lambda: tasks)
    monkeypatch.setattr(settings, "CACHE_DIR", str(cache_root))
    monkeypatch.setattr(settings, "MEDIA_ALLOWED_ROOTS", [])

    app = FastAPI()
    app.include_router(media.router, prefix="/api/v1")
    try:
        yield SimpleNamespace(client=TestClient(app), ds_id=ds["id"], media_dir=media_dir)
    finally:
        container.dataset_registry, container.task_registry = original


def test_field_modalities(media_env):
    res = media_env.client.get(f"/api/v1/datasets/{media_env.ds_id}/field_modalities")
    assert res.status_code == 200
    assert res.json()["data"] == {"id": "text", "image": "image", "audio": "audio", "text": "text"}


def test_dataset_media_relative_path_resolves_against_dataset_dir(media_env):
    res = media_env.client.get(f"/api/v1/datasets/{media_env.ds_id}/media", params={"field": "image", "row": 0})
    assert res.status_code == 200
    assert res.content == PNG_BYTES
    assert res.headers["content-type"] == "image/png"


def test_dataset_media_rejects_non_media_traversal(media_env):
    res = media_env.client.get(f"/api/v1/datasets/{media_env.ds_id}/media", params={"field": "image", "row": 1})
    assert res.status_code == 403


def test_dataset_media_missing_file_and_bad_field(media_env):
    client, ds_id = media_env.client, media_env.ds_id
    assert client.get(f"/api/v1/datasets/{ds_id}/media", params={"field": "audio", "row": 1}).status_code == 404
    assert client.get(f"/api/v1/datasets/{ds_id}/media", params={"field": "nope", "row": 0}).status_code == 404
    assert client.get(f"/api/v1/datasets/{ds_id}/media", params={"field": "image", "row": 99}).status_code == 404
    assert client.get("/api/v1/datasets/unknown/media", params={"field": "image"}).status_code == 404


def test_dataset_media_url_redirects(media_env):
    res = media_env.client.get(
        f"/api/v1/datasets/{media_env.ds_id}/media",
        params={"field": "image", "row": 2},
        follow_redirects=False,
    )
    assert res.status_code == 307
    assert res.headers["location"] == "https://example.com/x.png"


def test_allowed_roots_enforced(media_env, monkeypatch, tmp_path):
    other = tmp_path / "elsewhere"
    other.mkdir()
    monkeypatch.setattr(settings, "MEDIA_ALLOWED_ROOTS", [str(other)])
    res = media_env.client.get(f"/api/v1/datasets/{media_env.ds_id}/media", params={"field": "image", "row": 0})
    assert res.status_code == 403
    monkeypatch.setattr(settings, "MEDIA_ALLOWED_ROOTS", [str(media_env.media_dir)])
    res = media_env.client.get(f"/api/v1/datasets/{media_env.ds_id}/media", params={"field": "image", "row": 0})
    assert res.status_code == 200


def test_task_media_legacy_layout(media_env):
    res = media_env.client.get("/api/v1/tasks/execution/task1/media", params={"field": "image", "row": 0, "step": 0})
    assert res.status_code == 200
    assert res.content == PNG_BYTES
    assert media_env.client.get("/api/v1/tasks/execution/nope/media", params={"field": "image"}).status_code == 404
