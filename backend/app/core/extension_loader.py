"""Shared DataFlow extension loader.

Injects ``backend/dataflow_extensions`` onto ``sys.path`` (idempotent) then
imports each module listed in ``settings._DATAFLOW_EXTENSIONS``.  Both the
main-process ``AppContainer.init()`` and the Ray worker's
``dataflow_pipeline_execute`` call this, so the plumbing lives in one place.
"""
from __future__ import annotations

import importlib
import os
import sys

from app.core.logger_setup import get_logger

logger = get_logger(__name__)

# Absolute path to ``backend/dataflow_extensions``.
_EXTENSIONS_ROOT = os.path.join(
    os.path.dirname(  # app/
        os.path.dirname(  # app/core/
            os.path.abspath(__file__)
        )
    ),
    os.pardir,           # backend/
    "dataflow_extensions",
)
_EXTENSIONS_ROOT = os.path.normpath(_EXTENSIONS_ROOT)


def _ensure_path() -> None:
    if _EXTENSIONS_ROOT not in sys.path:
        sys.path.insert(0, _EXTENSIONS_ROOT)


def load_dataflow_extensions() -> None:
    """Import every module in ``settings._DATAFLOW_EXTENSIONS``.

    Safe to call multiple times; the path injection is idempotent and Python's
    module cache prevents double-loading.
    """
    from app.core.config import settings  # local import avoids circular deps

    _ensure_path()

    for ext in settings._DATAFLOW_EXTENSIONS:
        try:
            importlib.import_module(ext)
            logger.info(f"Loaded DataFlow extension: {ext}")
        except ImportError as e:
            logger.warning(f"Failed to load DataFlow extension '{ext}': {e}")
        except Exception as e:
            logger.error(f"Error loading DataFlow extension '{ext}': {e}")
