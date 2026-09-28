"""Pipelines: DBOS workflows. The only layer that writes the lake (``dataplat.lakecore.write``).

One step = compute + one ``Writer`` call = one DuckLake transaction. Workflows run under deterministic
ids (``dataplat.runtime.workflow_id``), so re-running is safe.
"""
