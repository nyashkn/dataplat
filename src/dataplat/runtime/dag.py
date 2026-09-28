"""Run Hamilton DAGs with function objects as outputs, never name strings.

``final_vars=["events_clean"]`` is string dispatch. After a rename it fails only at run time, and code
search can't see the use (see docs/traps.md, "the near-deletion of normalized_good"). Pass the
function itself::

    out = run_dag(events, final=[events.events_clean], inputs={"events_raw": raw})
    clean = out[events.events_clean]
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from types import ModuleType
from typing import Any

from hamilton import driver


def build_driver(*modules: ModuleType, config: Mapping[str, Any] | None = None) -> driver.Driver:
    return driver.Builder().with_modules(*modules).with_config(dict(config or {})).build()


def run_dag(
    *modules: ModuleType,
    final: Sequence[Callable[..., Any]],
    inputs: Mapping[str, Any],
    config: Mapping[str, Any] | None = None,
    dr: driver.Driver | None = None,
) -> dict[Callable[..., Any], Any]:
    bad = [f for f in final if not callable(f)]
    if bad:
        raise TypeError(
            f"run_dag(final=...) takes functions, not {type(bad[0]).__name__} {bad[0]!r}. "
            "Pass the transform function object so renames break at import time."
        )
    dr = dr or build_driver(*modules, config=config)
    out = dr.execute(list(final), inputs=dict(inputs))
    return {f: out[f.__name__] for f in final}
