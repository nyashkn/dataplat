"""Answer gate: preconditions, then an Answer with provenance, or a Refusal with evidence."""

from dataplat.gate.answer import (
    Answer,
    Precondition,
    Provenance,
    Refusal,
    answer,
    fresh_within,
    min_rows,
    partitions_complete,
)

__all__ = [
    "Answer",
    "Precondition",
    "Provenance",
    "Refusal",
    "answer",
    "fresh_within",
    "min_rows",
    "partitions_complete",
]
