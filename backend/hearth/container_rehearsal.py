"""Durable, offline Reader container rehearsal; no model or application activation."""

import fcntl
import hashlib
import json
import os
import re
import stat
import subprocess
import uuid
from dataclasses import dataclass
from pathlib import Path

from hearth.artifacts import sync_directory
from hearth.codex_events import MAX_STREAM, CodexEvents, Transcript
from hearth.database import Database
from hearth.models import Refused, identifier
from hearth.process_mock import write_json
from hearth.staged_input import stage_run

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


def read_document(path: Path) -> dict:
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as file:
        info = os.fstat(file.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise Refused("container_claim_invalid")
        raw = file.read(8193)
    if len(raw) > 8192:
        raise Refused("container_claim_invalid")
    return json.loads(raw)


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


class ContainerRehearsal:
    """One immutable claim; replay observes and never repeats create or start.

    Use a dedicated synthetic database/root with no application executor. This is
    not a Runtime adapter or launch-authority check. Root/ancestors are trusted and
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

    def start(self, run_id: str, *, scenario: str = "success") -> Observation:
        if scenario not in {"success", "hold"} or os.getuid() == 0:
            raise Refused("container_configuration_invalid")
        folder = self._folder(run_id)
        flags = os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK
        try:
            fd = os.open(self.root / ".launch.lock", flags | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            fd = os.open(self.root / ".launch.lock", flags)
        with os.fdopen(fd, "rb") as lock:
            if not stat.S_ISREG(os.fstat(lock.fileno()).st_mode):
                raise Refused("container_root_unsafe")
            fcntl.flock(lock, fcntl.LOCK_EX)
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
            self.docker("start", owned["Id"])
        return self.inspect(run_id)

    def inspect(self, run_id: str) -> Observation:
        claim = self._claim(run_id)
        try:
            value = self._inspect(claim)
            state = value["State"]
            if state["Running"]:
                return Observation("running")
            if state["Status"] != "exited" or state["Pid"] != 0:
                return Observation("unknown")
            raw = self.docker("logs", value["Id"]).encode()
            parser = CodexEvents()
            parser.feed(raw)
            transcript = parser.finish(exit_code=state["ExitCode"])
            if transcript.thread_id is not None and transcript.thread_id != run_id:
                return Observation("unknown")
            return Observation("exited", transcript)
        except OSError, ValueError, KeyError, TypeError, IndexError, subprocess.TimeoutExpired:
            return Observation("unknown")

    def stop(self, run_id: str) -> Observation:
        claim = self._claim(run_id)
        value = self._inspect(claim)
        if value["State"]["Running"]:
            self.docker("stop", "--time", "1", value["Id"])
        return self.inspect(run_id)

    def remove(self, run_id: str) -> None:
        """Explicit rehearsal cleanup; retaining the claim prevents a later relaunch."""
        value = self._inspect(self._claim(run_id))
        if value["State"]["Running"] or value["State"]["Status"] not in {"created", "exited"}:
            raise Refused("container_termination_unproven")
        self.docker("rm", value["Id"])
