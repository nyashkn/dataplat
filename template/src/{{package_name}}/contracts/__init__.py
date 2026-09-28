"""Contracts: LinkML declarations and the code generated from them.

- ``tables.yaml``: physical lake tables, generated into ``_generated/tables.py`` as constants.
- ``ontology.yaml``: business entities agents reason about, generated into ``_generated/ontology.py``
  (Pydantic) and ``_generated/ontology.schema.json`` (JSON Schema, for tool definitions).

Edit the YAML and run ``just contracts``. Never edit ``_generated/``.
"""
