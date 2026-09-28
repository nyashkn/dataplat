"""``dataplat lint``: the architecture rules ruff and import-linter can't express.

Rules (each exists because of a real incident; see docs/traps.md):

DPA001  file-writing call (``.write_parquet``, ``.to_parquet``, ``.sink_parquet``, ...) outside the
        writer. Seven hand-rolled Parquet writers drifted apart in mdundo-pipeline.
DPA002  SQL that writes files (``COPY ... TO '...'``, ``EXPORT DATABASE``). DuckDB makes this one
        line, which is why agents reach for it.
DPA003  listing storage (``os.listdir``, ``glob``, ``.glob``/``.rglob``/``.iterdir``, ``fs.ls``,
        SQL ``glob(...)`` or wildcard ``read_parquet('...*')``). RustFS truncates listings at about
        501 entries; the catalog knows every file.
DPA004  ``dataplat.lakecore.write`` imported outside ``<pkg>.pipelines``. One layer writes.
DPA005  ``dataplat.lakecore`` imported in ``<pkg>.transforms``, ``.sources`` or ``.contracts``.
        Transforms are pure; sources return frames and never touch the lake.
DPA006  Hamilton outputs requested by name string (``execute(["x"])``, ``run_dag(final=["x"])``).
        Pass the function object instead.
DPA007  SQL that changes lake data or schema (INSERT/UPDATE/DELETE/MERGE/CREATE/ALTER/DROP/TRUNCATE)
        outside ``<pkg>.pipelines``. Mutations go through the Writer, in a pipeline.
DPA008  I/O or environment access inside ``<pkg>.transforms`` (``open``, ``os.environ``, ``os.getenv``,
        ``pl.read_*``/``pl.scan_*``, ``.read_text``/``.write_text``). Transforms are pure.

Exempt one line with a reason: ``# dataplat: allow[DPA003] -- why this is fine``. A missing reason is
itself an error (DPA000).
"""

from __future__ import annotations

import ast
import fnmatch
import os
import re
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path

WRITE_METHODS = {
    "write_parquet",
    "sink_parquet",
    "to_parquet",
    "write_delta",
    "write_ipc",
    "sink_ipc",
    "write_iceberg",
    "to_feather",
    "write_database",
    "to_sql",
}
LIST_METHODS = {"glob", "rglob", "iterdir", "listdir", "scandir", "ls", "walk"}
FS_RECEIVER_FIND = ("fs", "s3")  # fsspec-style receivers whose .find() lists storage
FILE_READERS = {
    "read_parquet",
    "scan_parquet",
    "parquet_scan",
    "read_csv",
    "read_csv_auto",
    "read_json",
    "read_json_auto",
}
IMPURE_FILE_METHODS = {"read_text", "write_text", "read_bytes", "write_bytes"}
LIST_FUNCS = {("os", "listdir"), ("os", "scandir"), ("os", "walk"), ("glob", "glob"), ("glob", "iglob")}
PURE_LAYERS = ("transforms", "sources", "contracts")
HAMILTON_EXEC = {"execute", "materialize", "raw_execute", "run_dag"}

_COPY_TO = re.compile(r"\bCOPY\b[\s\S]*?\bTO\s*(['\"]|\{)", re.IGNORECASE)
_EXPORT = re.compile(r"\bEXPORT\s+DATABASE\b", re.IGNORECASE)
_SQL_GLOB = re.compile(r"\bglob\s*\(", re.IGNORECASE)
_WILDCARD_READ = re.compile(
    r"\b(read_parquet|parquet_scan|read_csv(_auto)?|read_json(_auto)?)\s*\(\s*\[?\s*['\"][^'\"]*\*", re.IGNORECASE
)
_DML = re.compile(
    r"\b(INSERT\s+INTO|DELETE\s+FROM|UPDATE\s+[\w.\"]+\s+SET|MERGE\s+INTO|CREATE\s+(OR\s+REPLACE\s+)?TABLE"
    r"|ALTER\s+TABLE|DROP\s+TABLE|TRUNCATE)\b",
    re.IGNORECASE,
)
_ALLOW = re.compile(r"#\s*dataplat:\s*allow\[([A-Z0-9, ]+)\](?:\s*--\s*(.*))?")


@dataclass(frozen=True)
class Finding:
    path: str
    line: int
    col: int
    code: str
    message: str
    end: int = 0  # last line of the offending node (for allow comments on wrapped calls)

    def __str__(self) -> str:
        return f"{self.path}:{self.line}:{self.col}: {self.code} {self.message}"


def module_name(path: Path, package: str) -> str:
    parts = path.with_suffix("").parts
    if package in parts:
        i = len(parts) - 1 - list(reversed(parts)).index(package)
        mod = parts[i:]
        return ".".join(mod[:-1] if mod[-1] == "__init__" else mod)
    return path.stem


class _Visitor(ast.NodeVisitor):
    def __init__(self, path: str, module: str, package: str) -> None:
        self.path, self.module, self.package = path, module, package
        self.findings: list[Finding] = []
        self._aliases: dict[str, str] = {}

    # -- helpers
    def _add(self, node: ast.AST, code: str, msg: str) -> None:
        line = getattr(node, "lineno", 0)
        self.findings.append(
            Finding(
                self.path, line, getattr(node, "col_offset", 0), code, msg, getattr(node, "end_lineno", line) or line
            )
        )

    def _layer(self) -> str | None:
        parts = self.module.split(".")
        return parts[1] if len(parts) > 1 and parts[0] == self.package else None

    def _check_import(self, node: ast.AST, target: str) -> None:
        layer = self._layer()
        if target == "dataplat.lakecore.write" or target.startswith("dataplat.lakecore.write."):
            if layer != "pipelines":
                self._add(node, "DPA004", "only <pkg>.pipelines may import dataplat.lakecore.write (single writer)")
        if (target == "dataplat.lakecore" or target.startswith("dataplat.lakecore.")) and layer in PURE_LAYERS:
            self._add(node, "DPA005", f"{layer} must not touch the lake; return frames and let a pipeline commit them")

    # -- imports
    def visit_Import(self, node: ast.Import) -> None:
        for a in node.names:
            self._aliases[a.asname or a.name.split(".")[0]] = a.name if a.asname else a.name.split(".")[0]
            self._check_import(node, a.name)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if node.module and node.level == 0:
            for a in node.names:
                full = f"{node.module}.{a.name}"
                self._aliases[a.asname or a.name] = full
                self._check_import(node, full)
                self._check_import(node, node.module)

    # -- calls
    def visit_Call(self, node: ast.Call) -> None:
        f = node.func
        if self._layer() == "transforms":
            self._check_impure_call(node)
        if isinstance(f, ast.Attribute) and f.attr in FILE_READERS and self._wildcard_arg(node):
            self._add(node, "DPA003", f".{f.attr}() with a wildcard lists storage; read lake tables instead")
        if isinstance(f, ast.Attribute) and f.attr == "find" and isinstance(f.value, ast.Name):
            if f.value.id.lower().endswith(FS_RECEIVER_FIND):
                self._add(node, "DPA003", f"{f.value.id}.find() lists storage; ask the catalog (Lake.files)")
        if isinstance(f, ast.Attribute):
            if f.attr in WRITE_METHODS:
                self._add(node, "DPA001", f".{f.attr}() writes files; commit through dataplat.lakecore.write.Writer")
            if f.attr in LIST_METHODS:
                self._add(node, "DPA003", f".{f.attr}() lists storage; ask the catalog (Lake.files) or use a manifest")
            if isinstance(f.value, ast.Name):
                base = self._aliases.get(f.value.id, f.value.id)
                if (base, f.attr) in LIST_FUNCS and f.attr not in LIST_METHODS:
                    self._add(node, "DPA003", f"{base}.{f.attr}() lists storage; ask the catalog (Lake.files)")
            if f.attr in HAMILTON_EXEC:
                self._check_hamilton_args(node)
        elif isinstance(f, ast.Name):
            target = self._aliases.get(f.id, f.id)
            if target in ("os.listdir", "os.scandir", "os.walk", "glob.glob", "glob.iglob"):
                self._add(node, "DPA003", f"{target}() lists storage; ask the catalog (Lake.files)")
            if f.id in HAMILTON_EXEC or target.endswith(".run_dag"):
                self._check_hamilton_args(node)
        self.generic_visit(node)

    @staticmethod
    def _wildcard_arg(node: ast.Call) -> bool:
        vals: list[ast.expr] = list(node.args) + [k.value for k in node.keywords]
        for v in vals:
            items = v.elts if isinstance(v, (ast.List, ast.Tuple)) else [v]
            for it in items:
                if isinstance(it, ast.Constant) and isinstance(it.value, str) and any(ch in it.value for ch in "*?["):
                    return True
                if isinstance(it, ast.JoinedStr) and any(
                    isinstance(p, ast.Constant) and isinstance(p.value, str) and "*" in p.value for p in it.values
                ):
                    return True
        return False

    def _check_impure_call(self, node: ast.Call) -> None:
        f = node.func
        msg = "transforms are pure: no files, no environment. Read in sources/, pass frames in."
        if isinstance(f, ast.Name) and f.id == "open":
            self._add(node, "DPA008", f"open(): {msg}")
        elif isinstance(f, ast.Attribute):
            base = self._aliases.get(f.value.id, f.value.id) if isinstance(f.value, ast.Name) else ""
            if base == "os" and f.attr in ("getenv", "putenv"):
                self._add(node, "DPA008", f"os.{f.attr}(): {msg}")
            elif base == "polars" and (f.attr.startswith("read_") or f.attr.startswith("scan_")):
                self._add(node, "DPA008", f"pl.{f.attr}(): {msg}")
            elif f.attr in IMPURE_FILE_METHODS:
                self._add(node, "DPA008", f".{f.attr}(): {msg}")

    def visit_Attribute(self, node: ast.Attribute) -> None:
        if self._layer() == "transforms" and node.attr == "environ" and isinstance(node.value, ast.Name):
            if self._aliases.get(node.value.id, node.value.id) == "os":
                self._add(node, "DPA008", "os.environ: transforms are pure; configuration comes in as inputs")
        self.generic_visit(node)

    def _check_hamilton_args(self, node: ast.Call) -> None:
        cands: list[ast.expr] = [node.args[0]] if node.args and isinstance(node.args[0], (ast.List, ast.Tuple)) else []
        cands += [k.value for k in node.keywords if k.arg in ("final", "final_vars")]
        for c in cands:
            if isinstance(c, (ast.List, ast.Tuple)) and any(
                isinstance(e, ast.Constant) and isinstance(e.value, str) for e in c.elts
            ):
                self._add(node, "DPA006", "request Hamilton outputs by function object, not name string")

    # -- strings (SQL)
    def visit_Expr(self, node: ast.Expr) -> None:
        if isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
            return  # docstring / bare string statement
        self.generic_visit(node)

    def visit_JoinedStr(self, node: ast.JoinedStr) -> None:
        text = "".join(
            v.value if isinstance(v, ast.Constant) and isinstance(v.value, str) else "{}" for v in node.values
        )
        self._scan_sql(node, text)

    def visit_Constant(self, node: ast.Constant) -> None:
        if isinstance(node.value, str):
            self._scan_sql(node, node.value)

    def _scan_sql(self, node: ast.AST, text: str) -> None:
        if self._layer() not in (None, "pipelines") and _DML.search(text):
            self._add(node, "DPA007", "SQL that changes lake data or schema belongs in pipelines, through the Writer")
        if _COPY_TO.search(text) or _EXPORT.search(text):
            self._add(node, "DPA002", "SQL that writes files; commit through dataplat.lakecore.write.Writer")
        if _SQL_GLOB.search(text) or _WILDCARD_READ.search(text):
            self._add(node, "DPA003", "SQL that lists storage (glob / wildcard read); read lake tables instead")


def scan_source(source: str, path: str, package: str) -> list[Finding]:
    tree = ast.parse(source, filename=path)
    v = _Visitor(path, module_name(Path(path), package), package)
    v.visit(tree)
    lines = source.splitlines()
    stmts = [(n.lineno, n.end_lineno or n.lineno) for n in ast.walk(tree) if isinstance(n, ast.stmt)]
    kept: list[Finding] = []
    for f in v.findings:
        # An allow comment may sit on any line of the enclosing statement (ruff format moves trailing
        # comments to the last line of a wrapped call).
        enclosing = [(a, b) for a, b in stmts if a <= f.line <= b]
        lo, hi = min(enclosing, key=lambda s: s[1] - s[0]) if enclosing else (f.line, max(f.end, f.line))
        allows = [m for m in (_ALLOW.search(line) for line in lines[max(lo, 1) - 1 : hi]) if m]
        match = next((m for m in allows if f.code in {c.strip() for c in m.group(1).split(",")}), None)
        if match is None:
            kept.append(f)
        elif len((match.group(2) or "").strip()) < 3:
            kept.append(Finding(f.path, f.line, f.col, "DPA000", f"allow[{f.code}] needs a reason after '--'", f.end))
    return kept


def iter_py(paths: Iterable[Path], root: Path, exempt: Iterable[str]) -> Iterator[Path]:
    pats = list(exempt)
    for p in paths:
        files = (
            [p]
            if p.is_file()
            else sorted(
                Path(d) / f
                for d, _, fs in os.walk(p)
                for f in fs
                if f.endswith(".py")  # dataplat: allow[DPA003] -- walking local source files, not storage
            )
        )
        for f in files:
            try:
                rel = f.resolve().relative_to(root.resolve()).as_posix()
            except ValueError:
                rel = f.as_posix()
            if "/_generated/" in f"/{rel}" or any(fnmatch.fnmatch(rel, pat) for pat in pats):
                continue
            yield f


def scan_paths(paths: Iterable[Path], *, root: Path, package: str, exempt: Iterable[str] = ()) -> list[Finding]:
    out: list[Finding] = []
    for f in iter_py(paths, root, exempt):
        try:
            rel = f.resolve().relative_to(root.resolve()).as_posix()
        except ValueError:
            rel = f.as_posix()
        out.extend(scan_source(f.read_text(), rel, package))
    return out
