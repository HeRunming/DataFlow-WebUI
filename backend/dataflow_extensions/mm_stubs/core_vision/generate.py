"""Stub vision operators (image captioning and text-to-image).

These return deterministic placeholder values without calling any model.
Their purpose is to make the harness verifiably compatible with operators
that read and write file-path-valued columns.
"""
from __future__ import annotations

import os

from dataflow.core import OperatorABC  # type: ignore[import]
from dataflow.utils.registry import OPERATOR_REGISTRY  # type: ignore[import]
from dataflow.utils.storage import DataFlowStorage  # type: ignore[import]

_STUB_PNG = (
    b"\x89PNG\r\n\x1a\n"
    b"\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x02\x00\x00\x00\x90wS\xde"
    b"\x00\x00\x00\x0cIDATx\x9cc\xf8\x0f\x00\x00\x01\x01\x00\x05\x18\xd8N"
    b"\x00\x00\x00\x00IEND\xaeB`\x82"
)


@OPERATOR_REGISTRY.register()
class ImageCaptionStub(OperatorABC):
    """Reads image paths and writes placeholder caption text.

    Compatible with VQA / image-to-text operators that follow the
    ``run(storage, input_image_key, output_key)`` convention.
    """

    def __init__(self, system_prompt: str = "stub"):
        self.system_prompt = system_prompt

    @staticmethod
    def get_desc(lang: str = "zh") -> str:
        if lang == "zh":
            return (
                "[Stub] 读取图像路径字段，写入占位标题文本。仅用于多模态管线兼容性验证，不调用真实模型。\n"
                "输入参数：\n"
                "- input_image_key：输入图像路径字段名（默认 image_path）\n"
                "- output_key：输出标题字段名（默认 caption）\n"
                "输出：为每行写入 '[stub caption for <文件名>]' 和 caption_model 字段"
            )
        if lang == "en":
            return (
                "[Stub] Reads image path fields and writes placeholder caption text. "
                "For multimodal pipeline compatibility testing only — no real model is called.\n"
                "Parameters:\n"
                "- input_image_key: input image-path column (default image_path)\n"
                "- output_key: output caption column (default caption)"
            )
        return "[Stub] ImageCaptionStub writes placeholder captions for image-path columns."

    def run(
        self,
        storage: DataFlowStorage,
        input_image_key: str = "image_path",
        output_key: str = "caption",
    ) -> str:
        self.input_image_key, self.output_key = input_image_key, output_key
        df = storage.read("dataframe")
        captions = []
        for _, row in df.iterrows():
            val = row.get(input_image_key, "")
            filename = os.path.basename(str(val)) if val else "(none)"
            captions.append(f"[stub caption for {filename}]")
        df[output_key] = captions
        df["caption_model"] = "ImageCaptionStub/0.1"
        storage.write(df)
        return output_key


@OPERATOR_REGISTRY.register()
class ImageGenStub(OperatorABC):
    """Reads a text prompt field and writes placeholder 1×1 PNG paths.

    Compatible with text-to-image operators that follow the
    ``run(storage, input_key, output_key)`` convention.
    The generated PNG files are written to the storage cache directory.
    """

    def __init__(self, system_prompt: str = "stub"):
        self.system_prompt = system_prompt

    @staticmethod
    def get_desc(lang: str = "zh") -> str:
        if lang == "zh":
            return (
                "[Stub] 读取文本提示字段，写入 1×1 占位 PNG 文件的绝对路径。"
                "仅用于多模态管线兼容性验证，不调用真实生图模型。\n"
                "输入参数：\n"
                "- input_key：输入提示字段名（默认 prompt）\n"
                "- output_key：输出图像路径字段名（默认 generated_image_path）"
            )
        if lang == "en":
            return (
                "[Stub] Reads text prompt fields and writes absolute paths to 1×1 placeholder PNG files. "
                "For multimodal pipeline compatibility testing only — no real image generation is done.\n"
                "Parameters:\n"
                "- input_key: input prompt column (default prompt)\n"
                "- output_key: output image-path column (default generated_image_path)"
            )
        return "[Stub] ImageGenStub writes 1×1 PNG placeholder files for prompt columns."

    def run(
        self,
        storage: DataFlowStorage,
        input_key: str = "prompt",
        output_key: str = "generated_image_path",
    ) -> str:
        self.input_key, self.output_key = input_key, output_key
        df = storage.read("dataframe")
        media_dir = os.path.join(storage.cache_path, "media")
        os.makedirs(media_dir, exist_ok=True)
        paths = []
        for i, _ in df.iterrows():
            png_path = os.path.abspath(os.path.join(media_dir, f"gen_{i}.png"))
            with open(png_path, "wb") as f:
                f.write(_STUB_PNG)
            paths.append(png_path)
        df[output_key] = paths
        storage.write(df)
        return output_key
