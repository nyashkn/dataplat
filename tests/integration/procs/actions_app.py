"""A tiny app for the multi-process action test.

python actions_app.py worker <system_db> <effects_log> <seconds>   executes queued actions
python actions_app.py cli    <system_db> <effects_log>             proposes one, approves it, prints result
"""

import os
import sys
import time

from dbos import DBOS

from dataplat.actions import action, action_queue, approve, propose, status
from dataplat.checks import Evidence, Verdict
from dataplat.runtime import dbos_config, launch

LOG = sys.argv[3] if len(sys.argv) > 3 else "/dev/null"


def always_ok(target: str) -> Evidence:
    return Evidence("ok", Verdict.PASS)


@action("procs.notify", preconditions=[always_ok], approval_timeout_s=60)
def notify(target: str) -> str:
    with open(LOG, "a") as f:
        f.write(f"effect:{target}\n")
    return f"notified {target}"


def main() -> None:
    role, system_db = sys.argv[1], sys.argv[2]
    os.environ["DATAPLAT_NS"] = "test"
    cfg = dbos_config("procs", system_database_url=system_db, code_version="v1", namespace="test", role=role)  # type: ignore[arg-type]
    launch(cfg, role=role)  # type: ignore[arg-type]
    action_queue("test")
    if role == "worker":
        with open(LOG + ".ready", "w") as f:  # launched and migrated: clients may connect now
            f.write("ready")
        time.sleep(float(sys.argv[4]))
        return
    wid = propose(notify, "artist-1")
    for _ in range(200):
        if status(wid) == "pending_approval":
            break
        time.sleep(0.1)
    print("STATUS", status(wid), flush=True)
    approve(wid, by="owner")
    print("RESULT", DBOS.retrieve_workflow(wid).get_result()["status"], flush=True)


if __name__ == "__main__":
    main()
