"""``dataplat`` command: contract generation and staleness check, the architecture scanner, namespaces.

dataplat contracts gen      regenerate contracts/_generated/* from LinkML
dataplat contracts check    exit 1 if any generated file is stale (pre-commit + CI)
dataplat lint [PATHS]       DPA001-DPA008 architecture rules (default: src)
dataplat ns [--init]        show (and create) the lake namespace for this checkout
"""

from __future__ import annotations

import argparse
import difflib
import os
import sys
from pathlib import Path

from dataplat.config import LakeConfig, ProjectSettings


def _contracts(action: str) -> int:
    from dataplat.contracts.generate import Output, render_ontology_jsonschema, render_ontology_pydantic, render_tables
    from dataplat.contracts.linkml import load_tables

    s = ProjectSettings.load()
    # Generators embed the schema path they were given. Run from the project root with relative paths,
    # so output is identical on every machine (otherwise `contracts check` fails in CI).
    os.chdir(s.root)
    outputs: list[Output] = []
    if s.tables_schema and s.tables_out:
        tables = load_tables(s.tables_schema, forbidden_columns=s.forbidden_columns)
        src = s.tables_schema.relative_to(s.root).as_posix()
        outputs.append(Output(s.tables_out, render_tables(tables, src)))
    if s.ontology_schema and s.ontology_out:
        rel = s.ontology_schema.relative_to(s.root)
        outputs.append(Output(s.ontology_out, render_ontology_pydantic(rel)))
    if s.ontology_schema and s.ontology_jsonschema_out:
        rel = s.ontology_schema.relative_to(s.root)
        outputs.append(Output(s.ontology_jsonschema_out, render_ontology_jsonschema(rel)))
    if not outputs:
        print("no contracts configured under [tool.dataplat.contracts]", file=sys.stderr)
        return 1
    if action == "gen":
        for o in outputs:
            o.write()
            print(f"wrote {o.path.relative_to(s.root)}")
        return 0
    stale = [o for o in outputs if o.stale()]
    for o in stale:
        rel = o.path.relative_to(s.root)
        print(f"STALE: {rel} does not match its LinkML source. Run `just contracts` and commit the result.")
        old = o.path.read_text().splitlines() if o.path.exists() else []
        diff = list(
            difflib.unified_diff(old, o.text.splitlines(), f"{rel} (committed)", f"{rel} (generated)", lineterm="", n=1)
        )
        print("\n".join(diff[:40]))
    return 1 if stale else 0


def _lint(paths: list[str]) -> int:
    from dataplat.lint.scan import scan_paths

    s = ProjectSettings.load()
    targets = [Path(p) for p in paths] or [s.root / "src"]
    findings = scan_paths(targets, root=s.root, package=s.package, exempt=s.lint_exempt)
    for f in findings:
        print(f)
    if findings:
        print(
            f"\n{len(findings)} architecture finding(s). Rules and exemptions: docs/CONVENTIONS.md (DPA001-DPA008).",
            file=sys.stderr,
        )
    return 1 if findings else 0


def _ns(init: bool) -> int:
    cfg = LakeConfig.from_env()
    for k, v in cfg.describe().items():
        print(f"{k:16} {v}")
    if init:
        from dataplat.lakecore import Lake
        from dataplat.lakecore.write import Writer

        with Lake(cfg) as lake:
            Writer(lake)  # creates the catalog, metadata schema and run log if missing
            print(f"namespace ready at snapshot {lake.current_snapshot()}")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="dataplat", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = p.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("contracts", help="generate or check generated contracts")
    c.add_argument("action", choices=["gen", "check"])
    lint = sub.add_parser("lint", help="architecture rules DPA001-DPA008")
    lint.add_argument("paths", nargs="*")
    ns = sub.add_parser("ns", help="show the lake namespace for this checkout")
    ns.add_argument("--init", action="store_true")
    a = p.parse_args(argv)
    if a.cmd == "contracts":
        return _contracts(a.action)
    if a.cmd == "lint":
        return _lint(a.paths)
    return _ns(a.init)


if __name__ == "__main__":
    raise SystemExit(main())
