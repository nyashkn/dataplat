"""Configuration: the lake connection (from env) and project settings (from pyproject [tool.dataplat]).

Secrets never live in files. ``DATAPLAT_CATALOG`` holds a libpq string *without* a password; libpq reads
``PGPASSWORD`` from the environment, which Infisical injects at runtime (``infisical run -- ...``).
S3 credentials come from ``AWS_ACCESS_KEY_ID`` / ``AWS_SECRET_ACCESS_KEY``. Nothing here is logged.
"""

from __future__ import annotations

import os
import re
import subprocess
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from pathlib import Path

from dataplat import isolation
from dataplat.errors import ConfigError, IsolationError

_KV_PASSWORD = re.compile(r"(?i)(^|[\s:])password\s*=\s*('[^']*'|\S+)")
_URI_PASSWORD = re.compile(r"(?i)^(postgres(ql)?://[^:/@\s]+):[^@\s]*@")


def has_password(catalog: str) -> bool:
    """True if a catalog string carries a password (libpq ``password=`` in any position, or a URI's user:pw@)."""
    body = catalog.removeprefix("postgres:")
    return bool(_KV_PASSWORD.search(body) or _URI_PASSWORD.search(body))


def redact(catalog: str) -> str:
    body = catalog.removeprefix("postgres:")
    body = _KV_PASSWORD.sub(lambda m: f"{m.group(1)}password=***", body)
    body = _URI_PASSWORD.sub(lambda m: f"{m.group(1)}:***@", body)
    return ("postgres:" + body) if catalog.startswith("postgres:") else body


@dataclass(frozen=True)
class S3Config:
    endpoint: str
    region: str = "us-east-1"
    url_style: str = "path"  # RustFS/MinIO want path-style
    use_ssl: bool = False
    key_id: str = field(default="", repr=False)
    secret: str = field(default="", repr=False)


@dataclass(frozen=True)
class LakeConfig:
    """Where the lake lives. ``catalog`` is ``duckdb:<path>`` (local/tests) or ``postgres:<libpq>``."""

    catalog: str
    data_path: str
    namespace: str = isolation.MAIN
    s3: S3Config | None = None
    code_version: str = "dev"

    def __post_init__(self) -> None:
        if not (self.catalog.startswith("duckdb:") or self.catalog.startswith("postgres:")):
            raise ConfigError(f"DATAPLAT_CATALOG must start with 'duckdb:' or 'postgres:', got {self.catalog!r}")
        isolation.validate(self.namespace)
        if self.namespace == isolation.MAIN and (wt := isolation.linked_worktree_name()) is not None:
            raise IsolationError(
                f"worktree {wt!r} may not use the 'main' namespace; worktrees get their own (see dataplat.isolation)"
            )

    @property
    def is_postgres(self) -> bool:
        return self.catalog.startswith("postgres:")

    @property
    def is_s3(self) -> bool:
        return self.data_path.startswith("s3://")

    def attach_uri(self) -> str:
        if self.is_postgres:
            return f"ducklake:{self.catalog}"
        path = Path(self.catalog.removeprefix("duckdb:"))
        if self.namespace != isolation.MAIN:
            path = path.with_name(f"{path.stem}.{self.namespace}{path.suffix}")
        return f"ducklake:{path}"

    def metadata_schema(self) -> str | None:
        return f"lake_{self.namespace}" if self.is_postgres else None

    def resolved_data_path(self) -> str:
        return isolation.data_prefix(self.data_path, self.namespace)

    def for_namespace(self, ns: str) -> LakeConfig:
        return replace(self, namespace=isolation.validate(ns))

    def describe(self) -> dict[str, str]:
        """Safe-to-print summary (no credentials)."""
        return {
            "catalog": redact(self.catalog),
            "data_path": self.resolved_data_path(),
            "namespace": self.namespace,
            "metadata_schema": self.metadata_schema() or "-",
            "code_version": self.code_version,
        }

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None, cwd: Path | None = None) -> LakeConfig:
        env = os.environ if env is None else env
        try:
            catalog, data_path = env["DATAPLAT_CATALOG"], env["DATAPLAT_DATA_PATH"]
        except KeyError as missing:
            raise ConfigError(
                f"{missing.args[0]} is not set. Copy .env.example to .env for local work; "
                "production values come from Infisical."
            ) from None
        if has_password(catalog):
            raise ConfigError("DATAPLAT_CATALOG must not contain a password; libpq reads PGPASSWORD.")
        s3 = None
        if data_path.startswith("s3://"):
            s3 = S3Config(
                endpoint=env.get("DATAPLAT_S3_ENDPOINT", "localhost:9000"),
                region=env.get("DATAPLAT_S3_REGION", "us-east-1"),
                url_style=env.get("DATAPLAT_S3_URL_STYLE", "path"),
                use_ssl=env.get("DATAPLAT_S3_USE_SSL", "false").lower() == "true",
                key_id=env.get("AWS_ACCESS_KEY_ID", ""),
                secret=env.get("AWS_SECRET_ACCESS_KEY", ""),
            )
        return cls(
            catalog=catalog,
            data_path=data_path,
            namespace=isolation.current_namespace(env, cwd),
            s3=s3,
            code_version=code_version(env, cwd),
        )


def code_version(env: Mapping[str, str] | None = None, cwd: Path | None = None) -> str:
    """``DATAPLAT_CODE_VERSION`` or the short git SHA (+``-dirty``). Recorded on every commit and run."""
    env = os.environ if env is None else env
    if v := env.get("DATAPLAT_CODE_VERSION"):
        return v
    try:
        here = cwd or Path.cwd()
        sha = subprocess.run(
            ["git", "rev-parse", "--short=12", "HEAD"],
            cwd=here,
            capture_output=True,
            text=True,
            timeout=5,
            check=True,
        ).stdout.strip()
        dirty = subprocess.run(
            ["git", "status", "--porcelain", "--untracked-files=no"],
            cwd=here,
            capture_output=True,
            text=True,
            timeout=5,
            check=True,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return "dev"
    return f"{sha}-dirty" if dirty else sha


@dataclass(frozen=True)
class ProjectSettings:
    """``[tool.dataplat]`` in the project's pyproject.toml."""

    root: Path
    package: str
    forbidden_columns: tuple[str, ...] = ()
    tables_schema: Path | None = None
    tables_out: Path | None = None
    ontology_schema: Path | None = None
    ontology_out: Path | None = None
    ontology_jsonschema_out: Path | None = None
    lint_exempt: tuple[str, ...] = ("tests/*", "tests/**", "scripts/*", "migration/*")

    @classmethod
    def load(cls, start: Path | None = None) -> ProjectSettings:
        root = _find_root(start or Path.cwd())
        data = tomllib.loads((root / "pyproject.toml").read_text())
        cfg = data.get("tool", {}).get("dataplat")
        if cfg is None:
            raise ConfigError(f"{root / 'pyproject.toml'} has no [tool.dataplat] table")
        contracts = cfg.get("contracts", {})

        def p(key: str) -> Path | None:
            return root / contracts[key] if key in contracts else None

        return cls(
            root=root,
            package=cfg["package"],
            forbidden_columns=tuple(cfg.get("forbidden_columns", ())),
            tables_schema=p("tables"),
            tables_out=p("tables_out"),
            ontology_schema=p("ontology"),
            ontology_out=p("ontology_out"),
            ontology_jsonschema_out=p("ontology_jsonschema_out"),
            lint_exempt=tuple(cfg.get("lint", {}).get("exempt", cls.lint_exempt)),
        )


def _find_root(start: Path) -> Path:
    for d in (start, *start.parents):
        if (d / "pyproject.toml").is_file():
            return d
    raise ConfigError(f"no pyproject.toml found above {start}")
