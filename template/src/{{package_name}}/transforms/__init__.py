"""Transforms: pure Hamilton functions, polars in and polars out.

No I/O, no environment, no orchestration (``just imports``). Types are parsed explicitly. Output nodes
carry a pandera validator bound to their contract. Business rules are ``@check`` expressions with
live-fire tests.
"""
