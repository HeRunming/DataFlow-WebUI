"""Stub speech operator (speech-to-text)."""
from __future__ import annotations

import os

from dataflow.core import OperatorABC  # type: ignore[import]
from dataflow.utils.registry import OPERATOR_REGISTRY  # type: ignore[import]
from dataflow.utils.storage import DataFlowStorage  # type: ignore[import]


@OPERATOR_REGISTRY.register()
class AudioTranscribeStub(OperatorABC):
    """Reads audio file-path fields and writes placeholder transcripts.

    Compatible with ASR operators that follow the
    ``run(storage, input_audio_key, output_key)`` convention.
    """

    def __init__(self, system_prompt: str = "stub"):
        self.system_prompt = system_prompt

    @staticmethod
    def get_desc(lang: str = "zh") -> str:
        if lang == "zh":
            return (
                "[Stub] 读取音频路径字段，写入占位转录文本。仅用于多模态管线兼容性验证，不调用真实 ASR 模型。\n"
                "输入参数：\n"
                "- input_audio_key：输入音频路径字段名（默认 audio_path）\n"
                "- output_key：输出转录字段名（默认 transcript）"
            )
        if lang == "en":
            return (
                "[Stub] Reads audio file-path fields and writes placeholder transcripts. "
                "For multimodal pipeline compatibility testing only — no real ASR model is called.\n"
                "Parameters:\n"
                "- input_audio_key: input audio-path column (default audio_path)\n"
                "- output_key: output transcript column (default transcript)"
            )
        return "[Stub] AudioTranscribeStub writes placeholder transcripts for audio-path columns."

    def run(
        self,
        storage: DataFlowStorage,
        input_audio_key: str = "audio_path",
        output_key: str = "transcript",
    ) -> str:
        self.input_audio_key, self.output_key = input_audio_key, output_key
        df = storage.read("dataframe")
        transcripts = []
        for _, row in df.iterrows():
            val = row.get(input_audio_key, "")
            filename = os.path.basename(str(val)) if val else "(none)"
            transcripts.append(f"[stub transcript of {filename}]")
        df[output_key] = transcripts
        storage.write(df)
        return output_key
