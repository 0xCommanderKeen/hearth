"""Opt-in POSIX process lifecycle rehearsal. Every child and receipt is simulated."""

import fcntl
import hashlib
import json
import os
import selectors
import signal
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import asdict
from pathlib import Path

from hearth.artifacts import MAX_ARTIFACT, sync_directory
from hearth.models import Refused, identifier
from hearth.runtime import Evidence, MockRuntime


def write_json(path: Path, document: dict) -> None:
    fd, temporary = tempfile.mkstemp(prefix=".write-", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as file:
            json.dump(document, file)
            file.flush()
            os.fsync(file.fileno())
        os.replace(temporary, path)
        sync_directory(path.parent)
    finally:
        Path(temporary).unlink(missing_ok=True)


def read_request(folder: Path) -> dict:
    with (folder / "request.json").open("rb") as file:
        raw = file.read(4097)
    if len(raw) > 4096:
        raise ValueError("oversized request")
    value = json.loads(raw)
    if (
        not isinstance(value, dict)
        or value.get("simulated") is not True
        or value.get("boundary") not in {"posix", "container"}
        or value.get("scenario") not in {"success", "hold", "failure", "unknown_usage"}
        or type(value.get("timeout")) not in {int, float}
        or not 0 < value["timeout"] <= 60
        or not isinstance(value.get("instruction_digest"), str)
        or len(value["instruction_digest"]) != 64
    ):
        raise ValueError("invalid request")
    if value["boundary"] == "container" and (
        value["scenario"] not in {"success", "hold"}
        or not isinstance(value.get("authority"), dict)
        or set(value["authority"]) != {"epoch", "owner_token"}
        or any(not isinstance(v, str) or not v for v in value["authority"].values())
    ):
        raise ValueError("invalid container authority")
    return value


class ProcessMockRuntime:
    """A claim survives worker loss; observation never uses a stored PID.

    Only the trusted worker signals its own unreaped child process group. This
    proves local lifecycle behavior, not confinement or provider cancellation.
    """

    kind = "process_mock"
    version = 1

    def __init__(
        self,
        root: Path,
        *,
        scenario: str = "success",
        timeout: float = 30,
        boundary: str = "posix",
    ):
        if scenario not in {"success", "hold", "failure", "unknown_usage"}:
            raise ValueError("Unknown mock scenario")
        if not 0 < timeout <= 60:
            raise ValueError("Mock timeout must be in (0, 60]")
        if boundary not in {"posix", "container"}:
            raise ValueError("Unknown process boundary")
        if boundary == "container" and scenario not in {"success", "hold"}:
            raise ValueError("Container fixtures support success and hold only")
        self.boundary = boundary
        self.root = root.resolve()
        self.scenario = scenario
        self.timeout = timeout
        self.root.mkdir(parents=True, exist_ok=True)

    def folder(self, run_id: str) -> Path:
        identifier(run_id)
        return self.root / run_id

    def start(self, run_id: str, instruction: str) -> None:
        folder = self.folder(run_id)
        digest = hashlib.sha256(instruction.encode()).hexdigest()
        with (self.root / ".launch.lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            if folder.exists():
                try:
                    previous = read_request(folder)
                except OSError, ValueError:
                    raise Refused("runtime_evidence_corrupt") from None
                if (
                    previous["instruction_digest"] != digest
                    or previous["boundary"] != self.boundary
                ):
                    raise Refused("runtime_identity_conflict")
                return
            authority = {}
            if self.boundary == "container":
                from hearth.database import Database

                if self.root.name != "process-mock":
                    raise Refused("container_process_root_invalid")
                database = Database(self.root.parent / "hearth.db")
                if database.restored() or database.process_boundary() != "container":
                    raise Refused("runtime_store_mismatch")
                with database.transaction() as db:
                    row = db.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
                    if row is None or row["input_digest"] != digest:
                        raise Refused("runtime_identity_conflict")
                    authority = {
                        "epoch": db.execute(
                            "SELECT value FROM system_meta WHERE key='epoch'"
                        ).fetchone()[0],
                        "owner_token": row["owner_token"],
                    }
            folder.mkdir()
            sync_directory(self.root)
            write_json(
                folder / "request.json",
                {
                    "simulated": True,
                    "instruction_digest": digest,
                    "boundary": self.boundary,
                    "authority": authority,
                    "scenario": self.scenario,
                    "timeout": self.timeout,
                },
            )
            # No retry after this durable claim, even when spawning raises or the
            # parent dies before learning whether the worker actually started.
            worker = subprocess.Popen(
                [sys.executable, "-I", "-m", "hearth.process_mock", "worker", str(folder)],
                cwd=folder,
                env={"PATH": os.defpath + ":/usr/local/bin", "LANG": "C.UTF-8"},
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
            threading.Thread(target=worker.wait, daemon=True).start()

    def inspect(self, run_id: str, *, expected_digest: str | None = None) -> Evidence:
        folder = self.folder(run_id)
        if not folder.exists():
            return Evidence("absent")
        try:
            request = read_request(folder)
            if expected_digest is not None and request["instruction_digest"] != expected_digest:
                return Evidence("unknown")
            result = MockRuntime(folder).inspect("result")
            if result.status in {"succeeded", "failed", "cancelled"}:
                with (folder / "result.json").open("rb") as file:
                    document = json.loads(file.read(MAX_ARTIFACT + 1))
                if document.get("instruction_digest") != request["instruction_digest"]:
                    return Evidence("unknown")
                return result
            if (folder / "result.json").exists():
                return Evidence("unknown")
            with (folder / "worker.lock").open("a") as lock:
                try:
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    return Evidence("running")
                if request["boundary"] == "container":
                    from hearth.container_worker import reconcile

                    return reconcile(folder, request)
            return Evidence("unknown")
        except OSError, ValueError, TypeError, Refused, subprocess.TimeoutExpired:
            return Evidence("unknown")

    def stop(self, run_id: str) -> None:
        folder = self.folder(run_id)
        if folder.exists():
            write_json(folder / "cancel.json", {"cancel": True})


def run_child(folder: Path, request: dict) -> Evidence:
    child = subprocess.Popen(
        [sys.executable, "-I", "-m", "hearth.process_mock", "child", str(folder)],
        cwd=folder,
        env={"PATH": os.defpath, "LANG": "C.UTF-8"},
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    assert child.stdout is not None
    output = bytearray()
    cancelled = False
    complete = False
    deadline = time.monotonic() + request["timeout"]
    try:
        with selectors.DefaultSelector() as selector:
            selector.register(child.stdout, selectors.EVENT_READ)
            while time.monotonic() < deadline:
                if (folder / "cancel.json").exists():
                    cancelled = True
                    break
                if not selector.select(timeout=0.02):
                    continue
                chunk = os.read(child.stdout.fileno(), 8192)
                if not chunk:
                    break
                output.extend(chunk)
                if len(output) > MAX_ARTIFACT:
                    break
                if output.endswith(b"\n"):
                    complete = True
                    break
    finally:
        # Do not poll/wait/reap before signalling: the unreaped group leader
        # reserves its PID, so a recycled stored PID can never be targeted here.
        try:
            os.killpg(child.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        child.wait(timeout=5)
        child.stdout.close()
    if cancelled:
        return Evidence("cancelled", cost=1_000)
    if not complete or child.returncode != -signal.SIGKILL:
        return Evidence("failed", cost=None)
    try:
        value = json.loads(output)
        # The same bounded validator as the in-process mock owns the fixture format.
        write_json(folder / "child-result.json", value)
        result = MockRuntime(folder).inspect("child-result")
        if result.status in {"succeeded", "failed"}:
            return result
    except OSError, ValueError, TypeError:
        pass
    return Evidence("failed", cost=None)


def worker(folder: Path) -> None:
    with (folder / "worker.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return
        # A second worker, even after the first dies, cannot spawn another child.
        try:
            with (folder / "started").open("x") as started:
                started.flush()
                os.fsync(started.fileno())
            sync_directory(folder)
        except FileExistsError:
            return
        request = read_request(folder)
        if request["boundary"] == "container" and not (folder / "cancel.json").exists():
            from hearth.container_worker import run_container

            # Unknown dispatch leaves the exclusive started claim in place. A
            # reopened adapter reconciles it; no second worker may start again.
            try:
                run_container(folder, request)
            except OSError, Refused, subprocess.TimeoutExpired:
                pass
            return
        result = (
            Evidence("cancelled", cost=0)
            if (folder / "cancel.json").exists()
            else run_child(folder, request)
        )
        write_json(
            folder / "result.json",
            {
                "simulated": True,
                "instruction_digest": request["instruction_digest"],
                "evidence": asdict(result),
            },
        )


def fake_child(folder: Path) -> None:
    request = read_request(folder)
    with (folder / "child-started").open("x"):
        pass
    if request["scenario"] == "hold":
        # A descendant exercises group cancellation; it has its own bounded life
        # even if the trusted worker is deliberately killed during a rehearsal.
        subprocess.Popen(
            [sys.executable, "-I", "-m", "hearth.process_mock", "heartbeat", str(folder)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        time.sleep(request["timeout"] + 0.2)
        return
    fixture = MockRuntime(folder / "fixture", scenario=request["scenario"])
    fixture.start("output", "synthetic process fixture")
    print(
        json.dumps({"simulated": True, "evidence": asdict(fixture.inspect("output"))}),
        flush=True,
    )
    # Keep the process-group identity alive until the owner acknowledges the
    # terminal fixture record by cleaning up the group. EOF is not completion.
    time.sleep(request["timeout"] + 0.2)


if __name__ == "__main__":
    mode, location = sys.argv[1:]
    folder = Path(location)
    if mode == "worker":
        worker(folder)
    elif mode == "child":
        fake_child(folder)
    elif mode == "heartbeat":
        deadline = time.monotonic() + read_request(folder)["timeout"]
        while time.monotonic() < deadline:
            (folder / "heartbeat").write_text(str(time.monotonic_ns()))
            time.sleep(0.02)
    else:
        raise SystemExit("Unknown mock process role")
