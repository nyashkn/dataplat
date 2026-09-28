"""Hamilton validator that runs a pandera-polars schema on a transform's output.

Usage in a transform module::

    @check_output_custom(PanderaPolars(EVENTS_CLEAN.pandera_schema()))
    def events_clean(events_typed: pl.DataFrame) -> pl.DataFrame: ...

Failure messages carry counts and shape classes, never row values.
"""

from __future__ import annotations

from typing import Any

import pandera.errors as pae
import pandera.polars as pa
import polars as pl
from hamilton.data_quality.base import DataValidator, ValidationResult

from dataplat.checks.pandera_errors import summarize


class PanderaPolars(DataValidator):
    def __init__(self, schema: pa.DataFrameSchema, importance: str = "fail") -> None:
        super().__init__(importance=importance)
        self.schema = schema

    def applies_to(self, datatype: type[type]) -> bool:
        return isinstance(datatype, type) and issubclass(datatype, pl.DataFrame)

    def description(self) -> str:
        return f"pandera-polars schema {self.schema.name}"

    @classmethod
    def name(cls) -> str:
        return "pandera_polars"

    def validate(self, dataset: Any) -> ValidationResult:
        try:
            self.schema.validate(dataset, lazy=True)
        except (pae.SchemaErrors, pae.SchemaError) as err:
            message, groups = summarize(err, self.schema.name or "schema")
            return ValidationResult(passes=False, message=message, diagnostics={"groups": groups})
        return ValidationResult(passes=True, message=f"{self.schema.name}: ok")
