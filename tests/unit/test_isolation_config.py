from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from dataplat import isolation
from dataplat.config import LakeConfig
from dataplat.errors import ConfigError, IsolationError


@pytest.mark.parametrize(
    ("raw", "ns"),
    [("feat/add-charges", "feat_add_charges"), ("Fix--Phone", "fix_phone"), ("123-hotfix", "ns_123_hotfix")],
)
def test_sanitize(raw: str, ns: str) -> None:
    assert isolation.sanitize(raw) == ns
    assert isolation.validate(ns) == ns


def test_validate_rejects_bad_names() -> None:
    with pytest.raises(IsolationError):
        isolation.validate("Bad-Name")


def _git(*args: str, cwd: Path) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


def test_worktree_gets_its_own_namespace_and_cannot_claim_main(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git("init", "-q", "-b", "main", cwd=repo)
    _git("-c", "user.email=a@b", "-c", "user.name=t", "commit", "-q", "--allow-empty", "-m", "init", cwd=repo)
    _git("worktree", "add", "-q", str(tmp_path / "feat-x"), "-b", "feat-x", cwd=repo)

    assert isolation.current_namespace({}, cwd=repo) == "main"
    assert isolation.current_namespace({}, cwd=tmp_path / "feat-x") == "feat_x"
    with pytest.raises(IsolationError, match="own namespace"):
        isolation.current_namespace({"DATAPLAT_NS": "main"}, cwd=tmp_path / "feat-x")


def test_from_env_requires_vars_and_refuses_passwords(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="DATAPLAT_CATALOG"):
        LakeConfig.from_env({}, cwd=tmp_path)
    with pytest.raises(ConfigError, match="password"):
        LakeConfig.from_env(
            {"DATAPLAT_CATALOG": "postgres:dbname=x password=hunter2", "DATAPLAT_DATA_PATH": "/d"}, cwd=tmp_path
        )


def test_namespaced_locations_and_no_secrets_in_repr(tmp_path: Path) -> None:
    env = {
        "DATAPLAT_CATALOG": f"duckdb:{tmp_path}/meta.ducklake",
        "DATAPLAT_DATA_PATH": "s3://lake/mdundo",
        "DATAPLAT_NS": "feat_x",
        "AWS_ACCESS_KEY_ID": "AKIAEXAMPLE",
        "AWS_SECRET_ACCESS_KEY": "s3cr3t-value",
        "DATAPLAT_CODE_VERSION": "abc",
    }
    cfg = LakeConfig.from_env(env, cwd=tmp_path)
    assert cfg.attach_uri().endswith("meta.feat_x.ducklake")
    assert cfg.resolved_data_path() == "s3://lake/mdundo/_ns/feat_x/"
    assert "s3cr3t" not in repr(cfg) and "AKIA" not in repr(cfg)
    assert "s3cr3t" not in str(cfg.describe())
    pg = LakeConfig("postgres:dbname=lakecat host=/tmp user=lake", "s3://lake/x/", namespace="main")
    assert pg.metadata_schema() == "lake_main"
    assert pg.resolved_data_path() == "s3://lake/x/"


@pytest.mark.parametrize(
    "catalog",
    [
        "postgres:dbname=x password=hunter2",
        "postgres:password=hunter2 dbname=x",
        "postgres:dbname=x password = 'hunter 2'",
        "postgres:postgresql://lake:hunter2@db/lakecat",
    ],
)
def test_passwords_are_detected_anywhere(catalog: str, tmp_path: Path) -> None:
    from dataplat.config import has_password, redact

    assert has_password(catalog)
    assert "hunter" not in redact(catalog)
    with pytest.raises(ConfigError, match="password"):
        LakeConfig.from_env({"DATAPLAT_CATALOG": catalog, "DATAPLAT_DATA_PATH": "/d"}, cwd=tmp_path)


def test_direct_construction_cannot_claim_main_inside_a_worktree(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git("init", "-q", "-b", "main", cwd=repo)
    _git("-c", "user.email=a@b", "-c", "user.name=t", "commit", "-q", "--allow-empty", "-m", "init", cwd=repo)
    _git("worktree", "add", "-q", str(tmp_path / "wt"), "-b", "wt", cwd=repo)
    monkeypatch.chdir(tmp_path / "wt")
    with pytest.raises(IsolationError, match="may not use the 'main' namespace"):
        LakeConfig("duckdb:x.ducklake", "/d")
    assert LakeConfig("duckdb:x.ducklake", "/d", namespace="wt").namespace == "wt"
