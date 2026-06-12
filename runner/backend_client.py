"""Backend client used by the experiment runner.

Supports two transports:
  1) HTTP (original path, for parity with the deployed WebUI)
  2) in-process direct calls (used when localhost networking is unavailable)
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import time
from pathlib import Path
from typing import Any

import httpx
from loguru import logger

from .config import BACKEND_BASE, MAX_POLL_SEC, POLL_INTERVAL_SEC, WEBUI_ROOT


_BACKEND_CONTEXT: dict[str, Any] | None = None


def _backend_mode() -> str:
    return os.environ.get("DATAFLOW_BACKEND_MODE", "auto").strip().lower() or "auto"


def _ensure_backend_context() -> dict[str, Any]:
    global _BACKEND_CONTEXT
    if _BACKEND_CONTEXT is not None:
        return _BACKEND_CONTEXT

    backend_root = WEBUI_ROOT / "backend"
    backend_root_str = str(backend_root)
    if backend_root_str not in sys.path:
        sys.path.insert(0, backend_root_str)

    # Importing app.main initializes setup_dataflow_core() + container.init().
    import app.main  # noqa: F401
    from app.core.config import settings
    from app.core.container import container
    from app.services.dataflow_engine import dataflow_engine

    _BACKEND_CONTEXT = {
        "container": container,
        "settings": settings,
        "dataflow_engine": dataflow_engine,
    }
    return _BACKEND_CONTEXT


class _HttpBackend:
    def __init__(self, base: str = BACKEND_BASE, timeout: float = 30.0) -> None:
        self.base = base.rstrip("/")
        self.client = httpx.Client(timeout=timeout)

    def close(self):
        self.client.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def register_dataset(self, name: str, root: str, pipeline: str = "default",
                         meta: dict | None = None) -> dict:
        body = {"name": name, "root": root, "pipeline": pipeline, "meta": meta or {}}
        r = self.client.post(f"{self.base}/api/v1/datasets/", json=body)
        r.raise_for_status()
        return r.json()

    def list_datasets(self) -> list[dict]:
        r = self.client.get(f"{self.base}/api/v1/datasets/")
        r.raise_for_status()
        return r.json().get("data") or []

    def delete_dataset(self, ds_id: str) -> dict:
        r = self.client.delete(f"{self.base}/api/v1/datasets/{ds_id}")
        return r.json()

    def get_dataset_columns(self, ds_id: str) -> list[str]:
        r = self.client.get(f"{self.base}/api/v1/datasets/columns/{ds_id}")
        r.raise_for_status()
        return r.json().get("data") or []

    def list_pipelines(self) -> list[dict]:
        r = self.client.get(f"{self.base}/api/v1/pipelines/")
        r.raise_for_status()
        return r.json().get("data") or []

    def get_pipeline(self, pipeline_id: str) -> dict | None:
        r = self.client.get(f"{self.base}/api/v1/pipelines/{pipeline_id}")
        if r.status_code == 404:
            return None
        r.raise_for_status()
        return r.json().get("data")

    def create_pipeline(self, name: str, config: dict, tags: list[str] | None = None) -> dict:
        body = {"name": name, "config": config, "tags": tags or []}
        r = self.client.post(f"{self.base}/api/v1/pipelines/", json=body)
        r.raise_for_status()
        return r.json().get("data")

    def delete_pipeline(self, pipeline_id: str) -> dict:
        return self.client.delete(f"{self.base}/api/v1/pipelines/{pipeline_id}").json()

    def list_serving(self) -> list[dict]:
        r = self.client.get(f"{self.base}/api/v1/serving/")
        r.raise_for_status()
        return r.json().get("data") or []

    def list_operators(self) -> list[dict]:
        r = self.client.get(f"{self.base}/api/v1/operators/")
        r.raise_for_status()
        return r.json().get("data") or []

    def execute_pipeline(self, pipeline_id: str, asynchronous: bool = False) -> dict:
        ep = "execute-async" if asynchronous else "execute"
        r = self.client.post(f"{self.base}/api/v1/tasks/{ep}", params={"pipeline_id": pipeline_id}, timeout=300.0)
        r.raise_for_status()
        return r.json().get("data") or r.json()

    def get_task_status(self, task_id: str) -> dict:
        r = self.client.get(f"{self.base}/api/v1/tasks/execution/{task_id}/status")
        r.raise_for_status()
        return r.json().get("data") or r.json()

    def get_task_result(self, task_id: str, step: int | None = None) -> dict:
        params = {}
        if step is not None:
            params["step"] = step
        r = self.client.get(f"{self.base}/api/v1/tasks/execution/{task_id}/result", params=params)
        r.raise_for_status()
        return r.json().get("data") or r.json()

    def get_task_log(self, task_id: str) -> str:
        r = self.client.get(f"{self.base}/api/v1/tasks/execution/{task_id}/log")
        if r.status_code >= 400:
            return ""
        return r.text

    def download_result(self, task_id: str, step: int | None, out_path: Path) -> Path:
        params = {} if step is None else {"step": step}
        with self.client.stream("GET", f"{self.base}/api/v1/tasks/execution/{task_id}/download",
                                params=params) as resp:
            resp.raise_for_status()
            out_path.parent.mkdir(parents=True, exist_ok=True)
            with out_path.open("wb") as f:
                for chunk in resp.iter_bytes():
                    f.write(chunk)
        return out_path

    def list_execution_steps(self, task_id: str) -> list[dict]:
        """Return the per-operator execution results (each has an 'index')."""
        r = self.client.get(f"{self.base}/api/v1/tasks/execution/{task_id}/result")
        if r.status_code >= 400:
            return []
        data = r.json().get("data") or r.json()
        if isinstance(data, dict):
            return data.get("execution_results") or data.get("results") or []
        return data if isinstance(data, list) else []

    def list_executions(self) -> list[dict]:
        r = self.client.get(f"{self.base}/api/v1/tasks/executions")
        r.raise_for_status()
        return r.json().get("data") or []

    def wait_for_task(self, task_id: str, max_wait: int = MAX_POLL_SEC,
                      poll: float = POLL_INTERVAL_SEC) -> dict:
        deadline = time.time() + max_wait
        last = {}
        while time.time() < deadline:
            try:
                last = self.get_task_status(task_id)
            except Exception as e:
                logger.warning(f"get_task_status failed: {e}")
            status = (last or {}).get("status") or (last or {}).get("state")
            if status in ("completed", "success", "succeeded", "failed", "error", "cancelled", "killed"):
                return last
            time.sleep(poll)
        logger.warning(f"task {task_id} did not finish within {max_wait}s")
        return last


class _InProcessBackend:
    def __init__(self) -> None:
        self.ctx = _ensure_backend_context()
        self.container = self.ctx["container"]
        self.settings = self.ctx["settings"]
        self.dataflow_engine = self.ctx["dataflow_engine"]

    def close(self):
        return None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def register_dataset(self, name: str, root: str, pipeline: str = "default",
                         meta: dict | None = None) -> dict:
        data = self.container.dataset_registry.add_or_update({
            "name": name,
            "root": root,
            "pipeline": pipeline,
            "meta": meta or {},
        })
        return {"success": True, "code": 200, "message": "Created", "data": data}

    def list_datasets(self) -> list[dict]:
        return self.container.dataset_registry.list()

    def delete_dataset(self, ds_id: str) -> dict:
        ok = self.container.dataset_registry.remove(ds_id)
        return {"success": ok, "code": 200 if ok else 404, "message": "OK" if ok else "Dataset not found"}

    def get_dataset_columns(self, ds_id: str) -> list[str]:
        return self.container.dataset_registry.get_columns(ds_id)

    def list_pipelines(self) -> list[dict]:
        return self.container.pipeline_registry.list_pipelines()

    def get_pipeline(self, pipeline_id: str) -> dict | None:
        return self.container.pipeline_registry.get_pipeline(pipeline_id)

    def create_pipeline(self, name: str, config: dict, tags: list[str] | None = None) -> dict:
        return self.container.pipeline_registry.create_pipeline({
            "name": name,
            "config": config,
            "tags": tags or [],
        })

    def delete_pipeline(self, pipeline_id: str) -> dict:
        ok = self.container.pipeline_registry.delete_pipeline(pipeline_id)
        return {"success": ok, "code": 200 if ok else 404, "message": "OK" if ok else "Pipeline not found"}

    def list_serving(self) -> list[dict]:
        raw = self.container.serving_registry._get_all() or {}
        result = []
        for sid, record in raw.items():
            item = dict(record)
            item["id"] = sid
            result.append(item)
        return result

    def list_operators(self) -> list[dict]:
        return self.container.operator_registry.get_op_list(lang="zh")

    def execute_pipeline(self, pipeline_id: str, asynchronous: bool = False) -> dict:
        if asynchronous:
            logger.warning("In-process backend does not support async execution; falling back to sync run")
        pipeline = self.container.pipeline_registry.get_pipeline(pipeline_id)
        if not pipeline:
            raise ValueError(f"Pipeline {pipeline_id} not found")
        task_id, _, initial_result = self.container.task_registry.start_execution(
            pipeline_id=pipeline_id,
            config=pipeline,
        )
        result = self.dataflow_engine.run(
            pipeline["config"],
            task_id,
            execution_path=self.container.task_registry.path,
        )
        data = self.container.task_registry._read()
        if task_id in data.get("tasks", {}):
            data["tasks"][task_id].update(result)
            self.container.task_registry._write(data)
        merged = dict(initial_result)
        merged.update(result)
        merged["task_id"] = task_id
        return merged

    def get_task_status(self, task_id: str) -> dict:
        return self.container.task_registry.get_execution_status(task_id) or {}

    def get_task_result(self, task_id: str, step: int | None = None) -> dict:
        return self.container.task_registry.get_execution_result(task_id, step, 5) or {}

    def get_task_log(self, task_id: str) -> str:
        logs = self.container.task_registry.get_execution_logs(task_id)
        return "\n".join(logs or [])

    def _result_file_for_step(self, task_id: str, step: int) -> Path:
        execution_data = self.container.task_registry._read().get("tasks", {}).get(task_id)
        if not execution_data:
            raise FileNotFoundError(f"Task {task_id} not found")
        output = execution_data.get("output", {})
        execution_results = output.get("execution_results", [])
        if step < 0 or step >= len(execution_results):
            raise FileNotFoundError(f"Invalid step {step} for task {task_id}")
        actual_step = step + 1
        return Path(self.settings.CACHE_DIR) / f"{task_id}_output" / f"dataflow_cache_step_step{actual_step}.jsonl"

    def download_result(self, task_id: str, step: int, out_path: Path) -> Path:
        src = self._result_file_for_step(task_id, step)
        if not src.exists():
            raise FileNotFoundError(str(src))
        out_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, out_path)
        return out_path

    def list_executions(self) -> list[dict]:
        return self.container.task_registry.list_executions()

    def wait_for_task(self, task_id: str, max_wait: int = MAX_POLL_SEC,
                      poll: float = POLL_INTERVAL_SEC) -> dict:
        deadline = time.time() + max_wait
        last = {}
        while time.time() < deadline:
            last = self.get_task_status(task_id)
            status = (last or {}).get("status") or (last or {}).get("state")
            if status in ("completed", "success", "succeeded", "failed", "error", "cancelled", "killed"):
                return last
            time.sleep(poll)
        return last


def _http_available(base: str, timeout: float) -> bool:
    try:
        with httpx.Client(timeout=min(timeout, 2.0)) as client:
            resp = client.get(f"{base.rstrip('/')}/api/v1/datasets/")
            return resp.status_code < 500
    except Exception:
        return False


class DataFlowBackend:
    def __init__(self, base: str = BACKEND_BASE, timeout: float = 30.0, mode: str | None = None) -> None:
        self.mode = (mode or _backend_mode()).lower()
        if self.mode == "http":
            self._impl = _HttpBackend(base=base, timeout=timeout)
        elif self.mode == "inproc":
            self._impl = _InProcessBackend()
        else:
            if _http_available(base, timeout):
                self._impl = _HttpBackend(base=base, timeout=timeout)
                self.mode = "http"
            else:
                logger.warning("HTTP backend unreachable; falling back to in-process backend transport")
                self._impl = _InProcessBackend()
                self.mode = "inproc"

    def close(self):
        return self._impl.close()

    def __enter__(self):
        self._impl.__enter__()
        return self

    def __exit__(self, *exc):
        return self._impl.__exit__(*exc)

    def __getattr__(self, name: str):
        return getattr(self._impl, name)
