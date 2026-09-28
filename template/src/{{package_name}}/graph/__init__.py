"""Derived graph specs: node and relation SQL over the lake, plus canary questions for parity.

The graph is rebuilt from the lake (``pipelines``) and published only when every canary agrees
between SQL and Cypher. It is never a source of truth.
"""
