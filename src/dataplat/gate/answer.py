"""The answer gate: preconditions first, then a number that carries its provenance.

Agents and dashboards ask questions through ``answer()``. Each question declares the conditions under
which its number is trustworthy, for example "every day in the window is present" or "no zero-row
partitions". If any condition is FAIL or INCONCLUSIVE the caller gets a ``Refusal`` listing the
evidence, never a best-effort number. An ``Answer`` carries the snapshot id, the datasets and the
evidence, so anyone can re-run it and get the same result.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import date
from typing import Any

import polars as pl

from dataplat.checks.verdict import Evidence, Verdict
from dataplat.contracts.model import TableContract
from dataplat.lakecore.read import Reader
from dataplat.trust.census import days_between, today_utc


@dataclass(frozen=True)
class Provenance:
    snapshot: int
    namespace: str
    code_version: str
    datasets: tuple[str, ...]
    evidence: tuple[Evidence, ...]
    basis: str = "measured"


@dataclass(frozen=True)
class Answer[T]:
    question: str
    value: T
    provenance: Provenance

    ok: bool = field(default=True, init=False)


@dataclass(frozen=True)
class Refusal:
    question: str
    reasons: tuple[Evidence, ...]
    provenance: Provenance

    ok: bool = field(default=False, init=False)

    def __str__(self) -> str:
        return f"REFUSED: {self.question}\n" + "\n".join(f"  - {e}" for e in self.reasons)


@dataclass(frozen=True)
class Precondition:
    name: str
    evaluate: Callable[[Reader], Evidence]


def answer[T](
    question: str,
    *,
    reader: Reader,
    compute: Callable[[Reader], T],
    preconditions: Sequence[Precondition] = (),
    datasets: Sequence[TableContract] = (),
) -> Answer[T] | Refusal:
    evidence = tuple(p.evaluate(reader) for p in preconditions)
    prov = Provenance(
        snapshot=reader.snapshot,
        namespace=reader.cfg.namespace,
        code_version=reader.cfg.code_version,
        datasets=tuple(c.table for c in datasets),
        evidence=evidence,
    )
    blocking = tuple(e for e in evidence if e.verdict is not Verdict.PASS)
    if blocking:
        return Refusal(question, blocking, prov)
    return Answer(question, compute(reader), prov)


# ---------------------------------------------------------------- standard preconditions
def _log(reader: Reader, contract: TableContract) -> pl.DataFrame:
    return reader.runlog().filter(pl.col("dataset") == contract.table)


def partitions_complete(contract: TableContract, start: date, end: date) -> Precondition:
    """Every daily partition in [start, end] was committed (with rows)."""
    name = f"partitions_complete[{contract.table} {start}..{end}]"

    def ev(reader: Reader) -> Evidence:
        if contract.cadence != "daily":
            return Evidence(name, Verdict.INCONCLUSIVE, detail="contract declares no daily cadence")
        want = set(days_between(start, end))
        log = _log(reader, contract)
        have = set(log.filter(pl.col("rows") > 0)["partition"].to_list())
        zero = set(log.filter(pl.col("rows") == 0)["partition"].to_list()) & want
        missing = sorted(want - have - zero)
        measured: dict[str, Any] = {"expected": len(want), "missing": len(missing), "zero_rows": len(zero)}
        if missing:
            measured["first_missing"] = missing[:5]
        if missing or zero:
            return Evidence(name, Verdict.FAIL, measured, detail="blocked metric: partial window")
        return Evidence(name, Verdict.PASS, measured)

    return Precondition(name, ev)


def fresh_within(contract: TableContract, max_lag_days: int, *, today: date | None = None) -> Precondition:
    name = f"fresh_within[{contract.table} <= {max_lag_days}d]"

    def ev(reader: Reader) -> Evidence:
        log = _log(reader, contract).filter(pl.col("rows") > 0)
        if log.height == 0:
            return Evidence(name, Verdict.FAIL, {"partitions": 0}, detail="dataset has no data")
        last = date.fromisoformat(max(log["partition"].to_list()))
        lag = ((today or today_utc()) - last).days
        return Evidence(name, Verdict.PASS if lag <= max_lag_days else Verdict.FAIL, {"lag_days": lag})

    return Precondition(name, ev)


def min_rows(contract: TableContract, start: date, end: date, floor: int) -> Precondition:
    """At least ``floor`` rows in the window; below it the answer is INCONCLUSIVE (too small to mean much)."""
    name = f"min_rows[{contract.table} >= {floor}]"

    def ev(reader: Reader) -> Evidence:
        days = days_between(start, end)
        n = int(_log(reader, contract).filter(pl.col("partition").is_in(days))["rows"].sum())
        return Evidence(name, Verdict.PASS if n >= floor else Verdict.INCONCLUSIVE, {"rows": n, "floor": floor})

    return Precondition(name, ev)
