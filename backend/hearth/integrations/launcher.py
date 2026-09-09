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
from dataclasses import dataclass
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
VARIABLE = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,127}\Z")

# The working directory a sandboxed session runs in: a fresh tmpfs, never a host path.
WORKSPACE = "/workspace"
# Every container Hearth starts carries this label, so what it left behind is always
# distinguishable from what somebody else is running on the same host.
LABEL = "org.hearth.sandbox"
# Where the sandbox image keeps each provider's pinned CLI. The image is built to put
# them here (`deploy/Dockerfile.sandbox`), and these are the files whose sha256 has to
# equal the binary pin the store already holds.
BINARIES = {
    "codex_live_binary": "/usr/local/bin/codex",
    "claude_live_binary": "/usr/local/bin/claude",
}
# One question to the container runtime -- a version, an inspect, a hash -- may take
# this long. A daemon that cannot answer in half a minute is unavailable.
CLIENT_TIMEOUT = 30
# How long the client is given to write the id of the container it created before the
# run is treated as one whose identity was never observed.
IDENTITY_TIMEOUT = 30


@dataclass(frozen=True)
class Mount:
    """One host path a run may reach, at one path inside the sandbox.

    Read-only unless `writable` is said. Nothing here decides *whether* a path may be
    mounted -- that is the grant's business (epic #183, slice #187) -- only that what
    reaches the container runtime is a shape a command line cannot be smuggled through.
    """

    source: str
    target: str
    writable: bool = False

    def __post_init__(self):
        for path in (self.source, self.target):
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
        value = f"type=bind,source={self.source},target={self.target}"
        return value if self.writable else value + ",readonly"


@dataclass
class Handle:
    """One started session: which launcher started it, its identity, and its stream.

    `id` is the worker's own process group on `process` and the container id on
    `container` -- the thing a later observation asks about. It is `None` only when
    the container runtime never told Hearth what it created, which is an execution
    nobody can name and never evidence that a retry is safe.
    """

    launcher: str
    id: str | None
    process: subprocess.Popen

    @property
    def stdout(self) -> IO[bytes] | None:
        return self.process.stdout

    @property
    def returncode(self) -> int | None:
        return self.process.returncode

    def document(self) -> dict:
        return {"launcher": self.launcher, "id": self.id}


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

    def inspect(self, handle: Handle) -> str: ...

    def stop(self, handle: Handle, signal: int) -> None: ...

    def wait(self, handle: Handle, timeout: float | None) -> int | None: ...


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

    def __init__(self, image: str, network: str, *, docker: str = "docker"):
        self.image = image
        self.network = network
        self.docker = docker

    def client(self, *arguments: str, timeout: float = CLIENT_TIMEOUT) -> str:
        """One question to the container runtime. No answer at all is unavailable."""
        try:
            result = subprocess.run(
                [self.docker, *arguments],
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
                # The client is given a search path and nothing else, exactly as the
                # worker is. A default socket is the daemon it talks to.
                env={"PATH": os.defpath},
            )
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
        return [
            self.docker,
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
        ]

    def start(self, command, *, env, cwd, stdin, mounts=(), socket=None, bufsize=-1):
        argv = self.arguments(command, env=env, mounts=mounts, socket=socket)
        # The runtime refuses to overwrite a cidfile, so it is written into a private
        # directory of this launch's own and read once. The durable record of what was
        # started is the worker's, beside the receipt; this is a handover, and it is
        # deliberately not the session's working directory, which a provider's own
        # adapter may require to be empty.
        directory = tempfile.mkdtemp(prefix="hearth-sandbox-")
        try:
            identity = Path(directory) / "container-id"
            argv += ["--cidfile", str(identity), self.image, *command]
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
            return Handle(self.kind, self._identify(identity, child), child)
        finally:
            shutil.rmtree(directory, ignore_errors=True)

    @staticmethod
    def _identify(path: Path, child: subprocess.Popen) -> str | None:
        """The id of the container the client created, as soon as it has written it.

        A client that ended without ever naming one leaves `None`: the session's
        identity was never observed, and no later observation may guess at it.
        """
        deadline = time.monotonic() + IDENTITY_TIMEOUT
        while time.monotonic() < deadline:
            try:
                value = path.read_text().strip()
            except OSError:
                value = ""
            if IDENTITY.match(value):
                return value
            if child.poll() is not None:
                break
            time.sleep(0.02)
        return None

    def inspect(self, handle):
        """What the container runtime says about the container, or that it is gone."""
        if handle.id is None:
            return "unknown"
        try:
            status = self.client(
                "inspect", "--type", "container", "--format", "{{.State.Status}}", handle.id
            )
        except Refused:
            # `--rm` means a finished container is removed, and an inspect that finds
            # nothing is the ordinary ending, not a broken daemon.
            return "absent"
        return "running" if status == "running" else "exited"

    def stop(self, handle, signal):
        if handle.id is None:
            return
        try:
            self.client("kill", "--signal", str(int(signal)), handle.id)
        except Refused:
            # A container that has already ended cannot be signalled, and the client
            # this launcher is attached to is what reports the ending anyway.
            pass

    def wait(self, handle, timeout):
        try:
            return handle.process.wait(timeout)
        except subprocess.TimeoutExpired:
            return None


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

    def __post_init__(self):
        if self.launcher not in KINDS or not CLIENT.match(self.docker):
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
        }:
            raise Refused("sandbox_configuration_invalid")
        if not isinstance(value["docker"], str):
            raise Refused("sandbox_configuration_invalid")
        return cls(
            launcher=value["launcher"] if isinstance(value["launcher"], str) else "",
            image=value["image"],
            network=value["network"],
            docker=value["docker"],
        )

    @classmethod
    def from_environment(cls, environment: dict[str, str] | None = None) -> Sandbox:
        values = os.environ if environment is None else environment
        launcher = values.get("HEARTH_SANDBOX") or PROCESS
        if launcher == PROCESS:
            return cls(docker=values.get("HEARTH_SANDBOX_DOCKER") or "docker")
        return cls(
            launcher=launcher,
            image=values.get("HEARTH_SANDBOX_IMAGE"),
            network=values.get("HEARTH_SANDBOX_NETWORK"),
            docker=values.get("HEARTH_SANDBOX_DOCKER") or "docker",
        )

    def open(self) -> Launcher:
        """The launcher this configuration names, ready to start one session."""
        if self.launcher == PROCESS:
            return ProcessLauncher()
        assert self.image is not None and self.network is not None
        return ContainerLauncher(self.image, self.network, docker=self.docker)


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

    An image that cannot produce the hash -- because it does not carry that CLI at
    all -- is the same refusal as one carrying different bytes. The daemon has
    already answered for its version, the image and the network by the time this
    runs, so what fails here is the image's own contents.
    """
    try:
        answer = launcher.client(
            "run",
            "--rm",
            "--network",
            "none",
            "--read-only",
            "--entrypoint",
            "sha256sum",
            launcher.image,
            path,
        )
    except Refused:
        raise Refused("sandbox_binary_mismatch") from None
    value = answer.split(" ")[0] if answer else ""
    if not re.fullmatch("[0-9a-f]{64}", value):
        raise Refused("sandbox_binary_mismatch")
    return value
