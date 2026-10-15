# Multimodal Operator Contract

This reference covers the additional contract requirements for multimodal operators beyond the baseline DataFlow operator contract. Read `operator-contract.md` for the foundational rules; this document extends them.

## Modality field convention

Multimodal operators communicate which fields they expect through **parameter names**. The harness uses these names to infer modality, validate pipelines, and render UI hints.

Follow these naming conventions for `run()` parameters:

| Convention | Modality | Examples |
|---|---|---|
| `input_image_key`, `*_image_key`, `*_image_path` | image | `input_image_key`, `src_image_key` |
| `input_audio_key`, `*_audio_key`, `*_audio_path`, `speech_key` | audio | `input_audio_key`, `speech_key` |
| `input_video_key`, `*_video_key`, `*_video_path` | video | `input_video_key` |
| `output_key`, `*_output_key` | text/generic output | `output_key`, `caption_output_key` |

These names are matched case-insensitively as whole words or underscore-separated segments (so `imagepath` does not match `image`). The implementation is `infer_param_modality()` in the `media_assets` service module.

## Media path rules

Operators that **read** media must tolerate absolute paths. They do not need to handle relative paths — the harness normalises them before the operator runs. Operators should never `os.chdir()` to resolve paths (the engine already does that for its own purposes; operators run inside the chdir'ed directory).

Operators that **write** media must write to a path derived from `storage.cache_path` and **return an absolute path** as the output field value. The harness's media endpoint locates generated media by reading the `output_file` recorded in `operators_detail`, so all generated paths must be resolvable from the absolute path stored in the field.

```python
# Correct: writes to storage.cache_path and stores absolute path
media_dir = os.path.join(storage.cache_path, "media")
os.makedirs(media_dir, exist_ok=True)
out_path = os.path.abspath(os.path.join(media_dir, f"output_{i}.png"))
df[output_key] = out_path  # absolute path

# Wrong: relative path or path outside cache_path
df[output_key] = "outputs/out.png"  # relative — breaks after chdir
df[output_key] = "/tmp/out.png"      # not in cache — may be cleaned up
```

## Multimodal operator template

Use this template as a starting point. Replace `MyVisionOperator` and the parameter names with your own.

```python
import os
from dataflow.core import OperatorABC
from dataflow.utils.registry import OPERATOR_REGISTRY
from dataflow.utils.storage import DataFlowStorage


@OPERATOR_REGISTRY.register()
class MyVisionOperator(OperatorABC):
    """One-line description of what this operator does."""

    def __init__(self, llm_serving=None, system_prompt: str = "You are a helpful assistant."):
        self.llm_serving = llm_serving
        self.system_prompt = system_prompt

    @staticmethod
    def get_desc(lang: str = "zh") -> str:
        if lang == "zh":
            return (
                "该算子读取图像路径字段，生成...输出...\n"
                "初始化参数：\n"
                "- llm_serving：LLM/VLM服务对象（可选）\n"
                "- system_prompt：系统提示词\n"
                "运行参数：\n"
                "- input_image_key：输入图像路径字段名\n"
                "- output_key：输出字段名"
            )
        if lang == "en":
            return (
                "This operator reads image-path fields and produces ...\n"
                "Init parameters:\n"
                "- llm_serving: LLM/VLM serving object (optional)\n"
                "- system_prompt: system prompt\n"
                "Run parameters:\n"
                "- input_image_key: input image-path column name\n"
                "- output_key: output column name"
            )
        return "MyVisionOperator processes image fields."

    def run(
        self,
        storage: DataFlowStorage,
        input_image_key: str = "image_path",
        output_key: str = "output",
    ) -> str:
        self.input_image_key, self.output_key = input_image_key, output_key
        df = storage.read("dataframe")
        results = []
        for _, row in df.iterrows():
            image_path = row.get(input_image_key, "")
            # image_path is an absolute local path or an http(s) URL.
            # Process it here...
            results.append(process(image_path))
        df[output_key] = results
        storage.write(df)
        return output_key
```

## Stub operators as reference implementations

The `mm_stubs` extension package (`backend/dataflow_extensions/mm_stubs/`) contains three production-pattern stub operators that demonstrate these conventions without calling any real model:

- `ImageCaptionStub` — reads `input_image_key`, writes `caption` and `caption_model`
- `AudioTranscribeStub` — reads `input_audio_key`, writes `transcript`
- `ImageGenStub` — reads `input_key` (text), writes `generated_image_path` (absolute PNG path)
- `FieldPresentFilterStub` — drops rows with an empty field; demonstrates retention rate tracking

For the fan-out+merge+generation demo pipeline, see the fixture `mm_demo_pipeline.json` under `backend/tests/fixtures/` and the end-to-end test `test_mm_stubs_e2e` under `backend/tests/` in the repository root.

## Data format reference

See [docs/multimodal/data-format-spec.md](../../../docs/multimodal/data-format-spec.md) for full details on record schema, path conventions, modality inference, column naming, and merge semantics.
