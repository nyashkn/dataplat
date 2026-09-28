"""The result vocabulary shared by checks, gates, parity tests and actions.

Three verdicts, not two: INCONCLUSIVE is a first-class answer ("the sample is too small", "the
partition is missing"). Code that cannot decide must say so instead of defaulting to PASS or 0.

Every Evidence says whether it was ``measured`` (computed from data at a pinned snapshot) or
``implied`` (read off code, config or docs). Reports keep the two apart.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, dataclass, field
from enum import StrEnum
from typing import Any, Literal

Basis = Literal["measured", "implied"]


class Verdict(StrEnum):
    PASS = "pass"
    FAIL = "fail"
    INCONCLUSIVE = "inconclusive"


@dataclass(frozen=True)
class Evidence:
    """One finding. ``measured`` holds counts, rates and shape classes only, never raw identifiers."""

    name: str
    verdict: Verdict
    measured: Mapping[str, Any] = field(default_factory=dict)
    basis: Basis = "measured"
    detail: str = ""

    @property
    def ok(self) -> bool:
        return self.verdict is Verdict.PASS

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["verdict"] = str(self.verdict)
        d["measured"] = dict(self.measured)
        return d

    def __str__(self) -> str:
        m = ", ".join(f"{k}={v}" for k, v in self.measured.items())
        return f"[{self.verdict}] {self.name}" + (f" ({m})" if m else "") + (f": {self.detail}" if self.detail else "")


def worst(evidence: list[Evidence] | tuple[Evidence, ...]) -> Verdict:
    """FAIL beats INCONCLUSIVE beats PASS. An empty list is INCONCLUSIVE (nothing was checked)."""
    if not evidence:
        return Verdict.INCONCLUSIVE
    verdicts = {e.verdict for e in evidence}
    if Verdict.FAIL in verdicts:
        return Verdict.FAIL
    if Verdict.INCONCLUSIVE in verdicts:
        return Verdict.INCONCLUSIVE
    return Verdict.PASS
