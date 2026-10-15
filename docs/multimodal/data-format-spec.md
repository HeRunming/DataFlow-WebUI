# Multimodal Data Format Specification

This document specifies how multimodal data is represented in DataFlow-WebUI pipelines. It is the reference for anyone building multimodal operators, uploading multimodal datasets, or writing agent instructions that configure multimodal pipelines.

## Core convention: records stay JSON, media travels as file paths

Records in a DataFlow pipeline are ordinary JSON objects (stored as JSONL). Image, audio, and video content is **not embedded** in the records. Instead, each multimodal field holds a **string value** that is either:

- an **absolute local path** to a media file (e.g. `/data/assets/cat.png`)
- a **relative local path** interpreted against the dataset root directory (e.g. `images/cat.png`)
- an **http(s) URL** pointing to a remote file

This matches the convention used by `Speech2TextGenerator` and `PromptedVQAGenerator` in open-dataflow 1.0.10, and is the approach all harness-side multimodal operators follow.

### Why paths, not embeddings

- Existing JSONL tooling (pandas, jq, validators) keeps working without modification.
- The harness execution model passes DataFrames between operators — embedding binary in columns would bloat in-memory state and make column inspection impossible.
- The media endpoint (`GET /api/v1/datasets/{id}/media?field=F&row=N`) fetches any registered field on demand, so the UI can render media without operators needing to expose it.

## Absolute-path requirement at execution time

The harness executor does `os.chdir(api_pipelines/)` around each `operator.run()` call, which means relative paths in records would resolve against the wrong directory. To prevent this:

- When the executor routes input to a root node (one that reads the input dataset directly), it detects relative media paths and **rewrites them to absolute paths** before anchoring `FileStorage`. The rewrite uses the dataset root directory as the base.
- If a dataset contains only absolute paths, no rewrite happens and the original file is used as-is.
- Operators that generate new media files (e.g. `ImageGenStub`) **must** write to a path derived from `storage.cache_path` and return the **absolute path** as the field value.

## Modality inference

The harness infers field modality from two sources:

### 1. Value sniffing (runtime)

A field value's modality is inferred from its file extension:

| Modality | Extensions |
|---|---|
| `image` | jpg, jpeg, png, gif, bmp, webp, tif, tiff, svg |
| `audio` | wav, mp3, flac, aac, ogg, m4a, opus, wma |
| `video` | mp4, mov, avi, mkv, webm, flv, wmv, m4v |
| `text` | everything else |

Data URIs (`data:image/...;base64,...`) are recognised by prefix and classified accordingly. Extension-less http(s) URLs stay `text` unless the column name convention applies.

For a column, the harness samples up to 20 rows and takes a **strict majority vote**: a column is `image/audio/video` only if more than half of its non-empty sampled values return that modality.

### 2. Column name convention (fallback)

When a column's values lack recognisable extensions (e.g. a CDN URL without an extension), the column name is used:

| Name pattern | Modality |
|---|---|
| `image`, `images`, `img`, `picture`, `photo` (word boundary) | `image` |
| `audio`, `speech`, `wav`, `voice` (word boundary) | `audio` |
| `video`, `clip`, `clips` (word boundary) | `video` |

These patterns are matched case-insensitively as whole words or underscore-delimited segments, so `image_path`, `input_image_key`, `my_image` all infer `image`, while `imagine` does not.

The JavaScript implementation in `frontend/src/js/mediaModalities.js` mirrors these rules exactly.

## Operator parameter name convention

When an operator run-parameter **name** follows a naming convention, the harness can infer which field modality it expects, enabling pipeline validation warnings and UI hints:

| Parameter name pattern | Expected modality |
|---|---|
| `input_image_key`, `image_key`, `*_image_path` | `image` |
| `input_audio_key`, `audio_key`, `speech_key`, `*_audio_path` | `audio` |
| `input_video_key`, `video_key`, `*_video_path` | `video` |

The backend implementation is `infer_param_modality()` in `backend/app/services/media_assets.py`; the JS mirror is `inferParamModality()` in `frontend/src/js/mediaModalities.js`.

## DAG pipelines and multimodal field visibility

In a DAG pipeline, each operator sees the fields produced by its direct ancestors. For merge nodes (multiple predecessors), the harness joins branch outputs:

- If every branch contains an `id` column (or a `merge_key` is specified on the operator), the merge is an `id`-keyed left join, keeping all rows from the first branch and adding columns from subsequent branches.
- If no common key is available, rows are aligned by position (shortest branch wins) with a logged warning.
- Column names that collide across branches are renamed `{column}__{branch_node_id}`. E.g. if both `caption` branch and `asr` branch produce a `model` column, the merged record contains `model` (from the first branch) and `model__asr` (from the second).

Downstream operators can reference any field visible in the merge result. The harness validates this statically before execution.

## id column recommendation

Include an `id` column in every multimodal dataset. It enables deterministic key-based merging in fan-out pipelines and helps the media endpoint serve records by row index consistently even when rows are filtered. Any stable unique identifier works (integer, UUID, filename stem).

## Example dataset record

```json
{
  "id": "img_0042",
  "image_path": "/data/cc3m/images/0042.jpg",
  "audio_path": "/data/librispeech/wavs/0042.wav",
  "caption_ref": "a cat sitting on a mat",
  "speaker_id": "spk_007"
}
```

After running through `ImageCaptionStub` and `AudioTranscribeStub` in parallel and merging:

```json
{
  "id": "img_0042",
  "image_path": "/data/cc3m/images/0042.jpg",
  "audio_path": "/data/librispeech/wavs/0042.wav",
  "caption_ref": "a cat sitting on a mat",
  "speaker_id": "spk_007",
  "caption": "[stub caption for 0042.jpg]",
  "caption_model": "ImageCaptionStub/0.1",
  "transcript": "[stub transcript of 0042.wav]"
}
```

## Media serving endpoints

The harness exposes three endpoints that let the browser fetch media by dataset record reference, without exposing raw filesystem paths:

| Endpoint | Purpose |
|---|---|
| `GET /api/v1/datasets/{id}/field_modalities` | Returns per-column modality map (`{field: "image"}`) |
| `GET /api/v1/datasets/{id}/media?field=F&row=N` | Returns media bytes for row N's field F |
| `GET /api/v1/tasks/execution/{task_id}/media?field=F&row=N&step=S` | Returns media bytes from a task's output at step S |

Paths taken from registered records are validated for existence and media-extension before serving. The `MEDIA_ALLOWED_ROOTS` setting (default: unrestricted) can limit serving to a directory whitelist.
