"""Architecture scanner for rules ruff and import-linter can't express (DPA001-DPA008)."""

from dataplat.lint.scan import Finding, scan_paths, scan_source

__all__ = ["Finding", "scan_paths", "scan_source"]
