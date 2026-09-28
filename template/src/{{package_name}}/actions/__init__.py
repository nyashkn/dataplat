"""Actions: side effects as ``dataplat.actions`` workflows.

Each action runs its preconditions (evidence), waits for human approval, runs its effect exactly once
and leaves an audit trail. Actions do not write the lake; if an action needs data changed, a pipeline
does it.
"""
