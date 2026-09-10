# A sandbox per run: containers as the execution boundary

Status: accepted, 2026-09-10 (proposed 2026-09-09).

Hearth runs every resident's session as a subprocess of a detached worker on the host
it is started on (ADR 0008 for Codex, epic #144 for Claude). The worker is trustworthy —
it holds the store's authority, answers the management bridge and writes the receipt —
but the provider CLI it launches runs as the same user, on the same filesystem, with the
same network as Hearth itself. A resident is confined by flags Hearth measured
(`docs/claude-runtime.md`, `docs/mac-isolation.md`), not by a boundary. That was the
demo shape, and #106 has said since then that production needs a container. Hearth is
now meant to run primarily on servers — any Linux host with a container runtime, not
one particular machine — with residents that hold different privileges over different
folders, so the boundary is decided here. Warren is deprecated; nothing
in this decision depends on it or talks to it.

**Decision.** A run executes inside a container created for that run, and what a
resident may reach is exactly what its grant mounts into it.

- **One Hearth per burrow, one sandbox per run.** Hearth stays a single process with
  one store: budgets, admission, pricing, journals, letters and the task board are one
  household and one ledger, and splitting them per resident would split the household
  ceiling. The execution boundary is below the worker, not around Hearth. Every admitted
  run gets a fresh container from the sandbox image, started once, and the container
  ends when its receipt is written. There is no long-lived container per resident: a
  resident is rows in the store, and state a resident keeps lives in the store (memory,
  journal), never in a container that outlives a run. The launch-once and no-relaunch
  guarantees of ADR 0008 map onto "one container, one CLI process, one receipt" without
  change.
- **The worker stays on the host and stays the authority.** The detached worker with
  its inherited flock is unchanged: it is Hearth's, it holds `BoundRun`, it answers the
  bridge and it prices the stream. What changes is one seam beneath it — the *launcher*.
  `process` is today's launcher (the CLI as a child of the worker) and remains the
  development and test launcher; `container` is the production launcher and runs the
  same CLI, with the same flags, inside a container the worker starts, attaches to and
  waits on. The CLI's stdout is still the worker's pipe and still the receipt. The
  container id is recorded where the worker pid is recorded today; `inspect` after a
  restart asks the container runtime; cancellation stops the container, and a stopped
  container is a session that ended, with the usage it really had. A stray container
  whose worker is gone is removed, never adopted and never relaunched.
- **Pins.** The sandbox image is pinned by digest in `system_meta`, beside the binary
  hashes; a different digest refuses to start (`sandbox_image_changed`), as a different
  binary does today. The image carries both pinned CLIs and Hearth's own bridge shim, and
  nothing else that can reach the network. The provider binaries inside the image are
  hashed at image check and must equal the pins the store already holds.
- **Privilege is the mount list.** A management grant gains a filesystem section:
  `mounts`, a bounded list of host paths with a mode, read-only unless `rw` is said, each
  named so a run's prompt can refer to it. Admission turns the grant's mounts, at the
  grant's pinned revision, into the container's bind mounts and records them on the run
  (`run_mounts`), so a finished run says what it could see. A folder that is not in the
  grant is not in the container. Hearth's own data directory, any login directory and
  the container runtime's socket are never mountable, whatever a grant says; a grant
  naming one is refused at write time, not at admission. Writes that change the
  household — memory, journal, letters, work — still go through the bridge tools over
  the socket, in one audited transaction in the worker; a writable mount is for files a
  resident produces, and it is audited as `rw` when granted and when used.
- **The workspace is empty and the network is the provider.** The working directory is
  a fresh tmpfs. The sandbox has no route to Hearth's API, to the host or to the LAN;
  its egress is the provider's own API host and nothing else. The bridge socket is a
  unix socket in the run folder, mounted into the container, so a session needs no
  network path to Hearth at all. Enforcement is a named container network the operator
  creates with that policy; Hearth records the network's name as configuration and
  measures, at start and from inside the image, that Hearth's own port and a LAN address
  are unreachable, refusing `sandbox_network_open` otherwise — a fence Hearth cannot see
  holding is not one it relies on.
- **Identity.** The sandbox runs as Hearth's own uid. That is what keeps the bridge's
  peer-credential check meaningful across the boundary: the worker created the socket,
  the connecting process is the uid the worker expects, and nothing else on the host is
  that uid. Root inside the container is not offered.
- **Logins are per resident, as files, with a household fallback.** On Linux both CLIs
  keep file credentials, so a login is a directory Hearth mounts read-only at the CLI's
  own config path. A resident may have its own login directory (`credentials/<resident
  id>/<kind>` under Hearth's private data, seeded by the operator with the CLI's own
  login flow, never copied by Hearth); a resident without one uses the household's
  directory for that kind. Which login a run used is recorded on the run. The macOS
  Keychain path (`docs/claude-runtime.md`, spike 1) stays a convenience of the
  `process` launcher on a laptop and is not carried into the sandbox.
- **Bundles carry mounts and logins as definition** (ADR 0010): a mount's name and mode
  travel, its host path is resolved on import against what the target household allows;
  a login is never in a bundle.

**Consequences.**

- `execution/`, `work/` and `management/` still know no provider names, and now know
  no launcher either: the launcher is chosen by configuration (`HEARTH_SANDBOX=process`
  or `container`), and the runtime adapters call it through one interface. Tests keep
  the fake `codex` and fake `claude`; they gain a fake `docker` that runs the command it
  is given, so the suite never needs a daemon.
- A run on the `container` launcher costs a container start on top of the CLI start.
  Sessions measured so far take five to sixty seconds; the start is a small fraction and
  is recorded on the receipt.
- A resident's reach is finally something an operator can read off a grant and a
  finished run, rather than something inferred from flags. It is also something a
  resident can lose: a grant revision that removes a mount removes it from the next
  run, and a run already started keeps the mounts it was admitted with.
- The Mac stays a first-class development host on the `process` launcher. Production is
  the `container` launcher on Linux; the same store, moved by backup and restore (ADR
  0013's forward upgrades hold), runs on either.
- Hearth itself is packaged as a container for the server, deployable from one compose
  file on any Linux host with nothing tied to the machine, with the container runtime's
  socket mounted so it can start sandboxes. That socket is root-equivalent on the host,
  which is why the sandboxes and the mounts they get are Hearth's decision alone and no
  resident's, and why nothing a resident says reaches the launcher unparsed.

Not decided here: which container runtime beyond "OCI, driven through the `docker` CLI
with podman as a drop-in"; whether sandboxes may one day keep a warm image layer per
resident for speed; and any use of the sandbox for skills that run code. Those are
follow-ups once the boundary holds.

Implemented by epic #183, which supersedes #106.

**Measured.** Accepted on 2026-09-10, after epic #183 built all of it against a real
container runtime. The decisions above are left as they were written; this is what
building them found, and where a measurement contradicted an assumption in them the
measurement wins. Every one is written up with its evidence in `docs/sandbox.md`.

- **"The bridge socket is a unix socket in the run folder, mounted into the container"
  does not hold everywhere.** A Mac cannot bind-mount a socket file into the Linux VM
  at all — the mount fails before the container starts. It holds on a Linux host, where
  the run folder and the daemon share one kernel, which is why Hearth itself is a
  container on the server and why the Mac stays a `process`-launcher development host
  (measurement 6).
- **`--rm` does not end a container whose client died.** The ADR read it as "the
  container ends when its receipt is written"; a worker killed mid-session leaves a live
  container spending real money. Every container Hearth starts is therefore labelled and
  a stray is killed and removed, never merely reaped (measurement 5).
- **A read-only login directory is not what the pinned Codex CLI runs on.** "A login is
  a directory Hearth mounts read-only at the CLI's own config path" fails —
  `Read-only file system` — because that directory is where the CLI keeps session state.
  A run gets a tmpfs there with the credential bind-mounted read-only inside it, which
  is narrower: the run cannot change the login, and what it writes neither outlives it
  nor is visible to the next resident (measurements 8 and 12).
- **The image is built for one uid and cannot be run as another.** The Claude CLI asks
  the system where its home is before anything else, so the uid Hearth runs as has to
  exist in the image's own passwd file (measurement 11).
- **"Its egress is the provider's own API host and nothing else" is not what a fence
  can say.** The providers are behind CDNs whose addresses rotate; an address allowlist
  is a fence that breaks on somebody else's deploy, and saying it exactly needs an
  egress proxy the CLIs are pointed at. What `deploy/fence.sh` installs and what Hearth
  measures is *nothing of this house* — every private destination dropped, the host's own
  addresses dropped, the public internet left alone. The half of the decision that does
  hold is the half that mattered: Hearth's own API and the LAN are unreachable from
  inside a sandbox, measured from in there at every start, and an open fence refuses
  `sandbox_network_open` and does not open.
- **A Hearth in a container hands the daemon paths from the host's filesystem, not its
  own.** The ADR treats "Hearth is packaged as a container" as packaging; it is also a
  constraint on every path Hearth passes down — the bridge socket, a login directory, a
  granted folder. The deployment answers it by mounting each volume inside Hearth at the
  path it has on the host, and the store has to be a real filesystem for `flock` besides
  (measurement 9).
- **"A resident reaches exactly what its grant mounts" is true of the container and not
  yet of the session.** The mounts are placed, recorded on the run and enforced by the
  kernel — a read-only one refuses a write, a writable one takes it, and `/mounts` holds
  the grant and nothing else. But Hearth configures the Codex CLI with its filesystem
  denied at `/` and no shell tool, so a real session has no tool with which to open a
  granted folder: the acceptance run reached neither its read-only folder nor its
  writable one, and said so. Making a granted folder reachable *by the model* is a
  change to each provider's permission profile and is left to its own issue.
