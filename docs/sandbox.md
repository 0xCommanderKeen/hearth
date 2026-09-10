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
`tests/integrations/test_launcher_parity.py` runs synthetic Codex and Claude CLI
sessions through both and holds the interpreted evidence identical.

## Review corrections — 2026-09-10

A receipt records the attached CLI stream; it cannot prove a container ended after
its daemon refused cancellation. A receipt-bearing run stays interrupted with its
reservation held until the daemon confirms the recorded container is absent. Cleanup
retries on the ordinary active-run pass, then settlement uses the original receipt.
An unavailable daemon is `unknown`, never `absent`, and cannot produce a
`sandbox.stray_removed` audit fact. Codex discovery must confirm its container's
removal before starting the turn's container and replacing its recorded identity.

Admission rechecks a grant's resolved targets against protected installation paths
and pins canonical host paths. Launch refuses a target that has become a symlink.
Before recording launch intent, Hearth also rechecks an already-admitted run against
the current installation's protected paths: a restart that moves a login into its
folder holds that run without holding unrelated residents. Configured login roots
remain protected even when their adapter could not open. These checks enforce the
existing boundary; no new mount authority or store schema is introduced.

Townhall's projection identity includes current login scopes, so seeding or removing
a resident login refreshes HTTP and SSE views while idle without inventing an audit
mutation. The folder editor qualifies isolation as a sandbox property.

These corrections have synthetic CLI, temporary SQLite and fake-daemon regression
evidence. The real-host evidence below was not repeated, and its remaining gates
stay open.

## Configuration

Read once in the control plane:

```sh
HEARTH_SANDBOX=process|container       # default: process
HEARTH_SANDBOX_IMAGE=<repo>@sha256:... # required for container; a tag alone is refused
HEARTH_SANDBOX_NETWORK=<name>          # required for container; the operator creates it
HEARTH_SANDBOX_DOCKER=<docker|podman|/absolute/path>   # optional, default `docker`
HEARTH_SANDBOX_DOCKER_HOST=unix:///…   # optional; the daemon, when it is not the default
HEARTH_SANDBOX_SHUT=<host:port>,…      # required for container; must be unreachable
HEARTH_SANDBOX_OPEN=<host:port>,…      # required for container; must be reachable
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
| `sandbox_fence_unconfigured` | neither list of the fence below may be empty |
| `sandbox_fence_unmeasured` | the probe in the image could not answer at all |
| `sandbox_network_open` | the fence does not hold; the audit says which address answered |

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

## The fence, and what Hearth will not take on trust

ADR 0016 says a session's only network is the provider's, and immediately afterwards
that *a fence Hearth cannot see holding is not one it relies on*. Enforcement is the
operator's -- a network with that policy, `deploy/README.md` for how one is built --
and Hearth's part is the seeing. `integrations/reach.py` is the whole of what runs on
the sandbox network: the standard library, one TCP connection per address, one JSON
document, started as `python3 -I -m hearth.integrations.reach` from the same pinned
image a session runs from, as the same uid, with nothing mounted and nothing in its
environment. `integrations/fence.py` is the half outside that reads the answers.

The vocabulary is the point, because a fence is judged on it:

| answer | what happened | is that a fence holding? |
| --- | --- | --- |
| `connected` | the handshake completed | no -- and for an *open* address it is the only yes |
| `dropped` | nothing came back | yes: a filtered packet looks like this |
| `no route` | the kernel would not send it | yes |
| `refused` | a reset came back | **no**: the packet arrived, and a fence that lets it arrive is relying on nothing listening tomorrow |
| `unresolved` | the name has no address here | no: that is not a fact about the network |
| `unreadable` | not an address this probe can dial | no: that is an operator's own typo |

Anything else the kernel says comes back as its own `errno` name, lower-cased, and is
read as "not blocked" for the same reason `refused` is: a verdict must never be a word
chosen because nothing better fit.

`HEARTH_SANDBOX_SHUT` names what must not be reachable -- Hearth's own address, one
address on the LAN -- and `HEARTH_SANDBOX_OPEN` what must be, which is the provider.
Hearth derives neither: which address answers for Hearth depends on how the operator
published it, and which address is "the LAN" is a fact about a building. Both lists are
required on the container launcher, because an empty one is a measurement that always
passes; an empty one refuses `sandbox_fence_unconfigured`.

A start measures it before any resident is admitted, records what it saw as an audit
fact `sandbox.fence` -- and *then* refuses `sandbox_network_open` if it did not hold,
because an operator whose instance will not start needs to read which address answered.
`GET /api/health` measures it again on the ask, afresh, like the login survey and for
the same reason: an operator asking is asking about now. It is not free -- a container
start plus the whole of `reach.TIMEOUT`, because a dropped packet is measured by waiting
-- so that endpoint takes a couple of seconds on a fence that holds. Every address is
dialled at once, so it is one `TIMEOUT` however many addresses there are. A probe that
could not answer at all is `sandbox_fence_unmeasured` and is never read as a fence that
held.

**What the fence is, exactly.** `deploy/fence.sh` drops every private destination from
the sandbox subnet in `DOCKER-USER` and the host's own addresses in `INPUT`, and leaves
the rest alone. That is *nothing of this house*, not "the provider and nothing else":
the providers are behind CDNs whose addresses rotate, so an address allowlist is a fence
that breaks on somebody else's deploy, and saying it exactly needs an egress proxy the
CLIs are pointed at, which is not built. ADR 0016's Measured section records the
difference. Two other things do the work beside those rules and are worth knowing:
Docker's own `DOCKER-ISOLATION` chains already keep one user-defined network from
reaching another, which is what closes Hearth's own address (measurement 22 -- with the
filter removed and Hearth still listening, its address still timed out while the LAN
answered); and the daemon's embedded resolver may forward name lookups from inside the
container's own namespace, which is why the filter has a hole for exactly that address
and nothing else.

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
always was. *Which* credential is mounted comes off the run itself -- the resident's own
login or the household's, pinned at admission -- and that is *Whose login a run spends*,
below.

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
installation's own protected paths, a name two mounts share, and one folder reached
twice -- the same path under two names, or two paths where one holds the other, because
`/data` writable beside `/data/secrets` read-only is `/data/secrets` writable under
another name and the audit would name only the first. The protected set is Hearth's data
directory, the login directory of every runtime this instance opened, and the container
runtime's socket -- and containment counts both ways: a mount *under* a protected path
reaches part of it and a mount *above* one reaches all of it, so both are refused. The
root is the one exception to that rule, because everything is under `/`; what `/`
protects is itself. Every path is held to all of this as it is written *and* as it
resolves: the daemon resolves a bind mount's source on the way in, so a symlink would
otherwise be the way past every rule here.

A grant is written once and a host changes afterwards, so the launch is a second gate.
`granted()` re-applies what needs no configuration -- absolute, normalised, and never
`/`, `/etc`, `/proc` or `/sys` -- and `ContainerLauncher` refuses any mount that reaches
the socket of the daemon it is about to talk to, including the one an operator pointed
it at after the grant was written (`sandbox_mount_forbidden`). What an installation
protects beyond that is configuration the detached worker does not carry, and it stays
the grant's own check.

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

## Whose login a run spends

A household has one login per provider, and a resident may have one of its own. Both
are directories; the difference is only where they are and who they are for.

| | the household's | a resident's own |
| --- | --- | --- |
| where | `HEARTH_CODEX_AUTH_HOME` / `HEARTH_CLAUDE_CONFIG_DIR`, anywhere the operator likes | `<data>/credentials/<resident id>/<kind>/`, always |
| who seeds it | the operator, with that CLI's own login flow | the operator, with that CLI's own login flow |
| who uses it | every resident that has none of its own | that one resident, on that one runtime kind |
| when it is checked | when the adapter opens, or the runtime does not open at all | at start, on `GET /api/health`, and before each of that resident's runs launches |

**Hearth never creates a login and never copies one.** It makes the shelf --
`<data>/credentials`, mode `0700` -- and nothing on it. Everything Hearth ever does with
the contents is mount them into a session and ask the CLI whether that directory is
logged in. It never reads a credential, and the answer it keeps is one boolean.

**The directory decides the scope; validity decides whether the run happens.** At
admission, `runs.login_scope` is `resident` if `<data>/credentials/<id>/<kind>/` exists
and `household` otherwise -- read once, so a login seeded or taken away afterwards
belongs to the *next* run, exactly as a grant's mounts do. An **empty** directory, or one
whose login has lapsed, is still the resident's own login: such a run waits with
`login_required` until an operator fixes it, and never falls back to the household's.
Falling back would spend a different subscription than the operator chose, and the point
of a resident's own login is that its money and the household's are different money.

**Which login a run spent is on the run and on its receipt.** `runs.login_scope` is
Hearth's own record of what it admitted, and the receipt's `login_scope` is the worker's
statement of what it launched with; the operator API and Townhall read the run's, for
the same reason a writable folder is audited from `run_mounts` and not from the receipt.
A run admitted before this column existed reads as `household`, which is what it really
spent, because it was the only login there was.

**A lapsed login holds one resident and nobody else.** The probe answer is remembered
for a minute (`logins.REFRESH`), because the supervisor looks at a held run twice a
second and a probe starts a CLI; whether the directory is still *there* is asked every
time. Every other resident in the household is launched by the same pass. Nothing is
spent and nothing is thrown away: the held run was never launched, and it runs as soon
as the login works again.

The answer is stamped **after** the probe and never before, because asking a CLI whether
a directory is logged in writes into that directory. Measured 2026-09-10 against the
pinned `2.1.263` on a throwaway directory: the first `auth status --json` answers
`loggedIn: false` in **0.142 s** and leaves a `.claude.json`, a `.claude.json.lock` and
a `backups/` behind; stamped before the probe, every answer would look stale the moment
it was given, and three asks in a row would have started three CLIs instead of one.
This is also the reason the *session* gets a tmpfs rather than the household's folder
(measurement 12). For the same reason, every path that asks -- an operator's survey as
much as the executor's own question -- writes what it heard into the one memory, so a
`/api/health` poll refreshes the executor's answer instead of spoiling it.

The probe runs inside the executor's own pass, under its lock, so the pass does wait for
it: one probe of a tenth of a second, at most one a minute per held resident, and up to
`PROBE_TIMEOUT` (30 s) for a CLI that hangs. That is the cost of not launching a session
on a login nobody could vouch for. What a held resident does *not* do is hold anybody
else's run: the pass goes on to every other resident.

**Where an operator sees it.** `GET /api/health` names every lapsed one under
`login.resident_lapsed` (probed afresh on each ask, behind the operator's token; nothing
about a login reaches the open `/health`). Beside it, `login.resident_unknown` names the
ones this instance could not get an answer about at all -- a provider whose CLI would not
start has not told anybody a login lapsed, and sending an operator to run a login flow
they do not need is a worse answer than saying nothing is known. Each *lapsed* one is
audited once per start as `login.resident_lapsed`; a run held for a login is audited once
per process as `run.waiting`, beside the run, because a run sitting at `starting` for a
reason nobody wrote down is unreadable. Townhall's resident view says, provider by
provider, whether that resident is on its own login or the household's, and a finished
run says which it spent. With no server running,
`python -m hearth credentials --data <dir>` lists them: resident, kind, path and
`logged_in` -- `null` where this host has no pinned binary to ask with, because a login
nobody probed has not lapsed. It reads `HEARTH_SANDBOX` for the same reason the server
does, so on a burrow that sandboxes its runs it asks the stricter question the sessions
will be asked rather than the one this host would answer.

**A login never travels in a bundle** (ADR 0010): not the credential, not the directory,
not even the fact that the exporting household gave that resident one. An imported
resident is on its new household's login until an operator seeds it one.

### Seeding one, per kind

Point the CLI's own configuration path at the directory and run its own login flow.
Nothing else puts a login there, and Hearth refuses to invent one.

```sh
mkdir -p -m 700 <data>/credentials/<resident id>/codex_subscription
CODEX_HOME=<data>/credentials/<resident id>/codex_subscription /path/to/codex login
```

```sh
mkdir -p -m 700 <data>/credentials/<resident id>/claude_subscription
CLAUDE_CONFIG_DIR=<data>/credentials/<resident id>/claude_subscription \
  ~/.local/share/claude/versions/2.1.263 auth login
```

**The headless options each CLI offers**, read from the pinned builds on 2026-09-10
(`codex-cli 0.153.4`, `2.1.263 (Claude Code)`):

- **Codex** `login` takes `--device-auth` for a host with no browser of its own, and
  `--with-api-key` / `--with-access-token`, each reading the secret from stdin, for a
  host where the operator already holds one. `codex login status` says whether it
  worked; on an empty `CODEX_HOME` it answers `Not logged in` and exits 1 (measured).
  **Hearth does not run it**: its logged-in answer is a line of prose naming the
  account, and Hearth's own probe is the presence of `auth.json`, which is exactly what
  the household's login is checked for when the adapter opens.
- **Claude** `auth login` takes `--claudeai` (the default subscription flow), `--console`
  (API billing instead, which is not what this pin is for), `--email` to pre-populate
  the login page, and `--sso`. There is **no device-code flag** on this build, so a
  server with no browser needs the browser flow completed somewhere that has one -- with
  a forwarded port, or by the operator placing the resulting `.credentials.json` in the
  directory by hand. Hearth's own probe is `auth status --json`, read for `loggedIn` and
  nothing else; on the container launcher the credential also has to be a *file* there,
  because a Keychain does not cross the boundary (measurement 11).

Verify without reading anything secret -- the answer also names the account, and only
`loggedIn` is ever Hearth's business:

```sh
python -m hearth credentials --data <data>
```

Run on this Mac on 2026-09-10 against a throwaway data directory holding a copy of a
real Codex login and an empty Claude directory, with `HEARTH_CLAUDE_BINARY` pointed at
the pin, it answered `logged_in: true` for the one and `logged_in: false` for the other
-- the real CLI really ran -- and printed nothing else of either. The copy was deleted
straight afterwards; no credential is in this repository.

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

## Hearth on the server, and the acceptance demo

The journeys above ran Hearth in a hand-built harness. This one ran it from
`deploy/compose.yaml`: Hearth's own image, a store on a named volume, the runtime
socket, `hearth-egress` with `deploy/fence.sh` on it, and a fresh household.
`deploy/README.md` is the runbook; two things about the shape are worth repeating here
because they are measurements rather than taste.

**Every volume is mounted inside Hearth at the path it has on the host.** A path Hearth
hands the daemon -- a run's bridge socket, a login directory, a granted folder -- is
resolved by the daemon in the *host's* filesystem, not in Hearth's, so a named volume is
mounted at `<docker data root>/volumes/<name>/_data` on both sides. The store also has
to be a real filesystem for `flock` and SQLite, which a bind mount from a Mac is not
(measurement 9); a volume is both at once. That is the answer to what the harness
worked around, and to the open question the Codex slice left about a containerized
Hearth's own temporary directories.

**Hearth is not on the sandbox network.** No service in the compose file is, which is
why the compose file cannot create `hearth-egress` either: an operator makes it, puts
the filter on it, and Hearth measures the result.

**Measured, 2026-09-10.** Docker Desktop 27.3.1, server `linux/arm64`, kernel
`6.10.14-linuxkit`; a fresh store on named volumes, throwaway ports, nothing on any
other machine. `scripts/sandbox-journey.py` ->
`docs/evidence/sandbox-journey-2026-09-10.json`.

18. **The fence holds, measured from inside `hearth-egress`.** Hearth's own address on
    its own network `dropped`, this building's router `dropped`, `chatgpt.com:443`
    `connected`. Recorded as `sandbox.fence` at start and answered afresh on every
    `GET /api/health`.
19. **An instance whose fence does not hold does not open.** With `deploy/fence.sh
    remove` and nothing else changed, the same deployment refused
    `sandbox_network_open` on restart and stayed down, and the audit says which address
    answered: `{"held": false, ... "192.168.1.1:80": "connected"}`. Putting the filter
    back brought it up again on its own.
20. **A routine run, on a resident's own login, in a per-run sandbox, settled.** The
    scheduler's own occurrence made the task; the run succeeded at **97,130 µ$** from
    the CLI's own numbers, in container `cf4ff1f4...` from the pinned image, and the
    container was gone afterwards -- "gone" meaning the daemon answered *No such
    container*, not merely that a question failed. `login_scope` is `resident` on the run and on the
    receipt: the resident's own directory, not the household's.
21. **The grant is on the run and the folders are in the container; the *session* still
    cannot open them.** `run_mounts` names `notes` (ro) and `drafts` (rw) at the grant's
    revision, the receipt names both, and `grant.mount_rw_granted` is audited. But the
    session reached neither -- it made no tool call at all and said so in its answer --
    because Hearth configures the Codex CLI with `permissions.reader.filesystem./` set
    to `deny` and `features.shell_tool` off. The mount is real and the kernel enforces
    it (measurements 14--17); what is missing is a tool with which to use it. Making a
    granted folder reachable by the model is a change to each provider's permission
    profile and is its own issue.
22. **Two mechanisms hold the two halves of the fence, and they are not the same one.**
    Hearth's own probe, run by hand from the pinned image on `hearth-egress` with
    `deploy/fence.sh remove` and Hearth still up and listening:

    ```
    with the filter:     {"172.30.0.2:8000": "dropped",   "192.168.1.1:80": "dropped",
                          "chatgpt.com:443": "connected"}
    without the filter:  {"172.30.0.2:8000": "dropped",   "192.168.1.1:80": "connected",
                          "chatgpt.com:443": "connected"}
    ```

    So Hearth's own address is closed by Docker's own `DOCKER-ISOLATION` chains, which
    keep one user-defined network from reaching another, and the LAN and the host are
    closed by the filter and only by the filter. Both are measured together and neither
    is assumed; it is worth knowing which is which, because a daemon configured to let
    its networks talk would move the first one and Hearth would refuse on the next
    start.
23. **The Claude half did not run, for the reason it has run out of since #186**:
    `claude_subscription_login_required`, recorded in the evidence as not run. A
    sandboxed Claude session's login is the file `.credentials.json`, and only the
    account holder can create one against the Linux build.

The demo has to reach Hearth *and* see the store, which on a burrow whose filesystem is
volumes means a container of Hearth's own image with the store volume mounted at its own
path. On this Mac it was this, with `$REPO` the checkout:

```sh
docker run --rm --user "$HEARTH_UID:$HEARTH_DOCKER_GID" --network hearth \
    -v /var/run/docker.sock:/var/run/docker.sock \
    -v hearth-store:/var/lib/docker/volumes/hearth-store/_data \
    -v hearth-folders:/var/lib/docker/volumes/hearth-folders/_data \
    -v "$REPO:$REPO" -w "$REPO" -e HEARTH_OPERATOR_TOKEN \
    --entrypoint /opt/hearth/bin/python "$HEARTH_IMAGE" \
    scripts/sandbox-journey.py --base http://172.30.0.2:8000 \
        --store /var/lib/docker/volumes/hearth-store/_data \
        --folders /var/lib/docker/volumes/hearth-folders/_data \
        --out docs/evidence/sandbox-journey-<date>.json
```

On a Linux server it is the command in the script's own docstring: the store is a
directory the operator can read and Hearth answers on a port.

The resident's own login has to be seeded *before* the demo runs, because a login is
resolved at admission. Provisioning derives a resident's id from its idempotency key, so
the directory can be made before the resident exists -- which is the only order that
works, and is why the script computes the id rather than reading it back.

**The store moves between the two burrows, both ways.** `scripts/store-round-trip.py`
-> `docs/evidence/store-round-trip-2026-09-10.json`: a Mac `process` store captured on
the Mac and restored and *opened* on the Docker host, which answered
`"sandbox": {"launcher": "container"}`; and this demo's store captured on the Docker
host and opened on the Mac, which answered `"launcher": "process"` with the run and the
image pin it arrived with. Both copies came up held, as ADR 0013 says a restored copy
must, and neither carried a credential: the format copies `hearth.db`, `artifacts/` and
`memory/` by name and never the tree the logins live in.

## Not yet true

- No resident has run against a real model *using* a granted folder. The folder is
  granted, admitted, placed, recorded and enforced by the kernel; the session has no
  filesystem tool with which to open it (measurement 21). That is the provider
  permission profile's own question and not the mount's.
- A management session's folders have the fake daemon's coverage and the transport's own
  test; its receipt names no container id, because such a run starts two, and what each
  of them was is in `handle.json` beside it.
- No Claude session has run in a sandbox against the real model: the CLI, the login
  mount and the bridge are each measured, and the paid three-run journey waits on the
  Linux login above.
- **Two** residents on different logins of their own have not run against a real model.
  One has (measurement 20), on a login of its own; a second real login is a second
  subscription and only the account holder can seed one. The two-resident acceptance is
  driven end to end with a fake CLI that reports the configuration directory it was
  handed, which is what proves the right one reaches each session.
- The fence is measured, not narrowed: what holds is "nothing of this house" and not
  "the provider and nothing else". An address allowlist would break on a CDN's own
  deploy; saying it exactly needs an egress proxy the CLIs are pointed at, which is not
  built. ADR 0016's Measured section records the difference.
- The fence filter is not persistent by itself: `DOCKER-USER` is rebuilt when the daemon
  restarts, so `deploy/fence.sh apply` belongs in whatever restores firewall state at
  boot. Nothing rests on remembering, because a start that cannot see the fence refuses.
- **The fence is IPv4 and only IPv4, and that is the one way an open fence could read
  as holding.** `deploy/fence.sh` touches `iptables` and never `ip6tables`, and
  `HEARTH_SANDBOX_SHUT` cannot name an IPv6 address either, because `host:port` cannot
  hold one unambiguously. On a sandbox network created with `--ipv6` a session would
  have an unmeasured v6 path to the host and the LAN while the measurement said the
  fence held. Docker does not enable IPv6 on a network by default and the runbook says
  not to; closing it properly means fencing v6 and giving the probe a way to say so,
  and neither exists yet.
- A login taken away in the moment between a run being gated and being launched is not
  held for the operator. The launch intent is already recorded by then, so that run is
  interrupted instead -- visibly, with nothing spent, and without ending the pass for
  any other resident. It is the same narrow shape every start-time refusal has.
- Codex's own lapse detection is the credential file and nothing more. `codex login
  status` would say more and Hearth does not ask it: its answer names the account. So a
  Codex login whose token has expired while its `auth.json` is still on disk reads as
  present here, and the session that spends it fails at the provider rather than
  waiting. Claude's probe is the CLI's own answer and does not have that gap.
- A **management** session in a container has the fake daemon's coverage and not a real
  daemon's: every journey so far is one `codex exec` run. The path question it raised is
  settled -- a containerized Hearth hands the daemon paths from the host's filesystem,
  and `deploy/compose.yaml` answers it by mounting every volume at the path it has there
  -- but the model catalog it generates goes in a temporary directory under `/tmp`,
  which inside a sandbox is a tmpfs the launcher mounts over, and that nesting has still
  only been seen with a fake daemon.
