"""LinkML to TableContract.

A LinkML class becomes a table when it carries a ``dataplat.table`` annotation::

    classes:
      EventsClean:
        annotations:
          dataplat.table: usage.events_clean
          dataplat.partition_by: event_date
          dataplat.grain: none            # or "a,b"
          dataplat.cadence: daily
          dataplat.expected_start: "2023-01-01"
        slots: [event_date, phone_raw, phone_number, ...]

Slot ranges map to dtypes (string, integer, float, double, boolean, date, datetime, enums). A slot can
override its dtype with ``annotations: {dataplat.dtype: "decimal(18,4)"}``. ``decimal`` has no default:
money needs an explicit precision and is never stored as a float.
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
from typing import Any

from dataplat.contracts.model import Column, TableContract
from dataplat.errors import ContractError

_RANGES = {
    "string": "string",
    "str": "string",
    "uri": "string",
    "uriorcurie": "string",
    "integer": "int64",
    "int": "int64",
    "float": "float64",
    "double": "float64",
    "boolean": "bool",
    "bool": "bool",
    "date": "date",
    "datetime": "datetime_utc",
}


def _annotations(element: Any) -> dict[str, str]:
    """Annotations as {tag: value}. Classes give a dict, induced slots a JsonObj; accept both."""
    anns = getattr(element, "annotations", None) or {}
    if isinstance(anns, dict):
        items = list(anns.items())
    elif hasattr(anns, "_items"):
        items = list(anns._items())
    else:
        items = [(getattr(a, "tag", a), a) for a in anns]
    return {str(tag): str(getattr(a, "value", a)) for tag, a in items if not str(tag).startswith("_")}


def load_tables(schema_path: Path, *, forbidden_columns: Iterable[str] = ()) -> list[TableContract]:
    from linkml_runtime.utils.schemaview import SchemaView

    sv = SchemaView(str(schema_path))
    forbidden = {c.lower() for c in forbidden_columns}
    contracts: list[TableContract] = []
    for cname in sv.all_classes():
        cls = sv.get_class(cname)
        ann = _annotations(cls)
        if "dataplat.table" not in ann:
            continue
        columns: list[Column] = []
        for sname in sv.class_slots(cname):
            slot = sv.induced_slot(sname, cname)
            if slot.name.lower() in forbidden:
                raise ContractError(
                    f"{cname}.{slot.name} is a forbidden column ([tool.dataplat] forbidden_columns). "
                    "Restricted fields never enter the lake; drop them in the source extract."
                )
            sann = _annotations(slot)
            rng = str(slot.range or sv.schema.default_range or "string")
            allowed: tuple[str, ...] | None = None
            if "dataplat.dtype" in sann:
                dtype = sann["dataplat.dtype"]
            elif rng in sv.all_enums():
                dtype = "string"
                allowed = tuple(str(v) for v in sv.get_enum(rng).permissible_values)
            elif rng == "decimal":
                raise ContractError(
                    f"{cname}.{slot.name}: decimal needs annotations: {{dataplat.dtype: 'decimal(p,s)'}}"
                )
            elif rng in _RANGES:
                dtype = _RANGES[rng]
            else:
                raise ContractError(f"{cname}.{slot.name}: unsupported range {rng!r}")
            columns.append(
                Column(
                    name=slot.name,
                    dtype=dtype,
                    required=bool(slot.required),
                    pattern=slot.pattern,
                    allowed=allowed,
                    minimum=_num(slot.minimum_value),
                    maximum=_num(slot.maximum_value),
                    description=(slot.description or "").strip(),
                )
            )
        grain = ann.get("dataplat.grain", "none").strip()
        contracts.append(
            TableContract(
                name=cname,
                table=ann["dataplat.table"],
                columns=tuple(columns),
                partition_by=ann.get("dataplat.partition_by") or None,
                grain=() if grain in ("", "none") else tuple(g.strip() for g in grain.split(",")),
                cadence=ann.get("dataplat.cadence") or None,
                expected_start=ann.get("dataplat.expected_start") or None,
                description=(cls.description or "").strip(),
            )
        )
    return contracts


def _num(v: Any) -> float | None:
    return None if v is None else float(v)
