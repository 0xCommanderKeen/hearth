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
own stream either way. `start` returns the instant the session is launched and
`identify` reads what was created afterwards, because the worker holds Hearth's
dispatch guard -- one write transaction over the whole store -- around the launch and
nothing that waits may happen inside it.
`tests/integrations/test_launcher_parity.py` runs a real Codex session and a real
Claude session through both and holds the evidence identical.

## Configuration

Read once in the control plane:

```sh
HEARTH_SANDBOX=process|container       # default: process
HEARTH_SANDBOX_IMAGE=<repo>@sha256:... # required for container; a tag alone is refused
HEARTH_SANDBOX_NETWORK=<name>          # required for container; the operator creates it
HEARTH_SANDBOX_DOCKER=<docker|podman|/absolute/path>   # optional, default `docker`
HEARTH_SANDBOX_DOCKER_HOST=unix:///…   # optional; the daemon, when it is not the default
```

The launcher runs the container client with a search path and nothing else in its
environment. Two consequences, and both are why the last two variables exist rather
than being read from the ambient environment:

- a client that is not on `/bin:/usr/bin` -- a Mac with Docker Desktop, where it lives
  in `/usr/local/bin` -- has to be named by absolute path in `HEARTH_SANDBOX_DOCKER`;
- `DOCKER_HOST` is *not* inherited, so a daemon that is not on the client's own default
  socket -- a rootless installation at `unix:///run/user/<uid>/docker.sock`, or a remote
  host -- has to be named in `HEARTH_SANDBOX_DOCKER_HOST`. Without it such a host
  refuses `sandbox_runtime_unavailable` at start even though `docker` works perfectly
  for the operator who set it up.

Neither could be an inherited variable: the detached worker that starts a session is
launched with `PATH` and nothing else, so everything the client needs travels in the
run's own request document with the rest of the sandbox.

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

A failure to hash is not automatically a mismatch: the client's exit code 125 (and a
client that cannot be run, and a timeout) is the daemon refusing to run the container
at all and refuses as `sandbox_runtime_unavailable`, because sending an operator to
rebuild an image over a daemon that went away helps nobody.

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

Codex also needs the executables its own package ships beside it -- `bwrap` for its own
filesystem sandbox and the code-mode host it looks for by name -- and the image keeps
them in the same shape that package does (measurement 8). They are covered by the
image's digest; the binary pin is still the CLI itself.

It is not built in continuous integration, because no CI host has the pinned provider
CLIs, and nothing Hearth ships downloads them. It was built by hand on 2026-09-09: once
with two stand-in binaries to prove the file itself, and then with the real pinned Codex
CLI for the journey below. The image runs as the build's uid, has no `apt` and no `pip`,
and imports the bridge shim under `python3 -I` -- which is how the shim is started and
why Hearth's package sits in the interpreter's own site directory rather than anywhere
`PYTHONPATH` would have to name.

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
session: 0.139 s median through the container launcher against 0.022 s for a plain
child -- roughly 0.10 s of overhead, on sessions the ADR measures in five to sixty
seconds. As assumed.

**2. The stream is not buffered.** Five lines written 0.3 s apart inside the container
arrived 0.30 s apart at the attached worker (spread 1.2 s). The worker can price and
cancel a session mid-turn under the container launcher exactly as it does today.

**3. Stopping the container ends the whole session, but not by signalling it.**
`docker kill --signal SIGTERM` reaches PID 1 only. In a container running four
processes, the session's own process was signalled and its *child* was not: the child
died with the container, never having run its handler. That differs from the process
launcher, which signals the whole process group and reaches every one of them. It is
the same outcome -- nothing survives -- but a CLI that cleans up on SIGTERM in a
subprocess will not get to. The container was gone 0.155 s after the signal, and `--rm`
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
(`org.hearth.sandbox`): a stray has to be findable without guessing. It is also why
`stop` falls back to signalling the client itself when the runtime named no container
or will not answer -- that leaves a labelled stray, but the worker gets its receipt
instead of waiting on a session it cannot end.

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

**7. An instance really starts on it, and refuses by name when it should.** Against the
same daemon, with a stand-in image and a network created for the check, an instance
configured `HEARTH_SANDBOX=container` started and answered:

```
GET /health: {"runtimes": [...], "sandbox": {"image": "sha256:cad9a2c8…525ef6",
              "launcher": "container"}, "service": "hearth"}
GET /api/health sandbox: {"image": "sha256:cad9a2c8…525ef6",
              "launcher": "container", "network": "hearth-sandbox-check"}
```

and the same store, restarted pointed at a different image, refused
`sandbox_image_changed`; at a network nobody created, `sandbox_network_missing`; at an
image the daemon does not hold, `sandbox_image_unavailable`.

## What a session sees of the host

One adapter builds one command and either launcher starts it, so everything that
differs between them is a path. `Placement` (`integrations/launcher.py`) is that
translation, and it collects the mounts the launcher is then handed.

| what | `process` | `container` |
| --- | --- | --- |
| the CLI | the host's binary, at the path the pin names | `/usr/local/bin/codex`, the image's own |
| the login | the configuration directory itself | `/hearth/login`, below |
| the final message | `<run>/workspace/final.md` | `/hearth/output/final.md`, one writable mount |
| the working directory | `<run>/workspace` | `/workspace`, the runtime's empty tmpfs |
| generated settings | the file Hearth wrote | the same path, mounted where it already is |

Two of those are worth saying why.

**The login is a directory of the run's own with one file it may not change.** ADR 0016
said a login is a directory mounted read-only at the CLI's config path. Measured: the
pinned Codex CLI does not run that way. It initializes its own app-server client inside
`CODEX_HOME` and refuses --
`failed to initialize in-process app-server client: Read-only file system (os error 30)`
-- because that directory is where it keeps session state, logs and a model cache. So a
sandboxed session gets a tmpfs at `/hearth/login`, which is nothing of the host's and is
gone when the run ends, and the household's `auth.json` is bind-mounted read-only inside
it. What the ADR wanted from read-only holds and holds more narrowly: a run cannot
change the login it was given, and what it does write neither outlives it nor is visible
to the next resident. The credential has to be valid when the run starts, because a
session that needs to refresh it cannot write it back; keeping it fresh stays outside
the sandbox, where it always was.

**The generated model catalog is mounted where it already is.** The app-server session
reports its own effective configuration back and Hearth compares it to what it sent, so
a path that changed on the way in would read as tampering. That is the same reason the
bridge socket is mounted at its own path.

## Measured, 2026-09-09, with the real pinned CLI

The first slice measured the launcher against a stand-in image. This one built
`deploy/Dockerfile.sandbox` with the real pinned Codex CLI -- the Linux build of the
same version, `codex-cli 0.153.4` -- and ran a resident's session in it. Three things
came out of that, and every one of them failed every run it touched.

**8. Codex on Linux is not one file.** Its own package ships three more things beside
the executable and the CLI finds them by their place in that layout:

- without `codex-resources/bwrap` the session ends before its first turn --
  `bubblewrap is unavailable` -- because Hearth's read-only permission profile turns
  the CLI's *own* filesystem sandbox on, and that runs through bubblewrap;
- without `codex-code-mode-host` the CLI reports `failed to spawn ... host executable
  was not found` where it otherwise reports `code-mode host is disabled`. Hearth knows
  the second and steps over it; the first it reads as a stream error, and the run
  settles failed.

So the image carries them in the same shape the package does, one directory above the
executable. They are pinned by the image's digest; the binary pin is still the CLI
itself.

**9. `flock` does not exclude on a Docker Desktop bind mount from macOS.** Two
processes took the same lock on a file under a bind-mounted Mac directory and both
succeeded; on the container's own filesystem the second was refused, as it must be.
Hearth's whole launch-once discipline rests on that lock (ADR 0008), so a store on such
a share is not a store Hearth can hold its guarantees on -- but the sharp edge this
slice added is that a free lock was about to *authorise killing a container*. It no
longer does on its own: the worker records its own pid beside the container it started,
and a session whose worker is still here is never called a stray. A lock that is wrong
now leaves a labelled container for the operator instead of ending somebody's work.

**10. A stray really is killed and removed.** Seen against the real daemon, by way of
the finding above: the observation decided a container had no worker, `docker kill` and
`docker rm --force` ended it, the run settled as unknown rather than at zero, and
`sandbox.stray_removed` was recorded with the container's id.

## What a run writes down about what it started

`handle.json` beside the receipt is the only thing that can find a container after the
worker that started it is gone -- `stray` will not sweep by Hearth's label, because the
label finds every other resident's live session too. So it is written more than once,
and it is the one file in a run folder that is *replaced* rather than published:

- **before the runtime has named the container**, carrying the file the runtime will
  write the name into. Asking for the id waits, and a worker killed while waiting would
  otherwise leave a live container that nothing on disk names; the cidfile is usually
  there a tenth of a second later, and reading it is how such a container is still
  found. Giving up on reading the id is not evidence that no container exists, so that
  file is kept for exactly as long as there is no id: otherwise the write that follows
  a failed reading would clear the pointer the write before it had just made.
- **again once the id is known**, and once more for each further session -- a management
  run starts two containers, one after the other, and whichever it is holding is the one
  that has to be findable.
- **with the worker's own pid**, which is the second reason required before anything is
  killed (measurement 9).

A container Hearth removes is only audited as removed when the runtime no longer has it:
a kill the daemon refused, or a removal already under way, is a container the next
observation finds again, and an audit fact is a claim about the world.

## The Codex journey, on a Linux Docker host

`scripts/codex-sandbox-journey.py` drives one resident through one run and records the
container it ran in, the image digest, Hearth's settlement and the usage the CLI itself
reported inside that container. Evidence:
`docs/evidence/sandbox-codex-journey-2026-09-09.json` -- one run, `succeeded`, 29,812
µ$ settled from the CLI's own numbers, in container `6a49e62b…`, from image
`sha256:5a769ff0…`, and the container is gone afterwards.

It must run on a Linux Docker host, and it means it: the store's Codex pin is the
sha256 of the CLI Hearth was configured with, the image's own copy of that CLI is
hashed against it at start, and a macOS build and a Linux build are never the same
bytes. On a Mac, Docker Desktop's Linux VM *is* a Linux Docker host, and Hearth can run
on it in a container of its own with the daemon's socket. That is how the evidence above
was recorded, and it is worth writing down because two details of it are not obvious:

- **The repository, the login and the data directory are mounted at the paths they have
  outside**, because a path Hearth hands the daemon is resolved by the daemon, not by
  Hearth's own filesystem. A path that means one thing inside Hearth's container and
  another to the daemon is a mount of the wrong directory.
- **The store goes on a Linux filesystem**, not on a bind-mounted Mac directory --
  measurement 9. A docker volume mounted at its own path under
  `/var/lib/docker/volumes/<name>/_data` is both at once: a real filesystem for Hearth,
  and a path the daemon can bind-mount subdirectories of into each sandbox.
- **A locally built image has no digest to pin.** `HEARTH_SANDBOX_IMAGE` wants
  `<repo>@sha256:…`, which is a *repository* digest and only exists once an image has
  been pushed somewhere. A `registry:2` container on the loopback address is enough, and
  nothing leaves the machine.

On a Linux server none of that applies: Hearth is a process on the host, its data
directory is a directory, and the journey is the command in its own docstring. On the
Mac it was this, with `$REPO` the checkout's own path and the data directory a volume:

```sh
docker run --rm --user "$(id -u):0" \
    -v /var/run/docker.sock:/var/run/docker.sock \
    -v hearth-journey:/var/lib/docker/volumes/hearth-journey/_data \
    -v "$REPO:$REPO" -v "$LOGIN:$LOGIN" -w "$REPO" \
    -e HEARTH_SANDBOX=container -e HEARTH_SANDBOX_IMAGE=localhost:5555/hearth/sandbox@sha256:… \
    -e HEARTH_SANDBOX_NETWORK=hearth-sandbox -e HEARTH_SANDBOX_DOCKER=/usr/local/bin/docker \
    <a python:3.14-slim with fastapi, uvicorn, the docker client and $REPO/backend on its path> \
    python scripts/codex-sandbox-journey.py --codex-binary … --codex-auth-home "$LOGIN" \
        --data /var/lib/docker/volumes/hearth-journey/_data/journey --out docs/evidence/…json
```

`gid 0` because Docker Desktop's socket is `root:root` inside a container; `$REPO/backend`
on the interpreter's own path (a `.pth` file, not `PYTHONPATH`) because the detached
worker is started with `python -I`.

## Not yet true

- The Claude adapter still names its CLI by its host path; #186 moves it, and the
  bridge socket it needs is the one measurement 6 says is Linux-only.
- Nothing resolves a grant into a mount list yet: `Launcher.start` takes one and the
  argv is built from it, but no caller passes one. That is #187.
- The network the sandbox is on is the operator's own, and Hearth does not yet measure
  from inside it that Hearth's API and the LAN are unreachable (#189). The journey above
  used an ordinary bridge network, so it proves the provider is reachable and nothing
  about what else is.
- Hearth itself is not packaged (#189). The container the journey ran in is a harness,
  not `deploy/compose.yaml`.
- A **management** session in a container has the fake daemon's coverage and not a real
  daemon's: the journey is one `codex exec` run. One thing in it is worth measuring when
  #186 or #189 next has a real host — the model catalog Hearth generates goes in a
  temporary directory, which on Linux is under `/tmp`, and `/tmp` inside the sandbox is
  a tmpfs the launcher mounts over. Nested that way it is the same shape as the login
  mount, which a real daemon does handle. It is also a *host* path, so a Hearth that is
  itself in a container hands the daemon a path from its own filesystem, which the daemon
  resolves in the host's — that is #189's problem, and the same one the journey harness
  works around by mounting everything at the paths it has outside.
