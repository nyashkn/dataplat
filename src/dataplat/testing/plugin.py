"""pytest plugin, auto-loaded through the ``pytest11`` entry point when dataplat is installed.

Fixtures
    lake_config   a throwaway lake: DuckDB catalog and data files under tmp_path, namespace "test"
    lake          ``Lake`` on that config
    writer        ``Writer`` on that lake
    dbos_runtime  DBOS launched on a temp SQLite system database, destroyed after the test

Telemetry
    Tests never export: ``DATAPLAT_TELEMETRY=off`` is set for the session, even when ``.env`` points
    ``OTEL_EXPORTER_OTLP_ENDPOINT`` at an OpenObserve.

Live-fire gate
    ``pytest --livefire-strict`` imports every module under the ini option ``dataplat_check_packages``,
    then fails the session if any registered check was never live-fired
    (``dataplat.checks.livefire.assert_catches``). ``just test-all`` and CI run with this flag.
"""

from __future__ import annotations

import importlib
import os
import pkgutil
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest


def pytest_configure(config: pytest.Config) -> None:
    os.environ["DATAPLAT_TELEMETRY"] = "off"


def pytest_addoption(parser: pytest.Parser) -> None:
    group = parser.getgroup("dataplat")
    group.addoption(
        "--livefire-strict",
        action="store_true",
        default=False,
        help="fail if a registered check was never live-fired in the failing direction",
    )
    parser.addini(
        "dataplat_check_packages",
        type="linelist",
        default=[],
        help="packages whose modules register checks (imported for the live-fire gate)",
    )


@pytest.fixture
def lake_config(tmp_path: Path) -> Any:
    from dataplat.config import LakeConfig

    return LakeConfig(
        catalog=f"duckdb:{tmp_path}/catalog/meta.ducklake",
        data_path=f"{tmp_path}/data/",
        namespace="test",
        code_version="test",
    )


@pytest.fixture
def lake(lake_config: Any) -> Iterator[Any]:
    from dataplat.lakecore import Lake

    lk = Lake(lake_config)
    yield lk
    lk.close()


@pytest.fixture
def writer(lake: Any) -> Any:
    from dataplat.lakecore.write import Writer

    return Writer(lake)


@pytest.fixture
def dbos_runtime(tmp_path: Path) -> Iterator[Any]:
    from dbos import DBOS

    from dataplat.runtime import dbos_config, launch

    launch(dbos_config("test", system_database_url=f"sqlite:///{tmp_path}/dbos.sqlite", code_version="test"))
    try:
        yield DBOS
    finally:
        DBOS.destroy(destroy_registry=False)


def _import_all(pkg_name: str) -> None:
    pkg = importlib.import_module(pkg_name)
    for info in pkgutil.walk_packages(getattr(pkg, "__path__", []), prefix=pkg_name + "."):
        importlib.import_module(info.name)


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    if not session.config.getoption("--livefire-strict") or exitstatus != 0:
        return
    for name in session.config.getini("dataplat_check_packages"):
        _import_all(name)
    from dataplat.checks.livefire import fired
    from dataplat.checks.registry import registered

    regs = registered()
    missing = sorted(set(regs) - fired())
    reporter = session.config.pluginmanager.get_plugin("terminalreporter")
    if missing:
        lines = [
            "",
            "LIVE-FIRE GATE FAILED: these registered checks were never shown to catch a bad row:",
            *(f"  - {n}  ({regs[n].module})" for n in missing),
            "Add a test calling dataplat.checks.livefire.assert_catches(<check>, clean_df, <mutation>).",
        ]
        for line in lines:
            if reporter is not None:
                reporter.write_line(line, red=True, bold=bool(line.startswith("LIVE")))
        session.exitstatus = pytest.ExitCode.TESTS_FAILED
    elif reporter is not None:
        reporter.write_line(f"live-fire gate: {len(regs)} registered checks, all live-fired", green=True)
