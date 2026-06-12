#!/usr/bin/env python3
"""Stdio MCP server for DataFlow experiments.

CRITICAL: DataFlow's registry has raw print() calls that write to stdout,
breaking the stdio MCP protocol. We redirect fd 1 → fd 2 during imports,
then restore it for the MCP server.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

# Suppress DataFlow's stdout logger BEFORE any imports
os.environ["DF_LOGGING_LEVEL"] = "CRITICAL"
os.environ["LOGURU_LEVEL"] = "CRITICAL"

# Save real stdout fd, redirect fd 1 to stderr during imports
_saved_stdout_fd = os.dup(1)
os.dup2(2, 1)  # fd 1 now points to stderr

import logging
logging.basicConfig(stream=sys.stderr, level=logging.ERROR)

ROOT = Path(__file__).resolve().parents[2]
EXPERIMENT_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = ROOT / "df_web_and_skills" / "DataFlow-WebUI" / "backend"
for p in (str(EXPERIMENT_ROOT), str(BACKEND_ROOT)):
    if p not in sys.path:
        sys.path.insert(0, p)

from fastmcp import FastMCP
from fastapi import HTTPException
from fastapi.responses import JSONResponse

from app.api.v1.envelope import ApiResponse
from app.api.v1.errors import ApiError
from app.api.v1.resp import ok
from app.api.v1.endpoints.operators import (
    get_operator_detail_by_name as ep_get_operator_detail_by_name,
    list_operator_categories as ep_list_operator_categories,
    list_operators as ep_list_operators,
    recommend_operator_categories as ep_recommend_operator_categories,
)
from app.api.v1.endpoints.serving import list_serving_instances as ep_list_serving_instances
from app.core.container import container
from app.mcp_server import _config_to_vue_flow
from app.schemas.operator import OperatorCategoryRecommendationRequest
from app.schemas.pipelines import PipelineConfig, PipelineIn, PipelineUpdateIn
from app.services.dataflow_engine import dataflow_engine
import app.main  # noqa: F401  # initializes backend globals

# After all imports: remove any loguru sinks that write to stdout
from loguru import logger as _lg
_lg.remove()

mcp = FastMCP("dataflow")


def _to_dict(resp: Any) -> dict[str, Any]:
    if isinstance(resp, JSONResponse):
        return json.loads(resp.body.decode("utf-8"))
    if hasattr(resp, "model_dump"):
        return resp.model_dump()
    if isinstance(resp, dict):
        return resp
    return {"success": True, "code": 200, "message": "OK", "data": resp}


def _handle_error(exc: Exception) -> dict[str, Any]:
    if isinstance(exc, ApiError):
        return ApiResponse(
            success=False,
            code=exc.code,
            message=exc.message,
            data=exc.data,
            meta={"http_status": exc.http_status, "path": "mcp://dataflow"},
        ).model_dump()
    if isinstance(exc, HTTPException):
        return ApiResponse(
            success=False,
            code=exc.status_code * 100,
            message=str(exc.detail) if exc.detail else "HTTP error",
            meta={"http_status": exc.status_code, "path": "mcp://dataflow"},
        ).model_dump()
    return ApiResponse(
        success=False,
        code=50000,
        message=str(exc) or "Internal error",
        meta={"http_status": 500, "path": "mcp://dataflow"},
    ).model_dump()


def _safe_call(fn, *args, **kwargs) -> dict[str, Any]:
    try:
        return _to_dict(fn(*args, **kwargs))
    except Exception as exc:
        return _handle_error(exc)


def _created(data: Any, message: str = "Created") -> dict[str, Any]:
    return {"success": True, "code": 200, "message": message, "data": data}


@mcp.tool
def list_operator_categories(lang: str = "zh"):
    return _safe_call(ep_list_operator_categories, lang=lang)


@mcp.tool
def recommend_operator_categories(task_description: str, dataset_columns: list[str] | None = None, max_categories: int = 2):
    payload = OperatorCategoryRecommendationRequest(
        task_description=task_description,
        dataset_columns=dataset_columns or [],
        max_categories=max_categories,
    )
    return _safe_call(ep_recommend_operator_categories, payload)


@mcp.tool
def list_operators(category: str | None = None, lang: str = "zh"):
    return _safe_call(ep_list_operators, category=category, lang=lang)


@mcp.tool
def get_operator_detail_by_name(op_name: str, lang: str = "zh"):
    return _safe_call(ep_get_operator_detail_by_name, op_name=op_name, lang=lang)


@mcp.tool
def list_datasets():
    try:
        return _to_dict(ok(container.dataset_registry.list()))
    except Exception as exc:
        return _handle_error(exc)


@mcp.tool
def get_dataset_columns(ds_id: str):
    try:
        return _to_dict(ok(container.dataset_registry.get_columns(ds_id)))
    except Exception as exc:
        return _handle_error(exc)


@mcp.tool
def register_dataset(name: str, root: str, pipeline: str = "default", meta: dict | None = None):
    try:
        ds = container.dataset_registry.add_or_update({
            "name": name,
            "root": root,
            "pipeline": pipeline,
            "meta": meta or {},
        })
        return _created(ds)
    except Exception as exc:
        return _handle_error(exc)


@mcp.tool
def list_serving():
    return _safe_call(ep_list_serving_instances)


@mcp.tool
def list_servings():
    return _safe_call(ep_list_serving_instances)


@mcp.tool
def list_pipelines():
    try:
        return _to_dict(ok(container.pipeline_registry.list_pipelines()))
    except Exception as exc:
        return _handle_error(exc)


@mcp.tool
def get_pipeline(pipeline_id: str):
    try:
        pipeline = container.pipeline_registry.get_pipeline(pipeline_id)
        if not pipeline:
            raise HTTPException(status_code=404, detail=f"Pipeline with id {pipeline_id} not found")
        return _to_dict(ok(pipeline))
    except Exception as exc:
        return _handle_error(exc)


@mcp.tool
def validate_pipeline_config(config: dict):
    try:
        payload = PipelineConfig.model_validate(config)
        validation = container.pipeline_registry.validate_pipeline_config(payload.model_dump())
        return _to_dict(ok(validation))
    except Exception as exc:
        return _handle_error(exc)


@mcp.tool
def create_pipeline(name: str, config: dict, tags: list[str] | None = None):
    try:
        payload = PipelineIn(name=name, config=PipelineConfig.model_validate(config), tags=tags or [])
        pipeline_in_data = payload.model_dump()
        operators = pipeline_in_data.get("config", {}).get("operators", [])
        for op in operators:
            op["params"] = container.pipeline_registry._parse_frontend_params(op.get("params", []))
        pipeline = container.pipeline_registry.create_pipeline(pipeline_in_data)
        return _created(pipeline)
    except Exception as exc:
        return _handle_error(exc)


@mcp.tool
def update_pipeline(pipeline_id: str, name: str | None = None, config: dict | None = None, tags: list[str] | None = None):
    try:
        payload_kwargs: dict[str, Any] = {}
        if name is not None:
            payload_kwargs["name"] = name
        if config is not None:
            payload_kwargs["config"] = PipelineConfig.model_validate(config)
        if tags is not None:
            payload_kwargs["tags"] = tags
        payload = PipelineUpdateIn(**payload_kwargs)
        updated = container.pipeline_registry.update_pipeline(
            pipeline_id,
            payload.model_dump(exclude_unset=True),
        )
        return _to_dict(ok(updated))
    except Exception as exc:
        return _handle_error(exc)


@mcp.tool
def render_pipeline_in_editor(pipeline_id: str):
    try:
        pipeline = container.pipeline_registry.get_pipeline(pipeline_id)
        if not pipeline:
            return {
                "status": "error",
                "message": f"Pipeline {pipeline_id} not found",
                "nodes_count": 0,
                "edges_count": 0,
            }
        nodes, edges = _config_to_vue_flow(pipeline.get("config", {}))
        return {
            "status": "ok",
            "message": f"Pipeline '{pipeline.get('name', pipeline_id)}' 已同步到编辑器",
            "nodes_count": len(nodes),
            "edges_count": len(edges),
        }
    except Exception as exc:
        return {
            "status": "error",
            "message": str(exc) or "render failed",
            "nodes_count": 0,
            "edges_count": 0,
        }


@mcp.tool
def execute_pipeline(pipeline_id: str):
    try:
        pipeline = container.pipeline_registry.get_pipeline(pipeline_id)
        if not pipeline:
            raise HTTPException(status_code=404, detail=f"Pipeline {pipeline_id} not found")
        task_id, _, initial_result = container.task_registry.start_execution(
            pipeline_id=pipeline_id,
            config=pipeline,
        )
        result = dataflow_engine.run(
            pipeline["config"],
            task_id,
            execution_path=container.task_registry.path,
        )
        data = container.task_registry._read()
        if task_id in data.get("tasks", {}):
            data["tasks"][task_id].update(result)
            container.task_registry._write(data)
        merged = dict(initial_result)
        merged.update(result)
        merged["task_id"] = task_id
        return _to_dict(ok(merged, message=f"Pipeline execution {merged.get('status', 'unknown')}"))
    except Exception as exc:
        return _handle_error(exc)


@mcp.tool
def execute_pipeline_async(pipeline_id: str):
    return execute_pipeline(pipeline_id)


@mcp.tool
def get_execution_status(task_id: str):
    try:
        status = container.task_registry.get_execution_status(task_id)
        if not status:
            raise HTTPException(status_code=404, detail=f"Task with id {task_id} not found")
        return _to_dict(ok(status))
    except Exception as exc:
        return _handle_error(exc)


@mcp.tool
def get_task_result(task_id: str, step: int | None = None, limit: int = 5):
    try:
        result = container.task_registry.get_execution_result(task_id, step, limit)
        if not result:
            raise HTTPException(status_code=404, detail=f"Task with id {task_id} not found")
        return _to_dict(ok(result))
    except Exception as exc:
        return _handle_error(exc)


if __name__ == "__main__":
    # Restore real stdout for MCP protocol
    os.dup2(_saved_stdout_fd, 1)
    os.close(_saved_stdout_fd)
    # Rebuild sys.stdout from the restored fd
    sys.stdout = open(1, 'w', buffering=1)
    mcp.run(transport="stdio")
