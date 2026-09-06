# Pinned Reader container rehearsal

`ContainerRehearsal` connects Hearth's actual staged context to the previously
[probed Mac container boundary](mac-isolation.md). It is an internal, offline
integration rehearsal with a fixed synthetic executable. It is not a Runtime
adapter, application runtime selector or operational launch-authority check.
Use only a dedicated synthetic database and worker root, with no app executor.

## Ownership and uncertain dispatch

A private run folder contains an immutable claim before Docker create is called.
The claim pins run ID, canonical staged directory, exact input digest, image digest,
fixture digest, scenario, non-root host UID/GID, contract version and random container
name. Its SHA-256 is the container ownership label. The daemon's returned identity
is inspected and then persisted before start. Once present, that exact ID is required
for observation, stop and removal; a running container cannot be adopted after its
identity evidence goes missing.

Repeated start calls validate the same staged bytes and claim, then only inspect.
They never repeat create or start, even if dispatch never reached the daemon, a
reply was lost, or the container has since been removed. Created-but-unstarted,
missing or unreachable containers remain unknown. A partial/corrupt claim refuses;
unknown is not retry permission. Cancellation and removal require the same name,
label and pinned ID. Removal requires a daemon-observed created/exited state and
retains the claim, so it cannot authorize a later relaunch.

The dedicated root and its ancestors are trusted operator storage and never mounted
into the container. Claimed input paths must belong to that root; linked claim
folders refuse. File evidence is bounded and must be regular, singly linked files.
Do not copy a live claim to another root and treat that as execution ownership.
This rehearsal is not included in operational backup/restore or admission accounting.

## Actual input and output boundary

`stage_run` supplies the admitted SQLite declaration/task/memory and synthetic notes.
The container mounts only that run directory, read-only. It runs as the non-root
host UID/GID so the actual 0400 file and 0700 directory remain unchanged and readable
on the verified Docker Desktop host. No broader project/root, host credentials,
engine socket or other residents are mounted.

The fixed Python executable checks the input SHA-256, synthetic flag, non-root UID,
active seccomp and no-new-privileges. It emits synthetic Codex-shaped JSONL or holds
for cancellation. Before start, inspection checks the configured image, executable,
user, read-only mount/root, network, namespace, capabilities and security options.
Network is disabled; scratch and resources retain the small offline probe limits.
No model runs, token counts are not invented, and no dollars are calculated.

The existing bounded `CodexEvents` parser interprets terminal logs only after daemon
state says exited with PID 0. The stream's thread ID must match the admitted run.
An observed local exit is separate from a completed transcript; cancellation can
have an incomplete transcript. A removed container's logs become unavailable and
inspection returns unknown. This is deliberately not a durable terminal artifact
or accounting receipt yet.

## Reproduce and evidence

Prepare the same pinned image as the [Mac probe](mac-isolation.md), then run:

```sh
uv run --frozen python scripts/rehearse-reader-container.py --report /tmp/hearth-reader-rehearsal.json
```

The script uses fresh SQLite and synthetic memory, injects a lost start reply,
reopens the worker, observes it from a fresh Python process, checks success and
cancellation, removes only the owned containers and verifies removed claims cannot
relaunch. Failed cleanup retains the original synthetic root and prints its path;
recover the same claim before cleanup. It never pulls images or changes Docker.
[Recorded Mac evidence](evidence/reader-container-2026-09-06.json) applies to the
module hash and image digest in that report. Default CI uses fault-injected Docker
responses and actual temporary SQLite; the host rehearsal is opt-in.

Remaining: trusted asynchronous worker integration, admission/launch authority,
quiescent backup/restore and durable terminal receipts, actual Codex image/version,
final-file ownership and real provenance/accounting. The model transport must keep
credentials outside generated tools and constrain allowed requests. A real test
still requires explicit selection. Mac development and $10/day remain confirmed;
no migration or real execution has been introduced.
