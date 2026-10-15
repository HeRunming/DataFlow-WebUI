# Multimodal Operator Integration Guide

This guide is for developers (or agents) writing operators that read or produce image, audio, or video data in the DataFlow-WebUI harness. It covers the operator contract, how to package an extension, the serving registration path, and the compile-time validation behaviour.

## Operator contract

All multimodal operators follow the same `OperatorABC` contract as text operators. There is no new base class.

```python
from dataflow.core import OperatorABC
from dataflow.utils.registry import OPERATOR_REGISTRY
from dataflow.utils.storage import DataFlowStorage

@OPERATOR_REGISTRY.register()
class MyImageCaptioner(OperatorABC):

    def __init__(self, llm_serving, system_prompt: str = "Caption this image."):
        self.llm_serving = llm_serving
        self.system_prompt = system_prompt

    @staticmethod
    def get_desc(lang: str = "zh") -> str:
        if lang == "zh":
            return "读取图像路径字段，生成标题文本。\n..."
        if lang == "en":
            return "Reads image-path fields and generates caption text.\n..."
        return "MyImageCaptioner generates captions for image fields."

    def run(
        self,
        storage: DataFlowStorage,
        input_image_key: str = "image_path",
        output_key: str = "caption",
    ) -> str:
        df = storage.read("dataframe")
        # ... process rows, write captions ...
        df[output_key] = captions
        storage.write(df)
        return output_key
```

### Key points

**Parameter name conventions matter.** The harness infers field modality from parameter names to provide validation warnings and UI hints. Use the naming patterns from [data-format-spec.md](data-format-spec.md):

- `input_image_key` / `*_image_path` → harness infers this parameter expects an image column
- `input_audio_key` / `*_audio_path` → audio
- `input_video_key` / `*_video_path` → video
- `output_key` → text output (no special inference)
- `output_*_path` → media output path

**Generated media files must use absolute paths.** Write output files under `storage.cache_path` and return an absolute path as the field value. See `ImageGenStub` in `backend/dataflow_extensions/mm_stubs/core_vision/generate.py` for the pattern:

```python
media_dir = os.path.join(storage.cache_path, "media")
os.makedirs(media_dir, exist_ok=True)
png_path = os.path.abspath(os.path.join(media_dir, f"gen_{i}.png"))
with open(png_path, "wb") as f:
    f.write(image_bytes)
df[output_key] = png_path  # absolute path
```

**Do not hardcode filesystem paths.** Use `storage.cache_path` (the per-node cache directory managed by the harness) for output files so they are cleaned up correctly and the media endpoint can locate them.

**`get_desc()` must support `zh` and `en` and return a fallback.** The operator registry and the MCP tools both call it with an explicit language code.

## Packaging an extension

Extensions live in `backend/dataflow_extensions/`. Each extension is a regular Python package directory — no `pip install` needed.

Directory layout (using `mm_stubs` as the example):

```
backend/
  dataflow_extensions/
    mm_stubs/
      __init__.py        ← imports all submodules so one entry loads everything
      core_vision/
        __init__.py
        generate.py      ← ImageCaptionStub, ImageGenStub
      core_speech/
        __init__.py
        generate.py      ← AudioTranscribeStub
      core_text/
        __init__.py
        filter.py        ← FieldPresentFilterStub
```

### Registering the extension

Add the package name to `_DATAFLOW_EXTENSIONS` in `backend/app/core/config.py`:

```python
_DATAFLOW_EXTENSIONS = [
    "mm_stubs",
    # "my_new_extension",
]
```

The shared loader (`app/core/extension_loader.py`) inserts `backend/dataflow_extensions` onto `sys.path` and imports each listed module. It is called at startup by both the main-process container (`app/core/container.py`) and the Ray worker (`ray_pipeline_executor.py`).

### Category inference

The harness derives the UI category for each operator from its `__module__` path. For operators in `dataflow.operators.*` the standard DataFlow category names are used directly. For operators in `mm_stubs.*`, the harness maps `mm_stubs.<level_1>.<file>` to `<level_1>`, so `mm_stubs.core_vision.generate` → category `core_vision`.

If your extension uses a different namespace, extend the `_gather_single_operator` function in `backend/app/services/operator_registry.py` with an `elif parts[0] == "my_ext":` branch following the same pattern.

## Serving registration

Multimodal operators that call external VLM or ASR APIs need a serving instance. The harness provides a registration hook:

```python
from app.services.serving_registry import register_serving_class

class MultimodalAPIServing(LLMServingABC):
    def __init__(self, api_key: str, model: str = "gpt-4o", ...):
        ...
    def generate_from_input(self, user_inputs, system_prompt):
        ...

# Call this from the extension's __init__.py, before the container initialises.
register_serving_class("MultimodalAPIServing", MultimodalAPIServing)
```

Once registered, `MultimodalAPIServing` appears in the serving registry under that cls_name and pipeline configs can reference it via `llm_serving`. Because multimodal operators (`Speech2TextGenerator`, `PromptedVQAGenerator`, stub operators) receive their serving instance through the standard `llm_serving` init parameter, the existing serving dispatch path works with no harness changes.

## Pipeline validation

When you add an operator with media-typed parameters to a pipeline, the harness static validator (`validate_pipeline_config`) performs two checks:

1. **Modality mismatch warning.** If `input_image_key` is bound to a column whose sampled values look like text (no image extension), a `media_modality_mismatch` warning is raised with the list of actually-image-typed columns as suggestions.

2. **Missing media file error.** If the referenced column's sampled values point to local paths that do not exist on disk, a `media_file_not_found` error is raised. The repair hint asks the user to verify the path and anchor relative paths to the dataset root directory.

Both checks apply only to the input dataset columns visible to the operator (i.e. for root nodes and nodes that inherit from the root). Fields produced by upstream operators are not checked (since they don't exist until runtime).

## Demo pipeline

`backend/tests/fixtures/mm_demo_pipeline.json` is a reference configuration that exercises fan-out, merge, and all three media stub operators:

```
Dataset (id, image_path, audio_path, prompt)
    ↓                        ↓
ImageCaptionStub       AudioTranscribeStub
  (cap → caption)        (asr → transcript)
         ↓                    ↓
              merge (cap+asr)
                    ↓
              ImageGenStub
           (gen → generated_image_path)
```

After execution:
- Merged records contain `caption`, `caption_model`, `transcript`, and `generated_image_path`.
- `operators_detail` carries `node_id`, `output_file`, `input_count`, and `conversion_rate` for each node.

Run `backend/tests/test_mm_stubs_e2e.py` to reproduce this end-to-end (requires `open-dataflow` installed; the test is skipped automatically if the package is absent).
