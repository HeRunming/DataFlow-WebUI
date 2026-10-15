"""Stub text filter operator (drops rows with empty target field)."""
from __future__ import annotations

from dataflow.core import OperatorABC  # type: ignore[import]
from dataflow.utils.registry import OPERATOR_REGISTRY  # type: ignore[import]
from dataflow.utils.storage import DataFlowStorage  # type: ignore[import]


@OPERATOR_REGISTRY.register()
class FieldPresentFilterStub(OperatorABC):
    """Filters out rows where a specified field is empty or null.

    Used in demo pipelines to exercise retention-rate tracking (the ratio of
    output rows to input rows recorded as ``conversion_rate`` in
    ``operators_detail``).
    """

    def __init__(self, field: str = "text"):
        self.field = field

    @staticmethod
    def get_desc(lang: str = "zh") -> str:
        if lang == "zh":
            return (
                "[Stub] 过滤指定字段为空/None 的行，演示留存率统计。\n"
                "初始化参数：\n"
                "- field：要检查的字段名（默认 text）"
            )
        if lang == "en":
            return (
                "[Stub] Filters rows where the specified field is empty or null. "
                "Demonstrates retention-rate tracking in the harness.\n"
                "Init parameters:\n"
                "- field: field to check (default text)"
            )
        return "[Stub] FieldPresentFilterStub removes rows with empty target field."

    def run(self, storage: DataFlowStorage) -> None:
        df = storage.read("dataframe")
        before = len(df)
        df = df[df[self.field].notna() & (df[self.field].astype(str).str.strip() != "")]
        storage.write(df)
        return None
