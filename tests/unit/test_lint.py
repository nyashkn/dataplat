from __future__ import annotations

import textwrap

import pytest

from dataplat.lint.scan import scan_source


def codes(src: str, module_path: str = "src/pkg/transforms/x.py") -> list[str]:
    return [f.code for f in scan_source(textwrap.dedent(src), module_path, "pkg")]


@pytest.mark.parametrize(
    ("src", "code"),
    [
        ("df.write_parquet('x.parquet')", "DPA001"),
        ("lf.sink_parquet(path)", "DPA001"),
        ("pdf.to_parquet('s3://b/k')", "DPA001"),
        ("con.execute(\"COPY (SELECT 1) TO 'out.parquet' (FORMAT parquet)\")", "DPA002"),
        ('con.execute(f"COPY t TO {path} (FORMAT parquet)")', "DPA002"),
        ("import os\nos.listdir('/data')", "DPA003"),
        ("from pathlib import Path\nPath('/lake').rglob('*.parquet')", "DPA003"),
        ("fs.ls('bucket/prefix')", "DPA003"),
        ("con.execute(\"SELECT * FROM glob('s3://lake/**')\")", "DPA003"),
        ("con.sql(f\"SELECT * FROM read_parquet('{root}/*/part-*.parquet')\")", "DPA003"),
        ("dr.execute(['events_clean'])", "DPA006"),
        ("run_dag(m, final=['events_clean'], inputs={})", "DPA006"),
    ],
)
def test_rules_fire(src: str, code: str) -> None:
    assert code in codes(src)


def test_writer_import_only_in_pipelines() -> None:
    src = "from dataplat.lakecore.write import Writer\n"
    assert "DPA004" in codes(src, "src/pkg/semantic/metrics.py")
    assert "DPA004" in codes(src, "src/pkg/cli/main.py")
    assert codes(src, "src/pkg/pipelines/usage_daily.py") == []


def test_pure_layers_cannot_touch_the_lake() -> None:
    assert "DPA005" in codes("from dataplat.lakecore import connect\n", "src/pkg/transforms/a.py")
    assert "DPA005" in codes("import dataplat.lakecore\n", "src/pkg/sources/b.py")
    assert codes("from dataplat.lakecore import connect\n", "src/pkg/semantic/c.py") == []


def test_clean_code_and_docstrings_pass() -> None:
    src = '''
    """Docs may say COPY t TO 'x' and os.listdir without tripping the scanner."""
    import polars as pl
    from pkg.transforms import events

    def f(df: pl.DataFrame) -> pl.DataFrame:
        return df.filter(pl.col("x") > 1)

    out = run_dag(events, final=[events.events_clean], inputs={})
    '''
    assert codes(src) == []


def test_allow_comment_needs_a_reason() -> None:
    assert codes("df.write_parquet('x')  # dataplat: allow[DPA001] -- fixture export for docs\n") == []
    assert codes("df.write_parquet('x')  # dataplat: allow[DPA001]\n") == ["DPA000"]
    assert codes("df.write_parquet('x')  # dataplat: allow[DPA003] -- wrong code\n") == ["DPA001"]


def test_allow_comment_survives_ruff_wrapping() -> None:
    wrapped = (
        'x = con.execute(\n    f"COPY ({sql}) TO {path} (FORMAT parquet)"\n)  # dataplat: allow[DPA002] -- staging\n'
    )
    assert codes(wrapped, "src/pkg/pipelines/x.py") == []


@pytest.mark.parametrize(
    ("src", "layer_file", "code"),
    [
        ("con.execute('DELETE FROM lake.usage.events WHERE 1=1')", "semantic/q.py", "DPA007"),
        ('con.execute("INSERT INTO usage.events SELECT 1")', "actions/a.py", "DPA007"),
        ("con.execute('ALTER TABLE usage.events ADD COLUMN x INT')", "cli/main.py", "DPA007"),
        ("import os\nos.environ['X']", "transforms/t.py", "DPA008"),
        ("import os\nos.getenv('X')", "transforms/t.py", "DPA008"),
        ("open('f.csv')", "transforms/t.py", "DPA008"),
        ("import polars as pl\npl.read_csv('x.csv')", "transforms/t.py", "DPA008"),
        ("from pathlib import Path\nPath('x').read_text()", "transforms/t.py", "DPA008"),
        ("from pathlib import Path\nPath('/lake').walk()", "pipelines/p.py", "DPA003"),
        ("fs.find('bucket/prefix')", "pipelines/p.py", "DPA003"),
        ("con.read_parquet('s3://lake/x/*.parquet')", "semantic/s.py", "DPA003"),
    ],
)
def test_new_rules_fire(src: str, layer_file: str, code: str) -> None:
    assert code in codes(src, f"src/pkg/{layer_file}")


def test_new_rules_stay_quiet_where_allowed() -> None:
    assert codes("con.execute('DELETE FROM lake.usage.events WHERE d = ?', [d])", "src/pkg/pipelines/p.py") == []
    assert codes("import os\nos.getenv('X')", "src/pkg/sources/s.py") == []
    assert codes("s = 'abc'.find('b')", "src/pkg/transforms/t.py") == []
    assert codes("con.read_parquet('s3://lake/x/part-0.parquet')", "src/pkg/semantic/s.py") == []
