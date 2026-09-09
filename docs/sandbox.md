# The sandbox: where a run's session executes

`docs/adr/0016-sandbox-per-run.md` decided that a run executes inside a container
created for that run. This page is the seam that decision rests on -- how it is
configured, what it refuses, and what a real container runtime was measured to do with
it. The measurements are authoritative: where they disagree with what the ADR assumed,
the measurement wins and this page says so.

## The two launchers

Hearth's detached worker is unchanged and is still the authority: it holds the run's
flock, answers the management bridge, reads the CLI's stdout and writes the receipt.
One step beneath it, `backend/hearth/integrations/launcher.py` owns starting the
session, and there are exactly two answers.

| | `process` | `container` |
| --- | --- | --- |
| what runs | the CLI, as a child of the worker | the CLI, inside a container of its own |
| the receipt | the child's stdout | the container's stdout, attached |
| identity | the worker's process group | the container id |
| cancellation | signal the process group | signal the container |
| a mount list | refused (`sandbox_mounts_unsupported`) | the container's bind mounts |
| where | development, tests, the Mac | production, any Linux host |

Both hand back one `Handle`, so the worker's read loop, its cancellation and its
timeout do not know which launcher they are talking to, and the receipt is the CLI's
own stream either way. `tests/integrations/test_launcher_parity.py` runs a real Codex
session and a real Claude session through both and holds the evidence identical.

## Configuration

Three environment variables, read once in the control plane:

```sh
HEARTH_SANDBOX=process|container       # default: process
HEARTH_SANDBOX_IMAGE=<repo>@sha256:... # required for container; a tag alone is refused
HEARTH_SANDBOX_NETWORK=<name>          # required for container; the operator creates it
HEARTH_SANDBOX_DOCKER=<docker|podman|/absolute/path>   # optional, default `docker`
```

The launcher runs the container client with a search path and nothing else in its
environment, so on a host where the client is not on `/bin:/usr/bin` -- a Mac with
Docker Desktop, where it lives in `/usr/local/bin` -- `HEARTH_SANDBOX_DOCKER` has to
name it by absolute path.

The choice is *not* read by the worker: the worker is detached with `PATH` alone, so
the sandbox travels in the run's own request document and is validated again there. A
run admitted before this seam existed carries no sandbox and is read as `process`,
which is what it really ran as.

## What is pinned, and what refuses

At start, on the `container` launcher, in this order:

| refusal | what was wrong |
| --- | --- |
| `sandbox_configuration_invalid` | the image is not digest-pinned, or the network or client name is not a name |
| `sandbox_runtime_unavailable` | the container runtime did not answer at all |
| `sandbox_image_unavailable` | the daemon does not hold that image (nothing is ever pulled) |
| `sandbox_network_missing` | the operator has not created that network |
| `sandbox_image_changed` | this store is pinned to a different image digest |
| `sandbox_binary_mismatch` | a CLI inside the image is not the one this store's binary pin names |

The image digest is written to `system_meta.sandbox_image` on the first start that
sees it, with an audit fact `sandbox.configured`, and every later start compares
against it -- exactly as a provider binary's sha256 does. The digest, not the
reference: the same bytes under another repository are the same sandbox.

The CLIs inside the image are hashed by the image's own `sha256sum`
(`docker run --entrypoint sha256sum`, measured at 0.1 s) and compared to
`codex_live_binary` and `claude_live_binary`. This runs *after* the runtime adapters
are built, so a store configured for a provider on this very start is checked on this
start rather than the next one.

`GET /health` names the launcher and, on `container`, the image digest -- a digest
names bytes, not a machine, so it tells a reader which sandbox is deployed without
telling a LAN peer anything about the host. The network's name stays behind the
operator's token on `GET /api/health`, with every reason a runtime did not open.

## The image

`deploy/Dockerfile.sandbox`: Debian slim with Python 3.14, the two pinned CLIs at
`/usr/local/bin/codex` and `/usr/local/bin/claude`, Hearth's package in the
interpreter's own site directory for the bridge shim alone (the shim is started with
`python -I`, which ignores `PYTHONPATH`), no package manager left runnable, and a
non-root user at the uid the build was given. The CLIs are copied in from the build
context, never downloaded: an image that fetches its own tools cannot be the same image
twice, and the whole point of the pin is that it can.

It is not built in continuous integration, because no CI host has the pinned provider
CLIs, and nothing Hearth ships downloads them.

## Measured, 2026-09-09

`scripts/measure-sandbox.py` measures the six facts the decision rests on, through the
launcher Hearth actually ships. Evidence: `docs/evidence/sandbox-2026-09-09.json`.

```sh
uv run --frozen python scripts/measure-sandbox.py \
    --docker /usr/local/bin/docker --report docs/evidence/sandbox-<date>.json
```

Host: Docker Desktop 27.3.1 on macOS, server `linux/arm64`, kernel `6.10.14-linuxkit`,
overlay2. That Linux VM is a Linux Docker host; nothing was run on any other machine.

**1. A container start costs about a tenth of a second.** First byte out of the
session: 0.127 s median through the container launcher against 0.023 s for a plain
child -- roughly 0.10 s of overhead, on sessions the ADR measures in five to sixty
seconds. As assumed.

**2. The stream is not buffered.** Five lines written 0.3 s apart inside the container
arrived 0.30 s apart at the attached worker (spread 1.215 s). The worker can price and
cancel a session mid-turn under the container launcher exactly as it does today.

**3. Stopping the container ends the whole session, but not by signalling it.**
`docker kill --signal SIGTERM` reaches PID 1 only. In a container running four
processes, the session's own process was signalled and its *child* was not: the child
died with the container, never having run its handler. That differs from the process
launcher, which signals the whole process group and reaches every one of them. It is
the same outcome -- nothing survives -- but a CLI that cleans up on SIGTERM in a
subprocess will not get to. The container was gone 0.075 s after the signal, and `--rm`
had removed it.

**4. A signalled session's exit code is 128 + the signal, not its negative.** A session
that did not handle SIGTERM reported **143** to the attached client, where a child of
the worker reports **-15**. This is the one place the two launchers' receipts differ,
and it is asserted rather than normalised: both are "not zero", which is all any reader
of the receipt asks, and rewriting the number would mean the receipt no longer said
what the runtime said. `tests/integrations/test_launcher_parity.py` holds the
difference.

**5. Killing the attached worker does *not* stop the container.** The client was killed
with SIGKILL; the container went on running and stayed listed by the daemon. `--rm`
removes a container when it *ends*, not when its client leaves. **This contradicts the
naive reading of `--rm` in the ADR**: a worker that dies mid-session leaves a live
container spending real money, and the stray cleanup slice (#185) has to kill it, not
merely reap it. After an explicit `docker kill` the container ended and `--rm` removed
it with no trace. This is why the launcher labels every container it starts
(`org.hearth.sandbox`): a stray has to be findable without guessing.

**6. The bridge socket cannot be a bind-mounted host path on Docker Desktop.**
Mounting the unix socket itself into the container failed before the container started:

```
invalid mount config for type "bind": stat /host_mnt/.../bridge.sock: operation not supported
```

The macOS filesystem reaches the Linux VM over a virtual filesystem that does not carry
socket files. **This contradicts the ADR's assumption** that "the bridge socket is a
unix socket in the run folder, mounted into the container" holds everywhere: it holds
on a Linux host, where the run folder and the daemon share one kernel, and it does not
hold on a Mac. The production shape was measured separately and works: with the socket
in a volume the daemon owns, a server container and a client container connected and
exchanged bytes (`connected pong`). So on the server -- Hearth in a container, the run
folder on a volume -- the bridge is reachable; on a Mac the `container` launcher cannot
carry Hearth's own tools into a session, and the Mac stays a `process`-launcher
development host, which is what the ADR decided anyway for other reasons.

## Not yet true

This slice is the seam, the pins and the measurements. No run executes in a container
yet beyond the measurement script:

- The command the adapters build still names the CLI by its **host** path. Inside the
  image the CLIs are at `/usr/local/bin/{codex,claude}`; translating the command, the
  login directory and the workspace is #185 (Codex) and #186 (Claude).
- Nothing resolves a grant into a mount list yet: `Launcher.start` takes one and the
  argv is built from it, but no caller passes one. That is #187.
- `inspect` after a restart, and removing a stray container whose worker is gone, are
  #185 -- measurement 5 above is what that slice has to handle.

Until then, setting `HEARTH_SANDBOX=container` pins the image and starts the instance;
the runs it launches are expected to fail, because the paths inside the image are not
the paths on the host.
