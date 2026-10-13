"""Multimodal field inspection and media file serving.

Records reference media by path; these endpoints let the UI / agents find out which
columns are media and fetch the referenced bytes by (dataset|task, row, field) — the
client never sends a raw filesystem path.
"""
import json
import os
from typing import Dict, Optional

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse, RedirectResponse

from app.api.v1.envelope import ApiResponse
from app.api.v1.resp import ok
from app.core.config import settings
from app.core.container import container
from app.core.logger_setup import get_logger
from app.services import media_assets
from app.services.task_outputs import resolve_output_file

logger = get_logger(__name__)

router = APIRouter(tags=["media"])


def _serve_cell(record: Optional[Dict], field: str, base_dir: str, index: int):
    if record is None:
        raise HTTPException(404, "Row not found")
    if field not in record:
        raise HTTPException(404, f"Field '{field}' not found in record")
    value = media_assets.pick_cell_path(record.get(field), index)
    if not value:
        raise HTTPException(404, f"Field '{field}' holds no media reference")
    if media_assets.is_url(value):
        if not value.lower().startswith(("http://", "https://")):
            raise HTTPException(400, "Only http(s) media URLs can be opened")
        return RedirectResponse(value)
    path = media_assets.normalize_media_path(value, base_dir)
    if not os.path.exists(path):
        raise HTTPException(404, f"Media file not found: {path}")
    if not media_assets.is_safe_media_path(path, settings.MEDIA_ALLOWED_ROOTS):
        raise HTTPException(403, "Refusing to serve this file (not a media file or outside MEDIA_ALLOWED_ROOTS)")
    return FileResponse(path, media_type=media_assets.guess_media_type(path))


def _read_jsonl_row(path: str, row: int) -> Optional[Dict]:
    if row < 0:
        return None
    with open(path, "r", encoding="utf-8") as f:
        idx = 0
        for line in f:
            if not line.strip():
                continue
            if idx == row:
                return json.loads(line)
            idx += 1
    return None


@router.get(
    "/datasets/{ds_id}/field_modalities",
    response_model=ApiResponse[Dict[str, str]],
    operation_id="get_field_modalities",
    summary="推断数据集每个字段的模态（text/image/audio/video），媒体以文件路径字段承载",
)
def get_field_modalities(ds_id: str, sample_rows: int = Query(None, ge=1, le=1000)):
    try:
        return ok(container.dataset_registry.get_field_modalities(ds_id, sample_rows))
    except FileNotFoundError:
        raise HTTPException(404, "Dataset not found")
    except Exception as e:
        raise HTTPException(500, f"Failed to infer field modalities: {e}")


@router.get(
    "/datasets/{ds_id}/media",
    operation_id="get_dataset_media",
    summary="按 行号+字段 返回数据集记录中引用的媒体文件（图片/音频/视频）",
)
def get_dataset_media(ds_id: str, field: str, row: int = Query(0, ge=0), index: int = Query(0, ge=0)):
    ds = container.dataset_registry.get(ds_id)
    if not ds:
        raise HTTPException(404, "Dataset not found")
    try:
        record = container.dataset_registry.read_record(ds_id, row)
    except Exception as e:
        raise HTTPException(500, f"Failed to read record: {e}")
    base_dir = os.path.dirname(os.path.abspath(ds["root"]))
    return _serve_cell(record, field, base_dir, index)


@router.get(
    "/tasks/execution/{task_id}/media",
    operation_id="get_task_media",
    summary="按 算子(step 或 node)+行号+字段 返回任务输出记录中引用的媒体文件",
)
def get_task_media(
    task_id: str,
    field: str,
    row: int = Query(0, ge=0),
    step: int = Query(None),
    node: str = Query(None),
    index: int = Query(0, ge=0),
):
    execution = container.task_registry._read().get("tasks", {}).get(task_id)
    if not execution:
        raise HTTPException(404, f"Task with id {task_id} not found")
    output = execution.get("output", {}) or {}
    cache_file, _, _ = resolve_output_file(
        settings.CACHE_DIR, task_id, output, step=step, node=node, fallback_to_input=True
    )
    if not cache_file:
        raise HTTPException(404, "Output file not found for this operator")
    try:
        record = _read_jsonl_row(cache_file, row)
    except Exception as e:
        raise HTTPException(500, f"Failed to read output record: {e}")
    # Executor outputs hold absolute paths; relative ones resolve against the
    # input dataset's directory, the same anchor the executor uses.
    base_dir = os.path.dirname(cache_file)
    config = execution.get("pipeline_config") or {}
    input_dataset = config.get("input_dataset")
    ds_id = input_dataset.get("id") if isinstance(input_dataset, dict) else input_dataset
    ds = container.dataset_registry.get(ds_id) if ds_id else None
    if ds:
        base_dir = os.path.dirname(os.path.abspath(ds["root"]))
    return _serve_cell(record, field, base_dir, index)
