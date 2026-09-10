# A sandbox per run: containers as the execution boundary

Status: proposed, 2026-09-09.

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
