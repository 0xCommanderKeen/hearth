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
| the CLI | the host's binary, at the path the pin names | `/usr/local/bin/codex` or `/usr/local/bin/claude`, the image's own |
| the login | the configuration directory itself | `/hearth/login`, below |
| the final message | `<run>/workspace/final.md` | `/hearth/output/final.md`, one writable mount |
| the working directory | `<run>/workspace` | `/workspace`, the runtime's empty tmpfs |
| generated settings | the file Hearth wrote | the same path, mounted where it already is |
| the bridge shim's interpreter | the one this worker is running | `/usr/local/bin/python3`, the image's own |
| the bridge socket and its configuration | the two files in the run folder | the same two paths, mounted where they already are |
| a folder the grant names | the host path itself, unconfined | `/mounts/<name>`, one bind mount, `readonly` unless the grant says `rw` |

Three of those are worth saying why.

**The login is a directory of the run's own with one file it may not change.** ADR 0016
said a login is a directory mounted read-only at the CLI's config path. Measured: the
pinned Codex CLI does not run that way. It initializes its own app-server client inside
`CODEX_HOME` and refuses --
`failed to initialize in-process app-server client: Read-only file system (os error 30)`
-- because that directory is where it keeps session state, logs and a model cache. So a
sandboxed session gets a tmpfs at `/hearth/login`, which is nothing of the host's and is
gone when the run ends, and the household's credential file is bind-mounted read-only
inside it (`auth.json` for Codex, `.credentials.json` for Claude). What the ADR wanted
from read-only holds and holds more narrowly: a run cannot change the login it was
given, and what it does write neither outlives it nor is visible to the next resident.
The credential has to be valid when the run starts, because a session that needs to
refresh it cannot write it back; keeping it fresh stays outside the sandbox, where it
always was.

The Claude CLI would in fact have run under the ADR's own shape -- measured, it answers
normally with a read-only configuration directory and simply writes nothing -- and it
is given the same tmpfs anyway, because what it writes there is the reason rather than
the error message: even `auth status` leaves a `.claude.json`, a lock and a `backups/`
directory, and a session adds `projects/` and `sessions/`. A household login the runs
wrote into would show the next resident what the last one left
(`docs/claude-runtime.md`, spike 8).

**The bridge's two files are mounted at the paths they already have.** Claude's session
reaches Hearth's own tools through a shim the CLI itself launches, named by a
`--mcp-config` document Hearth writes into the run folder beside the socket that
document points at. Both are mounted at their own paths, so the string in the document
is one string inside the sandbox and out and nothing has to agree with anything. The two
*files*, not the folder they sit in: a bind mount of a file creates that file and an
empty path down to it, so the run folder inside the container holds those two and
nothing else -- not the request document, which carries the run's owner token, and not
the receipt. What
does change is the interpreter named in it: inside the sandbox the shim is started by
the image's own `python3`, which is where Hearth's package sits for `python -I` to find
it. The socket itself is mounted by the launcher, and that is the mount a Mac cannot
make (measurement 6).

**The generated model catalog is mounted where it already is.** The app-server session
reports its own effective configuration back and Hearth compares it to what it sent, so
a path that changed on the way in would read as tampering. That is the same reason the
bridge socket is mounted at its own path.

## What a resident reaches on disk

A management grant carries `mounts`: at most sixteen entries of
`{name, host_path, mode}`, read-only unless the mode says `rw`
(`management/authority.py`, `docs/management.md`). It is not gated by the grant's
`enabled`: reaching a folder is not a management tool, and a resident with no
management authority at all may still be given one to read.

**What is refused, and when.** At write time, never at admission -- a run waiting on a
grant nobody will fix is a resident that never works again. `grant_mount_forbidden`
answers a relative or unnormalised path, `/`, `/etc`, `/proc`, `/sys`, this
installation's own protected paths, and a name or a path two mounts share. The
protected set is Hearth's data directory, the login directory of every runtime this
instance opened, and the container runtime's socket -- and containment counts both
ways: a mount *under* a protected path reaches part of it and a mount *above* one
reaches all of it, so both are refused. The root is the one exception to that rule,
because everything is under `/`; what `/` protects is itself.

**What admission does.** `run_mounts` is written in the same transaction as the
admission that pinned it, from the grant as it stands at that moment, with the grant's
revision on every row. A revision that removes a folder removes it from the *next* run;
a run already admitted keeps what it was admitted with. A granted folder the host does
not have refuses `mount_unavailable` before any money is reserved, and the task stays
queued until an operator puts the folder back or takes it out of the grant (ADR 0015's
wait rule; `mount_unavailable` is one of `ADMISSION_WAITS`, so a routine pass leaves the
task queued and reports no fault rather than holding one error open every pass). The run's own context, version 10, lists each folder by name, by the path it
has inside a sandbox, by the host path a run that is not sandboxed reaches, and by mode.

**What the launcher does.** The worker reads the list out of the request document
Hearth wrote, checks every field again -- nothing a resident says reaches a container's
argv -- and places each at `/mounts/<name>`. The process launcher places nothing,
because it confines nothing, and the run still records what it was granted: a run on a
laptop says honestly what it *would* have had. A management session gets the same
folders through the app-server transport, in both of the containers it starts.

**How a writable folder is known to have been used.** The worker surveys each writable
folder before the session starts and again once it has ended -- names, sizes and
modification times, never content -- and the receipt says of each mount whether it was
written, left alone, or not known. A folder too large to walk twice (`SURVEY_LIMIT`) or
one that cannot be read is `null`, never "untouched". Settlement turns a `true` into
`run.mount_rw_used`, in the transaction that ends the run, with the folder read from
what admission pinned rather than from the receipt: a receipt naming a folder this run
was not granted names nothing. Granting one is `grant.mount_rw_granted`, written where
the grant is.

**In a bundle** (ADR 0010) a folder travels as `{name, mode}` and never as a path: where
a folder is describes the machine the bundle was exported from. An import resolves each
name against a map the operator writes, grants exactly those, and leaves the rest out as
`mount_unresolved` in the resolution. The import goes through the same grant path, so
this household's protected paths refuse an import as they refuse an edit.

## Measured, 2026-09-09, a granted folder against the real daemon

`scripts/measure-mounts.py` -> `docs/evidence/sandbox-mounts-2026-09-09.json`. Docker
Desktop 27.3.1, server `linux/arm64`, kernel `6.10.14-linuxkit`. The image is the
sandbox image's own pinned base with the same user entry and no provider CLI in it:
what is measured is the runtime's treatment of a bind mount and the uid the session runs
as, and neither of those is the CLI's. The mounts come out of Hearth's own `granted()`
and the argv out of `ContainerLauncher`.

14. **A read-only mount is read-only to the kernel, not by convention.** The session
    read the file in `/mounts/notes` and its write into that folder failed
    `OSError: Read-only file system`. The mount argument Hearth built for it ends in
    `,readonly`.
15. **A writable mount takes the write, and the file is on the host.** The session
    wrote `/mounts/drafts/written-inside.md`; afterwards the host folder holds it, owned
    by uid 501 -- the uid Hearth itself runs as, which is the uid the launcher passes to
    `--user`.
16. **`/mounts` holds the grant and nothing else**, and the directory those two folders
    were carved out of does not exist inside the container at all.
17. **The survey answers what the receipt says.** Taken before and after, it reports the
    writable folder changed and the read-only one unchanged, and `used()` turns that into
    the receipt's `[{notes, ro, written: null}, {drafts, rw, written: true}]` -- `null`
    for the read-only one because the worker never surveys one, not because it was
    looked at and found untouched.

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

## Measured, 2026-09-09, with the real Claude CLI and the real bridge

The Codex slice put a real CLI in a real container. This one did the same for Claude,
and for the one thing that is Claude's alone: Hearth's own tools reaching a session
across the boundary. `scripts/measure-claude-sandbox.py` against the same daemon,
evidence `docs/evidence/sandbox-claude-2026-09-09.json`. Nothing was spent and no
credential was involved -- the login throughout is a synthetic file the script writes,
which the provider refuses with a 401, and that refusal is one of the answers. The whole
of it is written up in `docs/claude-runtime.md`, spike 8; the three that change how the
sandbox is built are here.

**11. The uid the sandbox runs as has to exist in the image's own passwd file.** With
`--user <uid>` on an image that does not name that uid, the Claude CLI ends before a
byte of its protocol: `ENOENT: no such file or directory, uv_os_homedir`. It asks the
system where its home is before it does anything else, and a uid nobody has named has
no answer. With the entry `deploy/Dockerfile.sandbox` creates -- home `/nonexistent`, on
a read-only root -- the same command answers normally. So the image is built *for* the
uid Hearth runs as, and an image built for one uid and run as another fails every Claude
run in it. Nothing else in the sandbox cared.

**12. A read-only login would have worked for this CLI, and it gets the tmpfs anyway.**
Measured both ways: a bounded session with the household's directory mounted read-only
at `CLAUDE_CONFIG_DIR` reached its first API answer exactly as the tmpfs one did. So
measurement 8's contradiction of ADR 0016 is Codex's alone. What decides it for Claude
is what the CLI *writes*: `.claude.json`, a lock, `backups/`, `projects/`, `sessions/`
-- into the login directory, on the cheapest question there is. The tmpfs lets it write
all of that and keeps none of it. After two sessions the household's login directory
held `.credentials.json` and nothing else.

**13. The bridge holds across the boundary, and the peer check is what holds it.** The
real shim, inside a container, started as `python3 -I -m
hearth.integrations.claude.mcp_bridge <socket>` from the very configuration Hearth
writes, connected over a socket mounted into it and had `initialize` and `tools/list`
answered by the trusted half outside. Both ends ran as Hearth's uid, and the socket sat
on a volume the daemon owns -- which is the burrow's shape, and the only shape available
here, because measurement 6 says a Mac cannot bind-mount a socket at all. The same shim
run as **another uid** was refused: the trusted half read `peer_uid = 0`, closed the
connection, and the session was told `-32603 the Hearth bridge did not answer`. That is
the whole of the bridge's authority story across the sandbox: it is not weakened by the
boundary, and it would be by a daemon that remaps uids (`userns-remap`), which is
therefore not a daemon Hearth's bridge holds on.

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

## The Claude journey, and what it still waits for

`scripts/claude-journey.py` is the three-run journey the Claude runtime was accepted on
(#149): one run reports and writes its own journal entry over the bridge, the next opens
with that entry and quotes it, the third is cancelled after it launches. It runs on the
`container` launcher by configuration alone -- `HEARTH_SANDBOX=container` with an image
and a network, and the harness in this page's Codex section, since the store's Claude pin
is the sha256 of the CLI Hearth was configured with and the image's copy is hashed
against it, so **Hearth itself has to be on Linux**. The evidence file it writes then
carries each run's own container id and image digest beside Hearth's settlement.

**It has not been run on the sandbox, and it cannot be from here.** Not for a reason in
Hearth: a sandboxed Claude session's login is the *file* `.credentials.json`
(measurement above), the Mac's login is a Keychain item, and only the account holder can
make that file, with a browser:

```sh
CLAUDE_CONFIG_DIR=/path/to/private-claude-config claude auth login
```

run against the **Linux** build of the pinned CLI -- in a container on the Linux Docker
host, with that directory on a volume. Until that exists, a store configured for the
container launcher refuses `claude_subscription_login_required` at start, which is the
refusal saying exactly this. Everything under it is measured: the CLI is the same
release and reports the same version string, the login mount is the shape it runs in,
and the bridge crosses the boundary.

The same three runs *were* run on the `process` launcher on 2026-09-09, after all of
this landed, and settled: `docs/evidence/claude-journey-process-2026-09-09.json` -- three
succeeded runs, 170,005 µ$ from the CLI's own numbers, run 1 writing its journal entry
over the bridge and run 2 quoting it back. It is not the sandbox journey and does not
claim to be; it is the evidence that the launcher this Mac can use still settles a real
session now that the adapter speaks in placements.

## Not yet true

- No resident has run against a real model with a granted folder: the mount itself is
  measured against the real daemon above, and the sessions that used one were the fake
  CLI's. The journeys in this page predate the grant.
- A management session's folders have the fake daemon's coverage and the transport's own
  test; its receipt names no container id, because such a run starts two, and what each
  of them was is in `handle.json` beside it.
- No Claude session has run in a sandbox against the real model: the CLI, the login
  mount and the bridge are each measured, and the paid three-run journey waits on the
  Linux login above.
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
