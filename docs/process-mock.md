# Process lifecycle rehearsal

`ProcessMockRuntime` implements the existing start/inspect/stop contract using a
trusted detached worker and a fixed synthetic child for Mac development. Fresh
application stores can select it with `HEARTH_MOCK_RUNTIME=process_mock`; existing
stores retain their recorded choice and reject a conflicting selection. The CLI
demo remains inline-only and refuses a process store before submitting work. No
Codex process, model, credential or live source is used.

Run the behavioral rehearsal from a development checkout:

```sh
uv run pytest tests/test_process_mock.py
```

The tests use fresh temporary SQLite and actual processes, including an engine
process that exits while its detached worker continues. Success is published into
SQLite and an immutable simulated artifact through the ordinary Executor. Tests
also cover repeated/concurrent starts, a lost acknowledgement after actual spawn,
failed spawn, worker loss, cancellation before launch, cancellation after adapter
restart including a heartbeat descendant, timeout, terminal usage and corruption.
Ambiguous launch plus cancellation retains the active run and blocks new admission.

## Ownership and evidence

The adapter serializes launch claims under a canonical-root file lock. It durably
creates the run directory and request before spawning. The request pins the input
digest, mock scenario and timeout; repeating the same run/input reads that claim
and never launches again. A changed input refuses. Missing or partial evidence in
an existing run directory is unknown/corrupt, never absent or retry permission.

The worker owns a lifetime file lock and an exclusive durable `started` marker.
Even another worker invoked directly cannot repeat child launch after a crash.
Observation uses the lock and validated terminal receipt, not a stored PID. A
cancel request is durable and survives adapter/engine restart. The worker alone
signals its own unreaped child's process group and then waits for that child.

The fixture reports a bounded JSON completion record and keeps its group leader
alive until cleanup. This avoids a Mac behavior observed during development:
signalling an already-exited group's leader returned EPERM, which correctly left
execution unknown. The final fixture protocol does not ignore that permission
failure. Its owner acknowledges completion by killing the still-owned group and
observing termination before publishing the terminal receipt. The fixture record
is synthetic completion evidence; it is not a Codex event parser or a model exit.

Workers launch isolated Python from an unrelated working directory with a small
explicit environment and no inherited shell configuration. Output is bounded to
512 KiB; mock lifetime is at most 60 seconds and final wait is bounded. This
controls the cooperative fixture, not arbitrary generated tools. Test descendants
remain in the owned process group and expire independently if the worker is killed.

## Recovery and limits before real wiring

Worker death leaves unknown evidence and preserves Hearth ownership. It does not
prove that arbitrary descendants or remote model work have stopped. Detached or
escaped descendants need the actual Mac isolation boundary. No filesystem/network
confinement or provider usage guarantee is claimed. Cancellation costs remain
explicit synthetic amounts; timeout has unknown usage.

Store selection changes only on a quiet store. Admission pins adapter kind, contract version and
the exact serialized input digest; observation and launch must match those pins.
Process backups require settled runs and idle workers. They retain only durable
request/completion/cancellation/started claims, with hashes and database-reference
checks. Locks, fixture scratch data and heartbeats are excluded. Restore stays held
and cannot start or cancel a worker.

The real Codex integration still needs a trusted process-group guard around the actual CLI, bounded
Codex event parsing, verified authentication/pricing and host isolation. Keep these
requirements in [the Reader design](codex-reader.md); do not convert this fixture
into an arbitrary executable launcher or treat it as real-runtime acceptance.
