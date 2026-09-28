"""A tiny app for the telemetry export test: one workflow succeeds, one fails in the foreground, the same
one fails again in the background (DBOS logs that failure with its stack trace), then telemetry flushes.

python telemetry_app.py <system_db>
"""

import sys

from dbos import DBOS

from dataplat.observe import telemetry
from dataplat.runtime import dbos_config, launch

MSISDN = "254712345678"


@DBOS.step()
def charge(msisdn: str) -> str:
    raise ValueError(f"payout failed for {msisdn} (national id 12345678, contact jane@example.com)")


@DBOS.workflow()
def payout(msisdn: str) -> str:
    return charge(msisdn)


@DBOS.workflow()
def healthy() -> str:
    return "ok"


def main() -> None:
    cfg = dbos_config(
        "telemetry_probe", system_database_url=sys.argv[1], code_version="v1", namespace="probe", role="cli"
    )
    launch(cfg, role="cli")
    healthy()
    try:
        payout(MSISDN)
    except ValueError:
        pass
    handle = DBOS.start_workflow(payout, MSISDN)
    try:
        handle.get_result()
    except ValueError:
        pass
    telemetry.flush()
    DBOS.destroy()


if __name__ == "__main__":
    main()
