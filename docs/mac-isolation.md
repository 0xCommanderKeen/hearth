# Offline Mac container boundary

Checked 2026-09-06 on the selected development Mac: macOS 26.5.2 arm64, local
Docker Desktop Linux VM, Docker client/server 27.3.1. This is an actual host probe
with synthetic files and fake processes. No Codex, credentials, live notes or model
endpoints participate. It does not enable a real runtime.

## Reproduce

Use the explicit local Docker Desktop socket at `~/.docker/run/docker.sock`.
The probe refuses a different platform, missing image, existing report or Python
optimization. It never starts Docker, changes daemon settings or pulls implicitly.
Prepare the pinned official image once, then run:

```sh
docker --host "unix://$HOME/.docker/run/docker.sock" pull python@sha256:cad9a2c871761c413caa6fdd6441c783451e740a48aaeba60ae62a8b53525ef6
uv run --frozen python scripts/probe-mac-container.py --report /tmp/hearth-mac-probe.json
```

The script creates and removes only a randomly named, labeled probe container.
A failed probe retains synthetic files and diagnostic evidence under its printed
temporary path. If daemon access becomes unavailable during cleanup, inspect that
claim and exact container label before removing it; do not prune unrelated work.
The downloaded image remains cached. The probe is opt-in, outside default CI.

## Verified controls

The image is fixed by registry digest; the report also records its local image ID
and both probe script hashes. Only the synthetic input directory is bind-mounted,
read-only. No host home, Hearth database, credential, control directory or engine
socket is mounted. Host canaries and an absolute symlink to an unmounted canary
cannot be read; synthetic notes can. Input/root/temp writes fail.

The runner uses UID/GID 65534, all capabilities dropped, no-new-privileges and
explicit `seccomp=builtin`; the child checks UID, capability masks, NoNewPrivs and
Seccomp=2. This daemon reported an unconfined default, so relying on that default
would be incorrect. Version-pinned [Docker CLI handling](https://github.com/docker/cli/blob/v27.3.1/cli/command/container/opts.go#L920)
and [daemon selection](https://github.com/moby/moby/blob/v27.3.1/daemon/seccomp_linux.go)
establish that `builtin` overrides it. An unsupported kernel can still fall back;
the in-container check must pass.

Limits are inspected in the daemon configuration and cgroup v2: 32 PIDs, 64 MiB
memory, no additional swap and half a CPU. Scratch is a 1 MiB noexec/nosuid/nodev
tmpfs; a positive write succeeds and oversized allocation hits ENOSPC. `/dev/shm`
is a separate 1 MiB allocation. These are probe sizes, not proposed Codex limits.
CPU/PID/memory settings are verified, not stress-tested to exhaustion.
[Run controls](https://docs.docker.com/reference/cli/docker/container/run/),
[tmpfs behavior](https://docs.docker.com/engine/storage/tmpfs/).

`--network none` is inspected. The child checks no active non-loopback interface,
no IPv4 routes or non-loopback IPv6 routes, and failed connections to a reserved
IP and Docker's host hostname. This VM exposes dormant kernel tunnel interfaces,
so counting interface names alone was too strict; those interfaces must remain
down. An internal bridge is not equivalent: it can still reach its gateway/host.
[None network](https://docs.docker.com/engine/network/drivers/none/),
[internal network limits](https://docs.docker.com/reference/cli/docker/network/create/).

The fake child ignores SIGTERM and double-forks after `setsid`. A live heartbeat
and PID check prove it survives independently before cancellation. Stopping the
exact container with a one-second grace period produces exit 137, daemon PID 0,
Running=false and rejection of `docker top` as not running. Cleanup removes that
owned container. This verifies local container termination, not provider-side
cancellation or zero cost. [Stop behavior](https://docs.docker.com/reference/cli/docker/container/stop/).

## Remaining integration gates

Docker Desktop places containers inside a Linux VM, with host paths accessible
when explicitly mounted. This evidence supports using that boundary for the next
worker integration. It does not prove protection against every kernel exploit or
an operator changing the configuration. [Mac boundary](https://docs.docker.com/desktop/setup/install/mac-permission-requirements/).

Native Codex also supports restricted-read permission profiles; broad read-only
is not its only option. Neither configuration alone nor this Python-image probe
establishes the actual Codex process/tool boundary. [Codex permissions](https://learn.chatgpt.com/docs/permissions),
[sandbox behavior](https://learn.chatgpt.com/docs/agent-approvals-security).

The [offline Reader integration rehearsal](container-rehearsal.md) now verifies
actual 0400/0700 staged input using the non-root host UID/GID, durable container
claims and fresh-process reconciliation with a fixed synthetic executable. The
original UID-65534 canary probe remains a separate boundary check. The rehearsal
now retains durable terminal receipts. The actual worker still needs application
launch authority, backup integration, a pinned Codex image/version, final-file/event handling and provenance through accounting/artifacts.

A network-disabled runner cannot call the model. The future trusted model channel
must keep provider credentials outside generated tools, constrain requests and
usage, and prevent arbitrary network bypass. Docker's internal network or a proxy
hostname alone proves none of that. Authentication/billing, exact Astra access,
pricing and an explicitly selected real test remain open. The user-confirmed Mac
and $10/day window remain settled. Migration remains cancelled.
