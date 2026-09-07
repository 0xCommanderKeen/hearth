# Contained synthetic process execution

The normal process worker can run the fixed Python summary fixture inside the
[verified Mac container configuration](mac-isolation.md). No Codex model, provider
credentials, live notes or external effects are involved.

Use a fresh data directory with:

```sh
HEARTH_MOCK_RUNTIME=process_mock HEARTH_PROCESS_BOUNDARY=container \
  uv run uvicorn hearth.app:from_env --factory --host 127.0.0.1 --port 8766
```

Keep the ordinary data-directory and operator-token configuration from the README.
The selected Mac needs its existing local Docker Desktop socket and the pinned image
from the [container rehearsal](container-rehearsal.md). No implicit image pull occurs.
Only `success` and `hold` fixture scenarios are supported. The container boundary is
immutable in the database; subsequent starts infer it when the environment selector is
unset. A conflicting selector refuses. Existing prototype configuration/request files lacking
the current required fields are refused without conversion; start with fresh data.

## Execution and recovery

Admission and the executor retain their existing process runtime contract. The durable
request pins the container choice, input digest, scenario, epoch, owner token and
a finite wall-clock deadline before spawning. Reconciliation enforces that same
deadline after worker loss; reopening never resets it. The
owner and epoch stay in private host evidence outside the mounted input. The detached
worker inherits a minimal environment and mounts only the staged synthetic context.
It obtains the database dispatch guard immediately before starting the inspected
container. Repeated requests or workers never repeat a claimed start.

The worker polls observed container state and the durable cancellation marker. After
worker loss, the normal adapter reconciles the exact owned container under the worker
lock. Cancellation can stop it without relying on a stored host PID. Terminal receipt
validation is shared with backup verification. Successful output is labelled as a
simulation; the fixed fixture costs 2,000 synthetic microdollars, confirmed cancellation
costs 1,000, and cancellation before worker dispatch costs zero. Other terminal failures
retain unknown usage. These values do not describe model usage or provider charges.

Receipt publication precedes cleanup. Conflicts or unproven termination found
during cleanup prevent result publication. Cached process results are also checked
against current container evidence before the executor accepts them. The worker attempts removal of the exact exited
container and retains its immutable claim/receipt. If removal cannot be verified, the
receipt still proves the original execution terminated; the stopped container may need
later cleanup through the same owned claim. Missing, corrupt or conflicting execution
evidence stays unknown. Cancellation or a changed declaration during preparation can
leave a created-but-unstarted claim unknown; this does not authorize retry.

## Backup and acceptance evidence

Capture refuses active runs, live workers and unsettled container claims. It freezes
process/container launch and receipt publication, copies pinned input and terminal
provenance, then validates request/run/receipt/artifact agreement. Consistently rehashed
but contradictory output is rejected. Verification never contacts Docker. Held restore
preserves the original claim paths as provenance, infers the stored process choice and
cannot execute. Repeated held backup/restore preserves the original execution epoch.

Run the actual Mac workflow explicitly:

```sh
uv run python scripts/rehearse-container-worker.py --report /tmp/hearth-worker-report.json
```

It runs fresh synthetic success, cancellation and trusted-worker-loss cases through the actual detached
worker and normal executor, reopens the app, verifies output/accounting and held backup
recovery, and checks owned container removal. Failures retain the original temporary
state and exact claims for diagnosis. [Recorded evidence](evidence/container-worker-2026-09-06.json)
pins the worker, authority, recovery and parser source hashes, plus the rehearsal script. Offline CI checks use real SQLite and injected Docker
responses. Neither those checks nor this synthetic host rehearsal prove real Codex
summary quality, model access, provider usage or the daily-observation gate.
