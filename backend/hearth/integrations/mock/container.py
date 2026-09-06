"""Durable, offline Reader container rehearsal; no model or application activation."""

import fcntl
import hashlib
import json
import os
import re
import stat
import subprocess
import tempfile
import uuid
from contextlib import AbstractContextManager, contextmanager, nullcontext
from dataclasses import dataclass
from pathlib import Path

from hearth.execution.staging import stage_run
from hearth.integrations.codex.events import MAX_STREAM, CodexEvents, Transcript
from hearth.integrations.mock.process import write_json
from hearth.residents.models import Refused, identifier
from hearth.storage.artifacts import sync_directory
from hearth.storage.database import Database

IMAGE = "python@sha256:cad9a2c871761c413caa6fdd6441c783451e740a48aaeba60ae62a8b53525ef6"
LABEL = "org.hearth.reader-rehearsal"
FIXTURE = """import hashlib,json,os,signal,sys,time
from pathlib import Path
raw=Path('/input/context.json').read_bytes()
assert hashlib.sha256(raw).hexdigest()==sys.argv[1]
context=json.loads(raw)
assert context['simulated'] is True
assert os.getuid()!=0
status=Path('/proc/self/status').read_text()
assert 'Seccomp:\\t2' in status and 'NoNewPrivs:\\t1' in status
if sys.argv[2]=='hold':
    signal.signal(signal.SIGTERM,signal.SIG_IGN)
    time.sleep(60)
else:
    events=[{'type':'thread.started','thread_id':context['run_id']},
            {'type':'turn.started'},
            {'type':'item.completed','item':{'id':'answer','type':'agent_message',
             'text':'# Synthetic container summary\\n'+ '\\n'.join(context['notes'])}},
            {'type':'turn.completed'}]
    for event in events: print(json.dumps(event),flush=True)
"""


def read_document(path: Path, *, limit: int = 8192) -> dict:
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as file:
        info = os.fstat(file.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise Refused("container_claim_invalid")
        raw = file.read(limit + 1)
    if len(raw) > limit:
        raise Refused("container_claim_invalid")
    try:
        return json.loads(raw)
    except ValueError, RecursionError:
        raise Refused("container_claim_invalid") from None


def publish_receipt(path: Path, document: dict) -> None:
    with container_lock(path.parent / ".terminal.lock"):
        _publish_receipt(path, document)


@contextmanager
def container_lock(path: Path):
    flags = os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK
    try:
        fd = os.open(path, flags | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        fd = os.open(path, flags)
    with os.fdopen(fd, "rb") as lock:
        info = os.fstat(lock.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise Refused("container_lock_invalid")
        fcntl.flock(lock, fcntl.LOCK_EX)
        yield


def _publish_receipt(path: Path, document: dict) -> None:
    data = json.dumps(document, sort_keys=True, ensure_ascii=False).encode()
    if len(data) > MAX_STREAM:
        raise Refused("container_receipt_too_large")
    fd, temporary = tempfile.mkstemp(prefix=".terminal-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as file:
            file.write(data)
            file.flush()
            os.fsync(file.fileno())
        try:
            os.link(temporary, path)
        except FileExistsError:
            if read_document(path, limit=MAX_STREAM) != document:
                marker = path.parent / "terminal.conflict"
                try:
                    conflict = os.open(marker, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                except FileExistsError:
                    pass
                else:
                    with os.fdopen(conflict, "wb") as marker_file:
                        os.fsync(marker_file.fileno())
                sync_directory(path.parent)
                raise Refused("container_receipt_conflict") from None
        Path(temporary).unlink()
        sync_directory(path.parent)
    finally:
        Path(temporary).unlink(missing_ok=True)


class LocalDocker:
    """Only the selected local Mac socket. Never pull or forward host credentials."""

    def __call__(self, *args: str) -> str:
        result = subprocess.run(
            ["docker", "--host", "unix://" + str(Path.home() / ".docker/run/docker.sock"), *args],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
        if result.returncode:
            raise OSError(f"Docker {args[0]} failed")
        if len(result.stdout.encode()) > MAX_STREAM:
            raise OSError("Oversized Docker evidence")
        return result.stdout.strip()


@dataclass(frozen=True)
class Observation:
    status: str
    transcript: Transcript | None = None


def decode_receipt(claim: dict, identity: dict, receipt: dict) -> Observation:
    """Validate terminal evidence without a daemon or the original absolute root."""
    binding = hashlib.sha256(json.dumps(claim, sort_keys=True).encode()).hexdigest()
    if (
        set(receipt)
        != {"version", "binding", "container_id", "exit_code", "events", "events_sha256"}
        or type(receipt["version"]) is not int
        or receipt["version"] != 1
        or receipt["binding"] != binding
        or identity != {"binding": binding, "id": receipt["container_id"]}
        or type(receipt["exit_code"]) is not int
        or not 0 <= receipt["exit_code"] <= 255
        or not isinstance(receipt["events"], str)
        or hashlib.sha256(receipt["events"].encode()).hexdigest() != receipt["events_sha256"]
    ):
        raise Refused("container_receipt_invalid")
    parser = CodexEvents()
    parser.feed(receipt["events"].encode())
    transcript = parser.finish(exit_code=receipt["exit_code"])
    if transcript.thread_id is not None and transcript.thread_id != claim["run_id"]:
        raise Refused("container_receipt_invalid")
    return Observation("exited", transcript)


class ContainerRehearsal:
    """One immutable claim; replay observes and never repeats create or start.

    Standalone probes use a dedicated synthetic database/root. The operational
    process worker supplies its dispatch guard. Root/ancestors are trusted and
    must never be mounted into a container; only its selected staged run is exposed.
    """

    def __init__(self, database: Database, root: Path, *, docker=None):
        self.database = database
        self.root = root.resolve()
        self.root.mkdir(mode=0o700, exist_ok=True)
        if self.root.stat().st_mode & 0o077:
            raise Refused("container_root_unsafe")
        sync_directory(self.root.parent)
        self.docker = docker or LocalDocker()

    def _folder(self, run_id):
        identifier(run_id)
        return self.root / run_id

    def _claim(self, run_id):
        folder = self._folder(run_id)
        path = folder / "claim.json"
        try:
            if folder.is_symlink():
                raise ValueError("linked claim directory")
            claim = read_document(path)
            if claim["run_id"] != run_id or claim["image"] != IMAGE or claim["version"] != 1:
                raise ValueError("claim mismatch")
            if claim["fixture_digest"] != hashlib.sha256(FIXTURE.encode()).hexdigest():
                raise ValueError("fixture changed")
            if claim["scenario"] not in {"success", "hold"}:
                raise ValueError("scenario changed")
            if claim["input"] != str(self.root / "inputs" / run_id):
                raise ValueError("foreign input root")
            if not re.fullmatch(r"hearth-reader-[0-9a-f]{32}", claim["name"]):
                raise ValueError("invalid container name")
            if not re.fullmatch(r"[0-9a-f]{64}", claim["digest"]):
                raise ValueError("invalid input digest")
            return claim
        except OSError, ValueError, TypeError, KeyError:
            raise Refused("container_claim_invalid") from None

    def _inspect(self, claim):
        objects = json.loads(self.docker("container", "inspect", claim["name"]))
        value = objects[0]
        binding = hashlib.sha256(json.dumps(claim, sort_keys=True).encode()).hexdigest()
        if value["Config"].get("Labels", {}).get(LABEL) != binding:
            raise Refused("container_ownership_mismatch")
        if value["Name"] != "/" + claim["name"]:
            raise Refused("container_ownership_mismatch")
        identity = self._folder(claim["run_id"]) / "identity.json"
        if identity.exists() or identity.is_symlink():
            if identity.is_symlink():
                raise Refused("container_ownership_mismatch")
            pinned = read_document(identity)
            if pinned != {"id": value["Id"], "binding": binding}:
                raise Refused("container_ownership_mismatch")
        elif value["State"]["Status"] != "created":
            raise Refused("container_ownership_mismatch")
        return value

    def start(
        self,
        run_id: str,
        *,
        scenario: str = "success",
        dispatch_guard: AbstractContextManager | None = None,
    ) -> Observation:
        if scenario not in {"success", "hold"} or os.getuid() == 0:
            raise Refused("container_configuration_invalid")
        folder = self._folder(run_id)
        with container_lock(self.root / ".launch.lock"):
            staged = stage_run(self.database, run_id, self.root / "inputs")
            digest = hashlib.sha256(staged.read_bytes()).hexdigest()
            if folder.exists() or folder.is_symlink():
                claim = self._claim(run_id)
                if (claim["digest"], claim["scenario"], claim["input"], claim["user"]) != (
                    digest,
                    scenario,
                    str(staged.parent),
                    f"{os.getuid()}:{os.getgid()}",
                ):
                    raise Refused("container_identity_conflict")
                return self.inspect(run_id)
            folder.mkdir(mode=0o700)
            sync_directory(self.root)
            claim = {
                "version": 1,
                "run_id": run_id,
                "image": IMAGE,
                "digest": digest,
                "fixture_digest": hashlib.sha256(FIXTURE.encode()).hexdigest(),
                "input": str(staged.parent),
                "user": f"{os.getuid()}:{os.getgid()}",
                "scenario": scenario,
                "name": "hearth-reader-" + uuid.uuid4().hex,
            }
            write_json(folder / "claim.json", claim)
            binding = hashlib.sha256(json.dumps(claim, sort_keys=True).encode()).hexdigest()
            # Durable claim precedes dispatch. Any lost reply remains an inspect-only claim.
            self.docker(
                "create",
                "--name",
                claim["name"],
                "--label",
                f"{LABEL}={binding}",
                "--pull",
                "never",
                "--restart",
                "no",
                "--network",
                "none",
                "--read-only",
                "--user",
                claim["user"],
                "--init",
                "--cap-drop",
                "ALL",
                "--security-opt",
                "no-new-privileges=true",
                "--security-opt",
                "seccomp=builtin",
                "--pids-limit",
                "32",
                "--memory",
                "64m",
                "--memory-swap",
                "64m",
                "--cpus",
                "0.5",
                "--shm-size",
                "1m",
                "--log-driver",
                "json-file",
                "--log-opt",
                "max-size=1m",
                "--log-opt",
                "max-file=1",
                "--tmpfs",
                "/scratch:rw,noexec,nosuid,nodev,size=1m,mode=1777",
                "--mount",
                f"type=bind,source={claim['input']},target=/input,readonly",
                "--workdir",
                "/scratch",
                "--entrypoint",
                "python3",
                IMAGE,
                "-I",
                "-c",
                FIXTURE,
                digest,
                scenario,
            )
            owned = self._inspect(claim)
            config, policy = owned["Config"], owned["HostConfig"]
            mounts = owned["Mounts"]
            if (
                config["Image"] != IMAGE
                or config["User"] != claim["user"]
                or config["Entrypoint"] != ["python3"]
                or config["Cmd"] != ["-I", "-c", FIXTURE, digest, scenario]
                or policy["NetworkMode"] != "none"
                or policy["ReadonlyRootfs"] is not True
                or policy["RestartPolicy"] != {"Name": "no", "MaximumRetryCount": 0}
                or policy["Privileged"] is not False
                or policy["PidMode"] != ""
                or policy["CapDrop"] != ["ALL"]
                or policy["CapAdd"]
                or set(policy["SecurityOpt"]) != {"no-new-privileges=true", "seccomp=builtin"}
                or len(mounts) != 1
                or mounts[0]["Source"] != claim["input"]
                or mounts[0]["Destination"] != "/input"
                or mounts[0]["RW"] is not False
            ):
                raise Refused("container_configuration_mismatch")
            write_json(folder / "identity.json", {"id": owned["Id"], "binding": binding})
            # Operational callers supply a fresh database guard. Enter it after
            # creation so cancellation/policy changes during Docker setup win.
            # Existing claims above remain inspect-only, including refused starts.
            with dispatch_guard if dispatch_guard is not None else nullcontext():
                self.docker("start", owned["Id"])
        return self.inspect(run_id)

    def _receipt(self, claim) -> Observation | None:
        folder = self._folder(claim["run_id"])
        path = folder / "terminal.json"
        marker = folder / "terminal.conflict"
        if marker.exists() or marker.is_symlink():
            raise Refused("container_receipt_conflict")
        if not path.exists() and not path.is_symlink():
            return None
        receipt = read_document(path, limit=MAX_STREAM)
        identity = read_document(folder / "identity.json")
        observation = decode_receipt(claim, identity, receipt)
        sync_directory(folder)
        return observation

    def inspect(self, run_id: str) -> Observation:
        claim = self._claim(run_id)
        try:
            with container_lock(self._folder(run_id) / ".terminal.lock"):
                return self._observe(claim)
        except OSError, ValueError, KeyError, TypeError, IndexError, subprocess.TimeoutExpired:
            return Observation("unknown")

    def _observe(self, claim) -> Observation:
        run_id = claim["run_id"]
        try:
            cached = self._receipt(claim)
            if cached is not None:
                return cached
            value = self._inspect(claim)
            state = value["State"]
            if state["Running"] is True:
                return Observation("running")
            if (
                state["Running"] is not False
                or state["Status"] != "exited"
                or type(state["Pid"]) is not int
                or state["Pid"] != 0
                or type(state["ExitCode"]) is not int
                or not 0 <= state["ExitCode"] <= 255
            ):
                return Observation("unknown")
            raw = self.docker("logs", value["Id"])
            document = {
                "version": 1,
                "binding": hashlib.sha256(json.dumps(claim, sort_keys=True).encode()).hexdigest(),
                "container_id": value["Id"],
                "exit_code": state["ExitCode"],
                "events": raw,
                "events_sha256": hashlib.sha256(raw.encode()).hexdigest(),
            }
            _publish_receipt(self._folder(run_id) / "terminal.json", document)
            return self._receipt(claim) or Observation("unknown")
        except OSError, ValueError, KeyError, TypeError, IndexError, subprocess.TimeoutExpired:
            return Observation("unknown")

    def stop(self, run_id: str) -> Observation:
        with container_lock(self.root / ".launch.lock"):
            claim = self._claim(run_id)
            value = self._inspect(claim)
            if value["State"]["Running"]:
                self.docker("stop", "--time", "1", value["Id"])
            return self.inspect(run_id)

    def remove(self, run_id: str) -> None:
        """Explicit rehearsal cleanup; retaining the claim prevents a later relaunch."""
        with container_lock(self.root / ".launch.lock"):
            self._remove(run_id)

    def _remove(self, run_id: str) -> None:
        value = self._inspect(self._claim(run_id))
        if (
            value["State"]["Running"] is not False
            or type(value["State"]["Pid"]) is not int
            or value["State"]["Pid"] != 0
            or value["State"]["Status"] not in {"created", "exited"}
        ):
            raise Refused("container_termination_unproven")
        if value["State"]["Status"] == "exited":
            if (
                type(value["State"]["ExitCode"]) is not int
                or not 0 <= value["State"]["ExitCode"] <= 255
            ):
                raise Refused("container_termination_unproven")
            if self.inspect(run_id).status != "exited":
                raise Refused("container_receipt_unproven")
            with container_lock(self._folder(run_id) / ".terminal.lock"):
                self._receipt(self._claim(run_id))
                receipt = read_document(self._folder(run_id) / "terminal.json", limit=MAX_STREAM)
                if (
                    receipt["container_id"] != value["Id"]
                    or receipt["exit_code"] != value["State"]["ExitCode"]
                ):
                    raise Refused("container_receipt_conflict")
                self.docker("rm", value["Id"])
        else:
            self.docker("rm", value["Id"])
