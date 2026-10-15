"""mm_stubs — multimodal stub operators for end-to-end compatibility testing.

Loaded via _DATAFLOW_EXTENSIONS = ["mm_stubs"] in config.py.
Operators are stubs only: they read file-path-valued columns and write
deterministic placeholder values without calling any real model.
"""
from .core_vision import generate as _cv_gen
from .core_speech import generate as _cs_gen
from .core_text import filter as _ct_filter

__all__ = [
    "ImageCaptionStub",
    "ImageGenStub",
    "AudioTranscribeStub",
    "FieldPresentFilterStub",
]

ImageCaptionStub = _cv_gen.ImageCaptionStub
ImageGenStub = _cv_gen.ImageGenStub
AudioTranscribeStub = _cs_gen.AudioTranscribeStub
FieldPresentFilterStub = _ct_filter.FieldPresentFilterStub
