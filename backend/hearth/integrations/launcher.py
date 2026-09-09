"""The one seam beneath the worker: how a session's process is started, and where.

Hearth's detached worker is the authority and stays on the host: it holds the run's
flock, answers the management bridge, reads the CLI's stdout and writes the receipt
(`docs/adr/0008-native-subscription-demo.md`, `docs/adr/0016-sandbox-per-run.md`).
What this module owns is the single step under it -- starting the provider's CLI --
and it offers exactly two answers.

- `ProcessLauncher` is what Hearth has always done: the CLI is a child of the worker,
  in its own session, signalled by process group. It is the development and test
  launcher and **it is not a boundary**: a mount list means nothing to it, because the
  whole host filesystem is already reachable by the process it starts. It says so by
  refusing to be handed one.
- `ContainerLauncher` starts the same command inside a container created for that run,
  from an image pinned by digest, on the operator's own container network, read-only
  with an empty workspace, as Hearth's own uid. What the session can reach is the
  mount list and nothing else.

Both hand back a `Handle` whose `stdout` is the worker's pipe, so the receipt is the
CLI's own stream either way, and both are driven by `stop`/`wait`, so the worker's
cancellation and timeout paths do not know which one they are talking to.

The launcher is chosen by configuration (`HEARTH_SANDBOX`) and travels to the detached
worker inside the run's own request document, because that worker is launched with a
search path and nothing else in its environment. `Sandbox` is that configuration: it
validates itself, it serializes, and it opens the launcher it names.

Measured against Docker Desktop's Linux VM on 2026-09-09; what was measured, and the
two places it differs from what the ADR assumed, is `docs/sandbox.md`.
"""

import os
import re
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import IO, Protocol

from hearth.residents.models import Refused

PROCESS = "process"
CONTAINER = "container"
KINDS = (PROCESS, CONTAINER)

# `system_meta` key holding the digest of the sandbox image this store was configured
# with, beside the provider binary pins. A different digest refuses to start, exactly
# as a different binary does.
IMAGE_PIN = "sandbox_image"

# A reference that names the bytes it resolves to. A tag alone is a moving target and
# is refused: the pin has to be something a later start can compare against.
IMAGE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/:-]{0,255}@(sha256:[0-9a-f]{64})\Z")
NETWORK = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\Z")
# The container CLI itself: a bare program name found on the search path, or one
# absolute path. `podman` is a drop-in and is spelled here the same way.
CLIENT = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}\Z|/[A-Za-z0-9._/-]{1,4095}\Z")
IDENTITY = re.compile(r"[0-9a-f]{64}\Z")
# The daemon a client is told to talk to, when it is not the client's own default.
HOST = re.compile(r"(unix|tcp|ssh|npipe)://[A-Za-z0-9._:/@%-]{1,4095}\Z")
VARIABLE = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,127}\Z")

# The working directory a sandboxed session runs in: a fresh tmpfs, never a host path.
WORKSPACE = "/workspace"
# Every container Hearth starts carries this label, so what it left behind is always
# distinguishable from what somebody else is running on the same host.
LABEL = "org.hearth.sandbox"
# Where the sandbox image keeps each provider's pinned CLI. The image is built to put
# them here (`deploy/Dockerfile.sandbox`), and these are the files whose sha256 has to
# equal the binary pin the store already holds.
CLI = {"codex": "/usr/local/bin/codex", "claude": "/usr/local/bin/claude"}
BINARIES = {name + "_live_binary": path for name, path in CLI.items()}
# Where Hearth's own files are placed inside a sandbox. A session needs three of them
# and no more: the login directory the CLI reads, the directory it writes its final
# message into, and the settings Hearth generated for this session alone. A resident's
# own folders are its grant's business and are named by the grant (slice #187).
LOGIN = "/hearth/login"
OUTPUT = "/hearth/output"
# How much a directory of the run's own may hold. The CLIs write session state and
# logs into their configuration directory; nothing a run keeps belongs there.
SCRATCH = "64m"
# One question to the container runtime -- a version, an inspect, a hash -- may take
# this long. A daemon that cannot answer in half a minute is unavailable.
CLIENT_TIMEOUT = 30
# What one question about a session nobody is holding any more may cost. This is asked
# from an observation pass, once per unfinished run, where the alternative to a slow
# answer is a supervisor that stops looking at everybody else's work; a daemon that
# cannot say whether a container exists in five seconds is asked again next pass.
STRAY_TIMEOUT = 5
# How long the client is given to write the id of the container it created before the
# run is treated as one whose identity was never observed.
IDENTITY_TIMEOUT = 30


@dataclass(frozen=True)
class Mount:
    """One path inside the sandbox: something of the host's, or nothing at all.

    With a source it is one host path the run may reach, read-only unless `writable`
    is said. With no source it is a directory of the run's own -- a tmpfs the runtime
    creates and takes away again -- for the places a CLI insists on writing and where
    nothing it writes should outlive the run or be visible to the next one.

    Nothing here decides *whether* a host path may be mounted -- that is the grant's
    business (epic #183, slice #187) -- only that what reaches the container runtime is
    a shape a command line cannot be smuggled through.
    """

    source: str | None
    target: str
    writable: bool = False

    def __post_init__(self):
        for path in ((self.source,) if self.source is not None else ()) + (self.target,):
            if (
                not isinstance(path, str)
                or not path.startswith("/")
                or len(path) > 4096
                or any(character in path for character in ",=\n\0")
            ):
                raise Refused("sandbox_mount_invalid")
        if type(self.writable) is not bool:
            raise Refused("sandbox_mount_invalid")

    def argument(self) -> str:
        if self.source is None:
            return f"type=tmpfs,destination={self.target},tmpfs-size={SCRATCH}"
        value = f"type=bind,source={self.source},target={self.target}"
        return value if self.writable else value + ",readonly"


@dataclass
class Placement:
    """Where the host's files are, as the session itself names them.

    One adapter builds one command for two launchers, and the difference between them
    is only ever a path. On `process` a path is itself and nothing is mounted: the
    session is a process on this host and already reaches every file Hearth can. In a
    container nothing on the host exists unless it is mounted, the CLI is the image's
    own copy at a path the image decides, and the working directory is a tmpfs. So an
    adapter asks this for every path it is about to put in a command, an environment
    or a request to the CLI, and hands `start` the mounts it collected on the way.

    Nothing here decides *whether* a path may be reached -- Hearth's own login,
    output and settings are the only ones it is asked about, and a resident's folders
    come from its grant (slice #187). What it does hold is that two different host
    paths never quietly land on one path inside the sandbox, where the second would
    hide the first.
    """

    launcher: str = PROCESS
    placed: dict[str, Mount] = field(default_factory=dict)

    @property
    def contained(self) -> bool:
        return self.launcher == CONTAINER

    @property
    def mounts(self) -> tuple[Mount, ...]:
        return tuple(self.placed[target] for target in sorted(self.placed))

    def binary(self, path, name: str) -> str:
        """The CLI this session runs: the host's copy, or the image's own.

        A container runs what the image carries, never what the host has. That is
        only sound because the image's own CLIs are hashed against this store's
        binary pins before any resident is admitted (`configure`), so the receipt
        still names the bytes that ran.
        """
        if not self.contained:
            return str(path)
        if name not in CLI:
            raise Refused("sandbox_binary_unknown")
        return CLI[name]

    def place(self, mount: Mount) -> str:
        """One path inside the sandbox, held against anything else claiming it."""
        if self.placed.setdefault(mount.target, mount) != mount:
            raise Refused("sandbox_mount_conflict")
        return mount.target

    def directory(self, path, target: str, *, writable: bool = False) -> str:
        """One host directory, at the path the session will name it by."""
        if not self.contained:
            return str(path)
        return self.place(Mount(str(path), target, writable=writable))

    def login(self, directory, credential: str, target: str) -> str:
        """The CLI's own configuration directory, and the one file in it Hearth owns.

        ADR 0016 said a login is a directory mounted read-only at the CLI's config
        path. Measured on 2026-09-09 against the pinned Codex CLI, that does not run:
        the CLI initializes its own app-server client inside `CODEX_HOME` and refuses
        on a read-only filesystem (`failed to initialize in-process app-server client:
        Read-only file system`). So the session is given a configuration directory of
        its own -- a tmpfs that is nothing of the host's and is gone when the run ends
        -- with the household's credential as the one file in it, read-only.

        What the ADR wanted from read-only holds and is stronger: a run cannot change
        the login it was given, and what it does write (session state, logs, a
        regenerated cache) neither outlives it nor is visible to the next resident.
        The credential must be a valid one when the run starts, because a session that
        needs to refresh it cannot write it back; keeping it fresh stays outside the
        sandbox, where it always was.
        """
        if not self.contained:
            return str(directory)
        self.place(Mount(None, target, writable=True))
        self.place(Mount(str(Path(directory) / credential), f"{target}/{credential}"))
        return target

    def same(self, path) -> str:
        """One host directory, mounted at the path it already has.

        For files Hearth generates for a session and then hears named back to it --
        the model catalog the CLI reports as its own effective configuration -- a path
        that changed on the way in would be a disagreement Hearth reads as tampering.
        Those are placed where they already are, which is what the bridge socket does
        for the same reason (`docs/adr/0016-sandbox-per-run.md`).
        """
        return self.directory(path, str(path))

    def workspace(self, path) -> str:
        """The session's working directory, which in a sandbox is the empty tmpfs."""
        return WORKSPACE if self.contained else str(path)


@dataclass
class Handle:
    """One started session: which launcher started it, its identity, and its stream.

    `id` is the worker's own process group on `process` and the container id on
    `container` -- the thing a later observation asks about. On the container launcher
    it is not known the instant the session starts, because the runtime writes it to a
    file of its own; `identify` reads it, and it stays `None` when the runtime never
    named one, which is an execution nobody can name and never evidence that a retry
    is safe.
    """

    launcher: str
    id: str | None
    process: subprocess.Popen
    # Where the container runtime was told to write the id of what it created, and the
    # private directory holding it. Both are gone once `identify` has read them.
    identity: Path | None = None
    directory: str | None = None

    @property
    def stdout(self) -> IO[bytes] | None:
        return self.process.stdout

    @property
    def returncode(self) -> int | None:
        return self.process.returncode

    def document(self) -> dict:
        """What is known about this session right now, and how to find out the rest.

        Before the runtime has named the container there is still something to write
        down: the file it is going to name it in. A worker that dies while waiting for
        that name leaves a live container, and this is the only thing anyone can find
        it by -- `stray` will not sweep by label, because a label finds every other
        resident's session too.
        """
        return {
            "launcher": self.launcher,
            "id": self.id,
            "cidfile": str(self.identity) if self.id is None and self.identity else None,
        }


class Launcher(Protocol):
    kind: str

    def start(
        self,
        command: list[str],
        *,
        env: dict[str, str],
        cwd: Path,
        stdin,
        mounts: tuple[Mount, ...] = (),
        socket: Path | None = None,
        bufsize: int = -1,
    ) -> Handle: ...

    def identify(self, handle: Handle) -> str | None: ...

    def inspect(self, handle: Handle) -> str: ...

    def stop(self, handle: Handle, signal: int) -> None: ...

    def wait(self, handle: Handle, timeout: float | None) -> int | None: ...

    def stray(self, identity: str | None) -> bool: ...


class ProcessLauncher:
    """Today's launcher: the CLI as a child of the worker, in its own session.

    Byte for byte the launch Hearth has always done. It confines nothing, so it takes
    no mounts: a caller that has a filesystem grant to enforce is on the wrong
    launcher, and being told so is better than a boundary that quietly is not one.

    A bridge socket is accepted and does nothing, which is the truth here: the session
    is a process on this host and the socket is already at the path its configuration
    names. Only a boundary has to be told about it.
    """

    kind = PROCESS

    def start(self, command, *, env, cwd, stdin, mounts=(), socket=None, bufsize=-1):
        if mounts:
            # The process launcher cannot take anything away from the session it
            # starts; pretending to apply a mount list would be a fence nobody holds.
            raise Refused("sandbox_mounts_unsupported")
        child = subprocess.Popen(
            command,
            cwd=cwd,
            env=env,
            stdin=stdin,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            bufsize=bufsize,
            # The CLI runs a process tree of its own, so it gets its own session and
            # the signal below goes to the whole group rather than to one pid.
            start_new_session=True,
        )
        return Handle(self.kind, str(child.pid), child)

    def identify(self, handle):
        """A child names itself the moment it exists; there is nothing to wait for."""
        return handle.id

    def inspect(self, handle):
        if handle.process.poll() is None:
            return "running"
        return "exited"

    def stop(self, handle, signal):
        try:
            os.killpg(handle.process.pid, signal)
        except ProcessLookupError:
            pass

    def wait(self, handle, timeout):
        try:
            return handle.process.wait(timeout)
        except subprocess.TimeoutExpired:
            return None

    def stray(self, identity):
        """Nothing. A pid this launcher no longer holds is not provably its own.

        The session a dead worker left behind is a process group on this host, and a
        process group is a number the kernel reuses. Signalling one on the strength
        of a file written before the worker died could end somebody else's work, so
        this launcher leaves it to the operator and says it did nothing. A container
        id cannot be confused with anything: it is what the runtime named once.
        """
        return False


class ContainerLauncher:
    """The same command, inside a container created for this run and nothing else.

    The client is run attached: its stdout *is* the container's stdout, so the worker
    reads the CLI's own stream through one pipe as it always has, and the client exits
    when the container does with the container's own exit code. `--rm` makes the
    container's removal the runtime's business, and the id is read from `--cidfile`
    so a later observation can ask about the container by name.

    Nothing a resident says reaches this argv unparsed: the command comes from
    Hearth's own adapter, the mounts from a grant Hearth resolved, and the image and
    network from configuration only an operator writes.
    """

    kind = CONTAINER

    def __init__(
        self, image: str, network: str, *, docker: str = "docker", host: str | None = None
    ):
        self.image = image
        self.network = network
        self.docker = docker
        self.host = host

    def argv(self, *arguments: str) -> list[str]:
        """The client, the daemon it is told to talk to, and the rest.

        The client is run with a search path and nothing else in its environment, as
        the worker itself is, so `DOCKER_HOST` cannot be inherited from whoever
        started Hearth. A daemon that is not on the client's default socket -- a
        rootless installation, a remote host -- is named by configuration instead and
        travels with the rest of the sandbox.
        """
        return [self.docker, *(("--host", self.host) if self.host else ()), *arguments]

    def attempt(self, *arguments: str, timeout: float) -> subprocess.CompletedProcess:
        return subprocess.run(
            self.argv(*arguments),
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
            env={"PATH": os.defpath},
        )

    def client(self, *arguments: str, timeout: float = CLIENT_TIMEOUT) -> str:
        """One question to the container runtime. No answer at all is unavailable."""
        try:
            result = self.attempt(*arguments, timeout=timeout)
        except OSError, subprocess.SubprocessError:
            raise Refused("sandbox_runtime_unavailable") from None
        if result.returncode:
            raise Refused("sandbox_runtime_unavailable")
        return result.stdout.strip()

    def arguments(self, command, *, env, mounts, socket) -> list[str]:
        """The whole argv of one sandboxed session, validated before it is a process."""
        for name, value in env.items():
            if not VARIABLE.match(name) or not isinstance(value, str) or "\0" in value:
                raise Refused("sandbox_environment_invalid")
        if (
            not isinstance(command, list)
            or not command
            or not all(isinstance(part, str) and "\0" not in part for part in command)
        ):
            raise Refused("sandbox_command_invalid")
        if socket is not None:
            # The bridge socket is mounted at the very path the session's own
            # configuration names, so the shim inside the container connects to the
            # string Hearth already wrote and no translation layer exists to disagree.
            mounts = (*mounts, Mount(str(socket), str(socket), writable=True))
        return self.argv(
            "run",
            # One container, one session, removed by the runtime when it ends. Hearth
            # never reuses one and never adopts a stray.
            "--rm",
            "--init",
            "--interactive",
            # An image the daemon does not already hold is a refusal, never a pull:
            # what runs is the digest an operator put there.
            "--pull",
            "never",
            # Hearth's own uid, which is what keeps the bridge's peer-credential check
            # meaningful across the boundary. Root inside the container is not offered.
            "--user",
            f"{os.getuid()}:{os.getgid()}",
            "--network",
            self.network,
            "--read-only",
            "--tmpfs",
            WORKSPACE + ":rw,noexec,nosuid,nodev,size=64m",
            "--tmpfs",
            "/tmp:rw,noexec,nosuid,nodev,size=64m",
            "--workdir",
            WORKSPACE,
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges=true",
            "--pids-limit",
            "512",
            # Every container Hearth starts says so, so a stray left by a worker that
            # died can be found and removed rather than guessed at.
            "--label",
            LABEL + "=1",
            *[part for name, value in sorted(env.items()) for part in ("--env", f"{name}={value}")],
            *[part for mount in mounts for part in ("--mount", mount.argument())],
        )

    def start(self, command, *, env, cwd, stdin, mounts=(), socket=None, bufsize=-1):
        """Start the session and return at once. `identify` reads what was created.

        Nothing is waited for here. The caller holds Hearth's dispatch guard -- one
        write transaction over the whole store -- around this call, so the only thing
        that may happen inside it is the launch itself.
        """
        argv = self.arguments(command, env=env, mounts=mounts, socket=socket)
        # The runtime refuses to overwrite a cidfile, so it is written into a private
        # directory of this launch's own and read once. The durable record of what was
        # started is the worker's, beside the receipt; this is a handover, and it is
        # deliberately not the session's working directory, which a provider's own
        # adapter may require to be empty.
        directory = tempfile.mkdtemp(prefix="hearth-sandbox-")
        identity = Path(directory) / "container-id"
        argv += ["--cidfile", str(identity), self.image, *command]
        try:
            child = subprocess.Popen(
                argv,
                cwd=cwd,
                env={"PATH": os.defpath},
                stdin=stdin,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                bufsize=bufsize,
                start_new_session=True,
            )
        except OSError:
            # A client that cannot even be executed is this host's configuration, and
            # it refuses by the same name as a daemon that will not answer -- so the
            # worker settles the run rather than dying with an exception nobody wrote
            # a receipt for.
            shutil.rmtree(directory, ignore_errors=True)
            raise Refused("sandbox_runtime_unavailable") from None
        return Handle(self.kind, None, child, identity=identity, directory=directory)

    def identify(self, handle):
        """The id of the container the client created, once it has written it.

        Read after the dispatch guard has closed, because it waits: the client writes
        the cidfile when the container is created, which is ordinarily a tenth of a
        second away and is not something to hold the store's write lock for.

        A client that ended without ever naming one leaves `None`: the session's
        identity was never observed, and no later observation may guess at it.
        """
        if handle.id is not None or handle.identity is None:
            return handle.id
        deadline = time.monotonic() + IDENTITY_TIMEOUT
        while True:
            try:
                value = handle.identity.read_text().strip()
            except OSError:
                value = ""
            if IDENTITY.match(value):
                handle.id = value
                break
            # The file is read before the client is asked whether it is still running,
            # and never the other way round: a client that wrote the id and exited in
            # between the two would otherwise leave a container Hearth created and
            # cannot name -- which is a session nobody can end, and exactly the stray
            # this launcher is supposed to be able to find.
            if handle.process.poll() is not None or time.monotonic() >= deadline:
                break
            time.sleep(0.02)
        if handle.directory is not None:
            shutil.rmtree(handle.directory, ignore_errors=True)
        handle.identity, handle.directory = None, None
        return handle.id

    def inspect(self, handle):
        """What the container runtime says about the container, or that it is gone."""
        if handle.id is None:
            return "unknown"
        return self.status(handle.id)

    def stop(self, handle, signal):
        """End the session. Whatever else happens, the client does not outlive this.

        Signalling the container by name is the ordinary path and the only one that
        really ends the session, because killing the attached client does *not* stop
        the container (measured, `docs/sandbox.md`). But a stop that did nothing at
        all -- the runtime never named a container, or the daemon will not answer --
        would leave the worker waiting on a client forever and the run would never
        settle. So the client's own process group is signalled as the fallback: the
        worker gets its receipt, and the container, if there is one, is a stray with
        Hearth's label on it for the runtime's own cleanup to find.
        """
        if handle.id is not None:
            try:
                self.client("kill", "--signal", str(int(signal)), handle.id)
                return
            except Refused:
                # A container that has already ended cannot be signalled; the client
                # this launcher is attached to has already reported the ending, and
                # the fallback below is a no-op on a process that is gone.
                pass
        try:
            os.killpg(handle.process.pid, signal)
        except ProcessLookupError:
            pass

    def wait(self, handle, timeout):
        try:
            return handle.process.wait(timeout)
        except subprocess.TimeoutExpired:
            return None

    def status(self, identity: str) -> str:
        """What the runtime says about one container, asked by id and nothing else."""
        try:
            state = self.client(
                "inspect",
                "--type",
                "container",
                "--format",
                "{{.State.Status}}",
                identity,
                timeout=STRAY_TIMEOUT,
            )
        except Refused:
            # `--rm` means a finished container is removed, and an inspect that finds
            # nothing is the ordinary ending, not a broken daemon.
            return "absent"
        return "running" if state == "running" else "exited"

    def stray(self, identity):
        """End and remove a container whose worker is gone. True if there was one.

        `--rm` removes a container when it *ends*; it does not end one whose attached
        client died, and a session nobody is reading is a session still spending
        money (measured, `docs/sandbox.md`). So a stray is killed by the id the
        runtime wrote down and then removed, and it is never adopted: the worker that
        held its stream is gone, so nothing can price what it said, and relaunching
        anything is refused by ADR 0008 whatever this answers.

        Only the id Hearth recorded for this run is touched. Sweeping by Hearth's own
        label would find every other resident's live session on the same burrow.
        """
        if not isinstance(identity, str) or not IDENTITY.match(identity):
            return False
        if self.status(identity) == "absent":
            # Either it ended and `--rm` removed it, or the daemon is not answering.
            # Both are "there is nothing here for Hearth to remove"; a daemon that
            # comes back is asked again by the next observation.
            return False
        for question in (("kill", identity), ("rm", "--force", identity)):
            try:
                self.client(*question, timeout=STRAY_TIMEOUT)
            except Refused:
                # It may have ended between the two questions and `--rm` taken it
                # away, which is the ending this was for. Whether it really went is
                # not assumed from either answer -- it is asked below.
                pass
        # Removed is a claim about the world, and the caller writes it into the audit
        # log. A kill the daemon refused, or a removal already under way and not
        # finished, is not one: it is a container the next observation will find again.
        return self.status(identity) == "absent"


@dataclass(frozen=True)
class Sandbox:
    """Where a run's session executes, as configuration and as a travelling document.

    Hearth's worker is detached with a search path and nothing else in its
    environment, so this is read from the environment once, in the control plane, and
    published into the run's own request. The worker rebuilds it with `of`, which
    validates every field again before anything is launched -- a request document is
    Hearth's own writing, but it is read after a crash, a restore and an upgrade.
    """

    launcher: str = PROCESS
    image: str | None = None
    network: str | None = None
    docker: str = "docker"
    # The daemon the client talks to, when it is not the one the client finds on its
    # own: a rootless installation's own socket, or a remote host. It is configuration
    # rather than an inherited `DOCKER_HOST`, because the detached worker is launched
    # with a search path and nothing else in its environment.
    host: str | None = None

    def __post_init__(self):
        if self.launcher not in KINDS or not CLIENT.match(self.docker):
            raise Refused("sandbox_configuration_invalid")
        if self.host is not None and (not isinstance(self.host, str) or not HOST.match(self.host)):
            raise Refused("sandbox_configuration_invalid")
        if self.launcher == PROCESS:
            if self.image is not None or self.network is not None:
                raise Refused("sandbox_configuration_invalid")
            return
        if (
            not isinstance(self.image, str)
            or not IMAGE.match(self.image)
            or not isinstance(self.network, str)
            or not NETWORK.match(self.network)
        ):
            raise Refused("sandbox_configuration_invalid")

    @property
    def digest(self) -> str | None:
        """The bytes the image reference names: what is pinned, compared and reported.

        The repository part of a reference can be moved, mirrored or renamed without
        changing the image at all, so the digest alone is the identity Hearth keeps.
        """
        match = IMAGE.match(self.image) if self.image is not None else None
        return match.group(1) if match is not None else None

    def document(self) -> dict:
        return {
            "launcher": self.launcher,
            "image": self.image,
            "network": self.network,
            "docker": self.docker,
            "host": self.host,
        }

    @classmethod
    def of(cls, value) -> Sandbox:
        """The sandbox a request document names. Absent is today's process launcher.

        A run admitted before this seam existed carries no sandbox at all, and it ran
        as a child of its worker; reading that as anything else would relaunch it
        somewhere it was never admitted to run.
        """
        if value is None:
            return cls()
        if not isinstance(value, dict) or set(value) != {
            "launcher",
            "image",
            "network",
            "docker",
            "host",
        }:
            raise Refused("sandbox_configuration_invalid")
        if not isinstance(value["docker"], str):
            raise Refused("sandbox_configuration_invalid")
        return cls(
            launcher=value["launcher"] if isinstance(value["launcher"], str) else "",
            image=value["image"],
            network=value["network"],
            docker=value["docker"],
            host=value["host"],
        )

    @classmethod
    def from_environment(cls, environment: dict[str, str] | None = None) -> Sandbox:
        values = os.environ if environment is None else environment
        launcher = values.get("HEARTH_SANDBOX") or PROCESS
        client = values.get("HEARTH_SANDBOX_DOCKER") or "docker"
        host = values.get("HEARTH_SANDBOX_DOCKER_HOST") or None
        if launcher == PROCESS:
            return cls(docker=client, host=host)
        return cls(
            launcher=launcher,
            image=values.get("HEARTH_SANDBOX_IMAGE"),
            network=values.get("HEARTH_SANDBOX_NETWORK"),
            docker=client,
            host=host,
        )

    def placement(self) -> Placement:
        """Where this run's files will be, as its own session names them."""
        return Placement(self.launcher)

    def open(self) -> Launcher:
        """The launcher this configuration names, ready to start one session."""
        if self.launcher == PROCESS:
            return ProcessLauncher()
        assert self.image is not None and self.network is not None
        return ContainerLauncher(self.image, self.network, docker=self.docker, host=self.host)


def configure(database, sandbox: Sandbox, clock=time.time) -> dict:
    """Check the sandbox this instance is configured for, and pin it to the store.

    Nothing here is optional and nothing here is deferred to the first run: an image
    the daemon does not hold, a network the operator never created or a daemon that
    does not answer are all configuration to fix before residents are admitted, and
    they are refused by name at start rather than discovered one failed run at a time.

    The image is pinned by digest in `system_meta`, beside the provider binary pins,
    and a start on a different digest refuses. The CLIs *inside* the image are hashed
    and must equal the pins the store already holds, because a sandbox carrying a
    different Codex or Claude than the one this household measured is a different
    provider, whatever its image says.
    """
    if sandbox.launcher == PROCESS:
        return {"launcher": PROCESS}
    launcher = sandbox.open()
    assert isinstance(launcher, ContainerLauncher)
    digest = sandbox.digest
    assert digest is not None
    launcher.client("version", "--format", "{{.Server.Version}}")
    try:
        launcher.client("image", "inspect", "--format", "{{.Id}}", sandbox.image or "")
    except Refused:
        raise Refused("sandbox_image_unavailable") from None
    try:
        launcher.client("network", "inspect", "--format", "{{.Name}}", sandbox.network or "")
    except Refused:
        raise Refused("sandbox_network_missing") from None
    # What the store already holds is read first, and every question that has to be
    # asked of the container runtime is asked with no transaction open: hashing the
    # image's own files starts containers, and a write lock is not held across that.
    with database.transaction() as db:
        previous = db.execute("SELECT value FROM system_meta WHERE key=?", (IMAGE_PIN,)).fetchone()
        pins = {
            key: value
            for key, value in db.execute("SELECT key, value FROM system_meta").fetchall()
            if key in BINARIES
        }
    if previous is not None and previous[0] != digest:
        raise Refused("sandbox_image_changed")
    for key, pin in sorted(pins.items()):
        if hashed(launcher, BINARIES[key]) != pin:
            raise Refused("sandbox_binary_mismatch")
    if previous is None:
        from hearth.work.service import _audit

        with database.transaction(write=True) as db:
            # Read again under the write: another start may have pinned this store in
            # between, and two starts must not disagree about which image it is.
            current = db.execute(
                "SELECT value FROM system_meta WHERE key=?", (IMAGE_PIN,)
            ).fetchone()
            if current is not None:
                if current[0] != digest:
                    raise Refused("sandbox_image_changed")
            else:
                db.execute("INSERT INTO system_meta VALUES (?,?)", (IMAGE_PIN, digest))
                _audit(
                    db,
                    "sandbox.configured",
                    CONTAINER,
                    int(clock()),
                    {"image": digest, "network": sandbox.network, "binaries": sorted(pins)},
                )
    return {"launcher": CONTAINER, "image": digest}


def hashed(launcher: ContainerLauncher, path: str) -> str:
    """The sha256 of one file inside the sandbox image, read by the image itself.

    `sha256sum` is the image's own, run with no network and nothing mounted, so what
    is hashed is the file the session would execute and not a copy of it on the host.

    A failure to hash is not automatically a mismatch, because the two would send an
    operator to opposite places. The client's own exit code separates them: 125 is
    the client or the daemon refusing to run the container at all, as is a timeout or
    a client that cannot be executed, and those are `sandbox_runtime_unavailable`.
    Anything else -- `sha256sum` reporting no such file, an entrypoint that cannot be
    executed, an answer that is not a digest -- is the image not carrying the CLI this
    store is pinned to, which is what `sandbox_binary_mismatch` means.
    """
    try:
        result = launcher.attempt(
            "run",
            "--rm",
            "--network",
            "none",
            "--read-only",
            "--entrypoint",
            "sha256sum",
            launcher.image,
            path,
            timeout=CLIENT_TIMEOUT,
        )
    except OSError, subprocess.SubprocessError:
        raise Refused("sandbox_runtime_unavailable") from None
    if result.returncode == 125:
        raise Refused("sandbox_runtime_unavailable")
    value = result.stdout.strip().split(" ")[0] if result.stdout else ""
    if result.returncode or not re.fullmatch("[0-9a-f]{64}", value):
        raise Refused("sandbox_binary_mismatch")
    return value
