from __future__ import annotations

import sys
import textwrap
import types

import pytest

from dataplat.runtime import dbos_config, run_dag, workflow_id


@pytest.fixture
def dag_module(tmp_path, monkeypatch) -> types.ModuleType:  # type: ignore[no-untyped-def]
    (tmp_path / "tiny_dag.py").write_text(
        textwrap.dedent(
            """
            def doubled(x: int) -> int:
                return x * 2

            def plus_one(doubled: int) -> int:
                return doubled + 1
            """
        )
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    import tiny_dag

    yield tiny_dag
    sys.modules.pop("tiny_dag", None)


def test_run_dag_takes_function_objects(dag_module: types.ModuleType) -> None:
    out = run_dag(dag_module, final=[dag_module.plus_one], inputs={"x": 20})
    assert out[dag_module.plus_one] == 41


def test_run_dag_rejects_name_strings(dag_module: types.ModuleType) -> None:
    with pytest.raises(TypeError, match="function object"):
        run_dag(dag_module, final=["plus_one"], inputs={"x": 1})  # type: ignore[list-item]


def test_workflow_ids_are_deterministic_and_safe() -> None:
    assert workflow_id("usage_daily", "2026-09-01") == "usage_daily:2026-09-01"
    assert workflow_id("usage_daily", "2026-09-01", attempt="r2") == "usage_daily:2026-09-01:r2"
    with pytest.raises(ValueError):
        workflow_id("usage daily", "x")


def test_dbos_config_namespaces_the_app() -> None:
    c = dbos_config("example", system_database_url="sqlite:///x", code_version="abc", namespace="feat_x")
    assert c["name"] == "example-feat_x" and c["application_version"] == "abc"
