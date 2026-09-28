"""dataplat: the shared platform for agent-built data projects.

Import from these entry points (each package's ``__init__`` documents its surface):

    dataplat.contracts      TableContract, Column (generated from LinkML)
    dataplat.lakecore       connect / Lake / Reader (read side, anywhere)
    dataplat.lakecore.write Writer (write side, <pkg>.pipelines only)
    dataplat.lakecore.publish PublishTarget, publish_bytes, publish_documents (fixed-key documents,
                             write-once; <pkg>.pipelines only)
    dataplat.checks         Verdict, Evidence, check, livefire, dirty, witness, profile, Rate
    dataplat.runtime        run_dag (Hamilton), dbos_config / workflow_id (DBOS)
    dataplat.trust          census
    dataplat.gate           answer, Precondition, Answer, Refusal
    dataplat.actions        action, approve, audit
    dataplat.graph          GraphSpec, publish, open_current (extra: graph)
    dataplat.isolation      namespaces for parallel worktrees
"""

__version__ = "0.2.0"
