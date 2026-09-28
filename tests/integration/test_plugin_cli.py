from __future__ import annotations

import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

CHECKS_MODULE = textwrap.dedent(
    """
    import polars as pl
    from dataplat.checks import check

    @check
    def covered_check() -> pl.Expr:
        \"\"\"x is positive.\"\"\"
        return pl.col("x") > 0

    @check
    def forgotten_check() -> pl.Expr:
        \"\"\"y is positive.\"\"\"
        return pl.col("y") > 0
    """
)
TEST_MODULE = textwrap.dedent(
    """
    import polars as pl
    from dataplat.checks import livefire as lf
    from lfpkg.checks import covered_check

    def test_covered():
        lf.assert_catches(covered_check, pl.DataFrame({"x": [1, 2]}), lf.set_value("x", -1))
    """
)


def _project(pytester: pytest.Pytester) -> None:
    pkg = pytester.mkpydir("lfpkg")
    (pkg / "checks.py").write_text(CHECKS_MODULE)
    pytester.makeini("[pytest]\ndataplat_check_packages = lfpkg\n")
    pytester.makepyfile(test_lf=TEST_MODULE)
    pytester.syspathinsert()


def test_livefire_gate_fails_on_uncovered_checks(pytester: pytest.Pytester) -> None:
    _project(pytester)
    result = pytester.runpytest_subprocess("--livefire-strict")
    result.stdout.fnmatch_lines(["*LIVE-FIRE GATE FAILED*", "*forgotten_check*"])
    assert result.ret == pytest.ExitCode.TESTS_FAILED


def test_livefire_gate_is_off_without_the_flag(pytester: pytest.Pytester) -> None:
    _project(pytester)
    assert pytester.runpytest_subprocess().ret == pytest.ExitCode.OK


PYPROJECT = textwrap.dedent(
    """
    [project]
    name = "demo"
    version = "0"
    [tool.dataplat]
    package = "demo"
    forbidden_columns = ["national_id"]
    [tool.dataplat.contracts]
    tables = "src/demo/contracts/tables.yaml"
    tables_out = "src/demo/contracts/_generated/tables.py"
    """
)
TABLES = textwrap.dedent(
    """
    id: https://example.org/demo
    name: demo
    prefixes: {linkml: https://w3id.org/linkml/}
    imports: [linkml:types]
    default_range: string
    slots:
      day: {range: date, required: true}
      n: {range: integer}
    classes:
      Daily:
        annotations: {dataplat.table: demo.daily, dataplat.partition_by: day, dataplat.cadence: daily, dataplat.expected_start: "2026-01-01"}
        slots: [day, n]
    """
)


def _run(args: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith("DATAPLAT_")}
    return subprocess.run(
        [sys.executable, "-m", "dataplat.cli", *args], cwd=cwd, capture_output=True, text=True, env=env
    )


def test_contracts_gen_and_stale_check(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text(PYPROJECT)
    schema = tmp_path / "src/demo/contracts/tables.yaml"
    schema.parent.mkdir(parents=True)
    schema.write_text(TABLES)
    assert _run(["contracts", "check"], tmp_path).returncode == 1  # nothing generated yet
    gen = _run(["contracts", "gen"], tmp_path)
    assert gen.returncode == 0, gen.stderr
    assert _run(["contracts", "check"], tmp_path).returncode == 0
    generated = (tmp_path / "src/demo/contracts/_generated/tables.py").read_text()
    assert "DAILY = TableContract(" in generated
    schema.write_text(TABLES.replace("n: {range: integer}", "n: {range: integer, required: true}"))
    stale = _run(["contracts", "check"], tmp_path)
    assert stale.returncode == 1 and "STALE" in stale.stdout and "required=True" in stale.stdout


def test_lint_cli_reports_and_exits_nonzero(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text(PYPROJECT)
    mod = tmp_path / "src/demo/transforms/bad.py"
    mod.parent.mkdir(parents=True)
    mod.write_text("def f(df):\n    df.write_parquet('x.parquet')\n")
    out = _run(["lint"], tmp_path)
    assert out.returncode == 1 and "src/demo/transforms/bad.py:2:4: DPA001" in out.stdout
    mod.write_text("def f(df):\n    return df\n")
    assert _run(["lint"], tmp_path).returncode == 0
