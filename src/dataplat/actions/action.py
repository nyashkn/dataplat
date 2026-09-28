"""Actions: side effects run as durable, gated, human-approved, auditable workflows.

An action is the write side for agents: sending an outreach email, creating a CRM task, pausing a
campaign. Each one is a DBOS workflow that

1. evaluates its preconditions as a recorded step (for example "the artist exists" or "not contacted
   in the last 30 days"); any FAIL or INCONCLUSIVE refuses the action,
2. waits for a human decision (``approve(workflow_id, by=...)``) unless ``approval=False``, and
   survives restarts while waiting,
3. re-checks the preconditions after approval, because the world may have changed during a wait of
   up to a week,
4. runs the effect as a recorded step. Once the step completes it never runs again. If the process
   dies *during* the step, DBOS re-runs it on recovery, so effects must be idempotent: pass
   ``DBOS.workflow_id`` to the external API as the idempotency key,
5. leaves an audit trail in the DBOS system database (``audit()``).

    @action("outreach.propose", preconditions=[artist_known, not_recently_contacted])
    def propose_outreach(artist_id: str, channel: str) -> dict: ...

Precondition callables take the same arguments as the effect and return ``Evidence``.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any, Literal

from dbos import DBOS, Queue

from dataplat import isolation
from dataplat.checks.verdict import Evidence, Verdict

ACTION_QUEUE = "actions"
APPROVAL_TOPIC = "dataplat.approval"
STATUS_EVENT = "dataplat.status"
DEFAULT_APPROVAL_TIMEOUT_S = 7 * 24 * 3600

PreconditionFn = Callable[..., Evidence]


def action(
    name: str,
    *,
    preconditions: Sequence[PreconditionFn] = (),
    approval: bool = True,
    approval_timeout_s: int = DEFAULT_APPROVAL_TIMEOUT_S,
) -> Callable[[Callable[..., Any]], Callable[..., dict[str, Any]]]:
    def deco(effect: Callable[..., Any]) -> Callable[..., dict[str, Any]]:
        effect_step = DBOS.step(name=f"{name}.effect")(effect)

        def check(*args: Any, **kwargs: Any) -> list[dict[str, Any]]:
            return [p(*args, **kwargs).to_dict() for p in preconditions]

        check_step = DBOS.step(name=f"{name}.preconditions")(check)

        def workflow(*args: Any, **kwargs: Any) -> dict[str, Any]:
            evidence = check_step(*args, **kwargs)
            blocking = [e for e in evidence if e["verdict"] != Verdict.PASS]
            if blocking:
                DBOS.set_event(STATUS_EVENT, "refused")
                return {"action": name, "status": "refused", "evidence": evidence}
            approver = None
            if approval:
                DBOS.set_event(STATUS_EVENT, "pending_approval")
                msg = DBOS.recv(APPROVAL_TOPIC, timeout_seconds=approval_timeout_s)
                if msg is None:
                    DBOS.set_event(STATUS_EVENT, "expired")
                    return {"action": name, "status": "expired", "evidence": evidence}
                approver = msg.get("by")
                if msg.get("decision") != "approved":
                    DBOS.set_event(STATUS_EVENT, "rejected")
                    return {
                        "action": name,
                        "status": "rejected",
                        "approver": approver,
                        "note": msg.get("note", ""),
                        "evidence": evidence,
                    }
            if approval:
                recheck = check_step(*args, **kwargs)
                if any(e["verdict"] != Verdict.PASS for e in recheck):
                    DBOS.set_event(STATUS_EVENT, "refused")
                    return {
                        "action": name,
                        "status": "refused_after_approval",
                        "approver": approver,
                        "evidence": recheck,
                    }
            value = effect_step(*args, **kwargs)
            DBOS.set_event(STATUS_EVENT, "done")
            return {"action": name, "status": "done", "value": value, "approver": approver, "evidence": evidence}

        workflow.__name__ = effect.__name__
        workflow.__qualname__ = effect.__qualname__
        workflow.__doc__ = effect.__doc__
        workflow.__module__ = effect.__module__
        return DBOS.workflow(name=name)(workflow)

    return deco


def action_queue(namespace: str | None = None) -> Queue:
    """The namespaced queue actions run on. Every process that launches DBOS registers it (the worker
    executes from it), so an agent can propose an action from a short-lived process and exit."""
    name = isolation.queue_name(ACTION_QUEUE, namespace or isolation.current_namespace())
    return DBOS.retrieve_queue(name) or DBOS.register_queue(name)


def propose(action_fn: Callable[..., Any], *args: Any, **kwargs: Any) -> str:
    """Enqueue an action for the worker; returns the workflow id a human approves with ``approve``."""
    return action_queue().enqueue(action_fn, *args, **kwargs).get_workflow_id()


def approve(
    workflow_id: str, *, by: str, decision: Literal["approved", "rejected"] = "approved", note: str = ""
) -> None:
    DBOS.send(workflow_id, {"decision": decision, "by": by, "note": note}, topic=APPROVAL_TOPIC)


def status(workflow_id: str, timeout_s: float = 0.0) -> str | None:
    return DBOS.get_event(workflow_id, STATUS_EVENT, timeout_seconds=timeout_s)


def audit(name: str | None = None) -> list[dict[str, Any]]:
    """Every run of an action: who approved it, what it returned, which code version ran it."""
    return [
        {
            "workflow_id": w.workflow_id,
            "action": w.name,
            "status": w.status,
            "app_version": w.app_version,
            "created_at": w.created_at,
            "output": w.output,
        }
        for w in DBOS.list_workflows(name=name)
    ]
