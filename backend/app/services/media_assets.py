"""Multimodal field conventions shared by preview, validation, execution and media endpoints.

Records stay plain JSON. Images / audio / video are carried as string fields whose
value is a local file path (or an http(s) URL). This module is the single source of
truth for:

- sniffing the modality of a single value (by extension / URL / data URI),
- inferring the modality of a dataset column (majority vote over sampled rows),
- inferring the modality an operator parameter expects (by parameter name),
- normalizing media paths to absolute paths (the engine ``os.chdir``s while
  operators run, so relative paths would silently resolve against the wrong dir),
- guarding media file serving against path traversal.

Pure functions only: no DataFlow import, no container access, so it is cheap to test
and safe to import inside Ray workers.
"""
from __future__ import annotations

import json
import mimetypes
import os
import re
from typing import Any, Dict, Iterable, List, Optional, Sequence

TEXT = "text"
IMAGE = "image"
AUDIO = "audio"
VIDEO = "video"
MEDIA_MODALITIES = (IMAGE, AUDIO, VIDEO)

IMAGE_EXTS = {"jpg", "jpeg", "png", "gif", "bmp", "webp", "tif", "tiff", "svg"}
AUDIO_EXTS = {"wav", "mp3", "flac", "aac", "ogg", "m4a", "opus", "wma"}
VIDEO_EXTS = {"mp4", "mov", "avi", "mkv", "webm", "flv", "wmv", "m4v"}

_EXT_TO_MODALITY: Dict[str, str] = {
    **{ext: IMAGE for ext in IMAGE_EXTS},
    **{ext: AUDIO for ext in AUDIO_EXTS},
    **{ext: VIDEO for ext in VIDEO_EXTS},
}

_EXTRA_MIME = {
    "webp": "image/webp",
    "flac": "audio/flac",
    "m4a": "audio/mp4",
    "opus": "audio/opus",
    "mkv": "video/x-matroska",
    "webm": "video/webm",
}

# Parameter-name conventions. Order matters: first match wins.
# Matches e.g. input_image_key, image_key, input_images_key, output_image_path_key,
# input_audio_key, speech_key, input_video_key, video_path.
_PARAM_PATTERNS = [
    (IMAGE, re.compile(r"(^|_)(image|images|img|imgs|picture|photo)(_|$)")),
    (AUDIO, re.compile(r"(^|_)(audio|audios|speech|wav|voice)(_|$)")),
    (VIDEO, re.compile(r"(^|_)(video|videos|clip|clips)(_|$)")),
]

# Column-name conventions, used as a tie-breaker when values cannot be sniffed
# (e.g. URLs without extensions).
_FIELD_NAME_PATTERNS = _PARAM_PATTERNS

_URL_RE = re.compile(r"^(https?|s3|gs|oss)://", re.IGNORECASE)
_DATA_URI_RE = re.compile(r"^data:(image|audio|video)/[\w.+-]+;base64,", re.IGNORECASE)


def _extension(value: str) -> str:
    # Strip URL query/fragment before looking at the extension.
    base = value.split("?", 1)[0].split("#", 1)[0]
    _, ext = os.path.splitext(base)
    return ext[1:].lower() if ext else ""


def is_url(value: Any) -> bool:
    return isinstance(value, str) and bool(_URL_RE.match(value.strip()))


def sniff_value_modality(value: Any) -> str:
    """Return the modality a single field value most likely carries."""
    if not isinstance(value, str):
        return TEXT
    stripped = value.strip()
    if not stripped or "\n" in stripped or len(stripped) > 4096:
        return TEXT
    data_uri = _DATA_URI_RE.match(stripped)
    if data_uri:
        return data_uri.group(1).lower()
    return _EXT_TO_MODALITY.get(_extension(stripped), TEXT)


def _sniff_cell(value: Any) -> str:
    """Like sniff_value_modality but also looks inside list cells (multi-image fields)."""
    if isinstance(value, list) and value:
        modalities = {sniff_value_modality(v) for v in value}
        if len(modalities) == 1:
            return modalities.pop()
        return TEXT
    return sniff_value_modality(value)


def infer_field_modality_from_name(field_name: str) -> str:
    name = str(field_name).lower()
    for modality, pattern in _FIELD_NAME_PATTERNS:
        if pattern.search(name):
            return modality
    return TEXT


def infer_field_modalities(records: Sequence[Dict[str, Any]]) -> Dict[str, str]:
    """Infer per-column modality from sampled records.

    A column is media if a strict majority of its non-empty sampled values sniff as
    the same media modality. Columns whose values are unrecognizable (e.g. extension-less
    URLs) fall back to the column-name convention, but only when values look like
    paths/URLs at all.
    """
    columns: List[str] = []
    for record in records:
        if isinstance(record, dict):
            for key in record.keys():
                if key not in columns:
                    columns.append(key)

    result: Dict[str, str] = {}
    for column in columns:
        votes: Dict[str, int] = {}
        non_empty = 0
        path_like = 0
        for record in records:
            if not isinstance(record, dict):
                continue
            value = record.get(column)
            if value is None or value == "" or value == []:
                continue
            non_empty += 1
            if isinstance(value, str) and (is_url(value) or os.sep in value or "/" in value):
                path_like += 1
            modality = _sniff_cell(value)
            votes[modality] = votes.get(modality, 0) + 1

        modality = TEXT
        if non_empty:
            best = max(votes.items(), key=lambda item: item[1])
            if best[0] != TEXT and best[1] * 2 > non_empty:
                modality = best[0]
            elif path_like * 2 > non_empty:
                modality = infer_field_modality_from_name(column)
        result[column] = modality
    return result


def infer_param_modality(param_name: str) -> str:
    """Modality an operator run() parameter expects, by naming convention.

    ``input_image_key`` -> image, ``input_audio_key`` -> audio, ``input_key`` -> text.
    """
    name = str(param_name).lower()
    for modality, pattern in _PARAM_PATTERNS:
        if pattern.search(name):
            return modality
    return TEXT


def guess_media_type(path: str) -> str:
    ext = _extension(path)
    if ext in _EXTRA_MIME:
        return _EXTRA_MIME[ext]
    guessed, _ = mimetypes.guess_type(path)
    return guessed or "application/octet-stream"


def normalize_media_path(value: Any, base_dir: str) -> Any:
    """Make a media path absolute, anchored at ``base_dir``.

    URLs, data URIs, absolute paths and non-strings are returned unchanged.
    ``~`` is expanded. Lists are normalized element-wise.
    """
    if isinstance(value, list):
        return [normalize_media_path(item, base_dir) for item in value]
    if not isinstance(value, str):
        return value
    stripped = value.strip()
    if not stripped or is_url(stripped) or _DATA_URI_RE.match(stripped):
        return value
    expanded = os.path.expanduser(stripped)
    if os.path.isabs(expanded):
        return os.path.normpath(expanded)
    return os.path.normpath(os.path.join(base_dir, expanded))


def _iter_local_paths(value: Any) -> Iterable[str]:
    if isinstance(value, list):
        for item in value:
            yield from _iter_local_paths(item)
    elif isinstance(value, str):
        stripped = value.strip()
        if stripped and not is_url(stripped) and not _DATA_URI_RE.match(stripped):
            yield stripped


def normalize_input_file(
    src_path: str,
    dst_path: str,
    media_fields: Sequence[str],
    base_dir: Optional[str] = None,
) -> int:
    """Rewrite a jsonl file so ``media_fields`` hold absolute paths.

    Returns the number of values that changed. If nothing changes, ``dst_path`` is
    still written so callers can always point storage at it.
    """
    base_dir = base_dir or os.path.dirname(os.path.abspath(src_path))
    changed = 0
    os.makedirs(os.path.dirname(os.path.abspath(dst_path)), exist_ok=True)
    with open(src_path, "r", encoding="utf-8") as src, open(dst_path, "w", encoding="utf-8") as dst:
        for line in src:
            if not line.strip():
                continue
            record = json.loads(line)
            if isinstance(record, dict):
                for field in media_fields:
                    if field in record:
                        new_value = normalize_media_path(record[field], base_dir)
                        if new_value != record[field]:
                            changed += 1
                            record[field] = new_value
            dst.write(json.dumps(record, ensure_ascii=False) + "\n")
    return changed


def needs_normalization(records: Sequence[Dict[str, Any]], media_fields: Sequence[str]) -> bool:
    """True if any sampled media field value is a relative local path."""
    for record in records:
        if not isinstance(record, dict):
            continue
        for field in media_fields:
            for path in _iter_local_paths(record.get(field)):
                if not os.path.isabs(os.path.expanduser(path)):
                    return True
    return False


def validate_media_references(
    records: Sequence[Dict[str, Any]],
    field: str,
    base_dir: Optional[str] = None,
    limit: int = 5,
) -> List[str]:
    """Return up to ``limit`` resolved local paths in ``field`` that do not exist.

    URLs and data URIs are skipped (not checked over the network).
    """
    missing: List[str] = []
    for record in records:
        if not isinstance(record, dict):
            continue
        for path in _iter_local_paths(record.get(field)):
            resolved = normalize_media_path(path, base_dir) if base_dir else os.path.expanduser(path)
            if not os.path.exists(resolved) and resolved not in missing:
                missing.append(resolved)
                if len(missing) >= limit:
                    return missing
    return missing


def is_safe_media_path(path: str, allowed_roots: Sequence[str] = ()) -> bool:
    """True if ``path`` has a known media extension, is an existing regular file
    (after resolving symlinks), and — when ``allowed_roots`` is non-empty — lies
    inside one of them.

    Single-user local tool, but the media endpoint still must not become a generic
    "read any file" primitive for anything that can reach the port. Callers only
    pass paths taken from registered records, never raw request input.
    """
    if not isinstance(path, str) or not path:
        return False
    real = os.path.realpath(path)
    # Check the extension of both the given and the resolved path so a
    # ``photo.png -> /etc/passwd`` symlink is rejected too.
    if _EXT_TO_MODALITY.get(_extension(path)) is None or _EXT_TO_MODALITY.get(_extension(real)) is None:
        return False
    if not os.path.isfile(real):
        return False
    roots = [root for root in allowed_roots if root]
    if not roots:
        return True
    for root in roots:
        real_root = os.path.realpath(root)
        if real == real_root or real.startswith(real_root.rstrip(os.sep) + os.sep):
            return True
    return False


def pick_cell_path(value: Any, index: int = 0) -> Optional[str]:
    """Pick one local path/URL from a cell (cells may hold a list of paths)."""
    if isinstance(value, list):
        if 0 <= index < len(value) and isinstance(value[index], str):
            return value[index]
        return None
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None
