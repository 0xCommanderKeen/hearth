"""Consistent mock backups and quarantined restores; no in-place overwrite or activation."""

import fcntl
import hashlib
import json
import os
import re
import shutil
import sqlite3
import stat
import tempfile
import time
import uuid
from contextlib import ExitStack, contextmanager
from importlib.metadata import version
from pathlib import Path

from hearth.artifacts import Artifact, Artifacts, sync_directory
from hearth.database import SCHEMA_VERSION, Database, schema_matches
from hearth.memory import MemoryFiles, memory_path
from hearth.models import Refused, identifier
from hearth.process_mock import read_request
from hearth.runtime import decode_evidence

FORMAT = 1
STORES = {
    "artifacts": ".md",
    "mock-runtime": ".json",
    "mock-inbox": ".md",
    "mock-noticeboard": ".md",
    "memory": ".md",
    "process-mock": "",
}
PROCESS_FILES = {"request.json", "result.json", "started", "cancel.json"}
PROCESS_TRANSIENT = {"worker.lock", "heartbeat", "child-started", "child-result.json", "fixture"}
MAX_FILE = 128 * 1024 * 1024


def _read(path: Path) -> bytes:
    if path.parent.is_symlink():
        raise Refused("backup_file_missing_or_unsafe")
    try:
        if path.parent.parent.name in {"memory", "process-mock"}:
            root = os.open(path.parent.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            try:
                directory = os.open(
                    path.parent.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=root
                )
            finally:
                os.close(root)
        else:
            directory = os.open(path.parent, os.O_RDONLY | os.O_NOFOLLOW)
        try:
            fd = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
        finally:
            os.close(directory)
        with os.fdopen(fd, "rb") as file:
            if not stat.S_ISREG(os.fstat(file.fileno()).st_mode):
                raise Refused("backup_file_missing_or_unsafe")
            data = file.read(MAX_FILE + 1)
    except OSError:
        raise Refused("backup_file_missing_or_unsafe") from None
    if len(data) > MAX_FILE:
        raise Refused("backup_file_too_large")
    return data


def _write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with path.open("xb") as file:
        os.chmod(path, 0o600)
        file.write(data)
        file.flush()
        os.fsync(file.fileno())
    sync_directory(path.parent)


def _allowed(name: str) -> bool:
    if name == "hearth.db":
        return True
    pieces = name.split("/")
    if len(pieces) == 3 and pieces[0] == "process-mock":
        try:
            identifier(pieces[1])
            return pieces[2] in PROCESS_FILES
        except Refused:
            return False
    if pieces[0] == "process-mock":
        return False
    if len(pieces) == 3 and pieces[0] == "memory":
        try:
            return name == "memory/" + memory_path(pieces[1], Path(pieces[2]).stem)
        except Refused:
            return False
    if pieces[0] == "memory":
        return False
    if len(pieces) != 2 or pieces[0] not in STORES:
        return False
    path = Path(pieces[1])
    if path.suffix != STORES[pieces[0]]:
        return False
    try:
        identifier(path.stem)
    except Refused:
        return False
    return True


def _store_files(folder: Path, *, skip_hidden: bool = False):
    """Memory and process evidence have one identity level; no symlink traversal."""
    if folder.is_symlink() or not folder.is_dir():
        raise Refused("backup_source_unsafe")
    for child in folder.iterdir():
        if skip_hidden and child.name.startswith("."):
            continue
        if child.is_symlink():
            raise Refused("backup_source_unsafe")
        if folder.name in {"memory", "process-mock"}:
            identifier(child.name)
            if not child.is_dir():
                raise Refused("backup_path_invalid")
            candidates = child.iterdir()
        else:
            candidates = (child,)
        for path in candidates:
            if skip_hidden and folder.name == "process-mock" and path.name in PROCESS_TRANSIENT:
                continue
            if skip_hidden and path.name.startswith("."):
                continue
            relative = str(path.relative_to(folder.parent))
            if not _allowed(relative):
                raise Refused("backup_path_invalid")
            yield path


@contextmanager
def _destination(destination: Path):
    if destination.exists() or destination.is_symlink():
        raise Refused("backup_destination_exists")
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=".hearth-copy-", dir=destination.parent))
    try:
        yield temporary
        # Persist every newly created directory link, including resident memory folders.
        for directory in sorted(
            (path for path in temporary.rglob("*") if path.is_dir()),
            key=lambda path: len(path.parts),
            reverse=True,
        ):
            sync_directory(directory)
        sync_directory(temporary)
        # Reserve the name without replacing even an empty existing directory.
        destination.mkdir(mode=0o700)
        try:
            os.replace(temporary, destination)
        except BaseException:
            destination.rmdir()
            raise
        sync_directory(destination.parent)
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)


def _check_database(root: Path) -> dict:
    path = root / "hearth.db"
    with sqlite3.connect(path.as_uri() + "?mode=ro", uri=True) as db:
        db.row_factory = sqlite3.Row
        if db.execute("PRAGMA user_version").fetchone()[0] != SCHEMA_VERSION:
            raise Refused("backup_schema_incompatible")
        if db.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise Refused("backup_database_corrupt")
        if db.execute("PRAGMA foreign_key_check").fetchone() is not None:
            raise Refused("backup_references_invalid")
        if not schema_matches(db):
            raise Refused("backup_schema_unexpected")
        selected = db.execute("SELECT value FROM system_meta WHERE key='runtime_kind'").fetchone()
        if selected is None or selected[0] not in {"inline_mock", "process_mock"}:
            raise Refused("backup_runtime_invalid")
        for run in db.execute("SELECT * FROM runs"):
            if (
                run["runtime_kind"] != selected[0]
                or run["runtime_version"] != 1
                or not re.fullmatch(r"[0-9a-f]{64}", run["input_digest"])
            ):
                raise Refused("backup_runtime_invalid")
            if run["runtime_kind"] == "process_mock":
                if run["finished_at"] is None:
                    raise Refused("backup_process_unsettled")
                if not run["launch_attempted"] and (
                    run["status"] != "cancelled"
                    or run["actual_cost"] != 0
                    or not run["usage_known"]
                ):
                    raise Refused("backup_runtime_invalid")
                if run["launch_attempted"]:
                    folder = root / "process-mock" / run["id"]
                    _read(folder / "request.json")
                    try:
                        request = read_request(folder)
                    except ValueError, OSError:
                        raise Refused("backup_runtime_invalid") from None
                    evidence = decode_evidence(
                        _read(folder / "result.json"), expected_digest=run["input_digest"]
                    )
                    if (
                        request["instruction_digest"] != run["input_digest"]
                        or evidence.status != run["status"]
                        or evidence.status not in {"succeeded", "failed", "cancelled"}
                        or not (folder / "started").is_file()
                    ):
                        raise Refused("backup_runtime_invalid")
                    if (
                        run["usage_known"]
                        and not db.execute(
                            "SELECT 1 FROM usage_reconciliations WHERE run_id=?", (run["id"],)
                        ).fetchone()
                        and evidence.cost != run["actual_cost"]
                    ):
                        raise Refused("backup_runtime_invalid")
                    if run["artifact_id"]:
                        artifact = db.execute(
                            "SELECT * FROM artifacts WHERE id=?", (run["artifact_id"],)
                        ).fetchone()
                        if (
                            artifact is None
                            or evidence.output is None
                            or hashlib.sha256(evidence.output.encode()).hexdigest()
                            != artifact["sha256"]
                        ):
                            raise Refused("backup_runtime_invalid")
        rows = db.execute("SELECT * FROM artifacts").fetchall()
        for row in rows:
            # Do not instantiate the store on verification: verification never repairs missing dirs.
            if not (root / "artifacts").is_dir() or (root / "artifacts").is_symlink():
                raise Refused("backup_artifact_missing")
            Artifacts(root / "artifacts").read(Artifact(**dict(row)))
        for memory in db.execute("SELECT * FROM memory_revisions"):
            MemoryFiles(root / "memory").read(
                memory["resident_id"], memory["sha256"], memory["size"]
            )
        if db.execute(
            "SELECT 1 FROM run_memory m JOIN runs r ON r.id=m.run_id "
            "WHERE m.resident_id != r.resident_id"
        ).fetchone():
            raise Refused("backup_references_invalid")
        return {
            "artifacts": len(rows),
            "runs": db.execute("SELECT count(*) FROM runs").fetchone()[0],
            "epoch": db.execute("SELECT value FROM system_meta WHERE key='epoch'").fetchone()[0],
        }


def verify(source: Path) -> dict:
    source = source.absolute()
    if source.is_symlink() or not source.is_dir():
        raise Refused("backup_source_unsafe")
    manifest = json.loads(_read(source / "manifest.json"))
    if (
        not isinstance(manifest, dict)
        or type(manifest.get("format")) is not int
        or manifest.get("format") != FORMAT
        or type(manifest.get("schema")) is not int
        or manifest.get("schema") != SCHEMA_VERSION
        or manifest.get("simulated") is not True
    ):
        raise Refused("backup_format_incompatible")
    files = manifest.get("files")
    if not isinstance(files, dict) or "hearth.db" not in files or len(files) > 100_000:
        raise Refused("backup_manifest_invalid")
    for name, digest in files.items():
        if not isinstance(name, str) or not _allowed(name):
            raise Refused("backup_path_invalid")
        if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise Refused("backup_manifest_invalid")
        path = source / name
        if path.parent.is_symlink() or hashlib.sha256(_read(path)).hexdigest() != digest:
            raise Refused("backup_checksum_mismatch")
    # Reject extra payloads rather than carrying unverified files into the restore.
    actual = set()
    for child in source.iterdir():
        if child.is_symlink():
            raise Refused("backup_source_unsafe")
        if child.is_dir():
            if child.name not in STORES:
                raise Refused("backup_path_invalid")
            actual.update(str(item.relative_to(source)) for item in _store_files(child))
        elif child.name != "manifest.json":
            actual.add(child.name)
    if actual != set(files):
        raise Refused("backup_manifest_mismatch")
    checked = _check_database(source)
    return manifest | {"verified": checked}


def _process_lock(stack: ExitStack, path: Path) -> None:
    try:
        descriptor = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
        lock = stack.enter_context(os.fdopen(descriptor, "a"))
        if not stat.S_ISREG(os.fstat(lock.fileno()).st_mode):
            raise Refused("backup_source_unsafe")
    except OSError:
        raise Refused("backup_source_unsafe") from None
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        raise Refused("backup_workers_busy") from None


def capture(data: Path, destination: Path) -> dict:
    data = data.resolve()
    destination = destination.parent.resolve() / destination.name
    if destination.is_relative_to(data):
        raise Refused("backup_destination_inside_source")
    database = Database(data / "hearth.db")
    if not database.path.is_file() or database.path.is_symlink():
        raise Refused("backup_database_missing")
    with _destination(destination) as temporary, ExitStack() as stack:
        for suffix in (".executor.lock", ".publication.lock", ".notifications.lock"):
            lock = stack.enter_context(database.path.with_suffix(suffix).open("a"))
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise Refused("backup_workers_busy") from None
        # The reserved write transaction freezes scheduler/API mutations too. A
        # separate read connection backs up the same stable state via SQLite's API.
        # A backup is a read operation even for quarantined copies. Reserve a
        # writer slot directly without using the operational mutation interface.
        with sqlite3.connect(database.path) as frozen:
            frozen.execute("BEGIN IMMEDIATE")
            if frozen.execute(
                "SELECT 1 FROM runs WHERE runtime_kind='process_mock' AND finished_at IS NULL"
            ).fetchone():
                raise Refused("backup_process_unsettled")
            process_root = data / "process-mock"
            if process_root.exists():
                if process_root.is_symlink() or not process_root.is_dir():
                    raise Refused("backup_source_unsafe")
                _process_lock(stack, process_root / ".launch.lock")
                for folder in process_root.iterdir():
                    if folder.name.startswith("."):
                        continue
                    if folder.is_symlink() or not folder.is_dir():
                        raise Refused("backup_source_unsafe")
                    if not (folder / "result.json").is_file():
                        raise Refused("backup_process_unsettled")
                    _process_lock(stack, folder / "worker.lock")
            with sqlite3.connect(database.path.as_uri() + "?mode=ro", uri=True) as source:
                with sqlite3.connect(temporary / "hearth.db") as target:
                    source.backup(target)
            os.chmod(temporary / "hearth.db", 0o600)
            with (temporary / "hearth.db").open("rb") as file:
                os.fsync(file.fileno())
            for name in STORES:
                folder = data / name
                if folder.is_symlink():
                    raise Refused("backup_source_unsafe")
                if not folder.exists():
                    continue
                for path in _store_files(folder, skip_hidden=True):
                    _write(temporary / path.relative_to(data), _read(path))
        manifest = {
            "format": FORMAT,
            "schema": SCHEMA_VERSION,
            "release": version("hearth"),
            "implementation_sha256": hashlib.sha256(
                b"".join(
                    path.name.encode() + b"\0" + path.read_bytes()
                    for path in sorted(Path(__file__).parent.glob("*.py"))
                )
            ).hexdigest(),
            "created_at": int(time.time()),
            "simulated": True,
            "files": {
                str(path.relative_to(temporary)): hashlib.sha256(_read(path)).hexdigest()
                for path in sorted(temporary.rglob("*"))
                if path.is_file()
            },
        }
        _write(temporary / "manifest.json", json.dumps(manifest, sort_keys=True, indent=2).encode())
        result = verify(temporary)
    return result


def restore(source: Path, destination: Path) -> dict:
    source = source.absolute()
    destination = destination.parent.resolve() / destination.name
    manifest = verify(source)
    if destination.is_relative_to(source):
        raise Refused("backup_destination_inside_source")
    with _destination(destination) as temporary:
        for name, expected in manifest["files"].items():
            content = _read(source / name)
            if hashlib.sha256(content).hexdigest() != expected:
                raise Refused("backup_changed_during_restore")
            _write(temporary / name, content)
        _check_database(temporary)
        epoch = str(uuid.uuid4())
        with sqlite3.connect(temporary / "hearth.db") as db:
            db.execute("PRAGMA synchronous=FULL")
            db.execute(
                "INSERT OR REPLACE INTO system_meta VALUES ('restore_hold', ?)",
                (
                    json.dumps(
                        {
                            "source_epoch": manifest["verified"]["epoch"],
                            "source_schema": manifest["schema"],
                            "restored_at": int(time.time()),
                        }
                    ),
                ),
            )
            db.execute("UPDATE system_meta SET value=? WHERE key='epoch'", (epoch,))
        _check_database(temporary)
        _write(
            temporary / "restore-manifest.json",
            json.dumps(manifest, sort_keys=True, indent=2).encode(),
        )
    return {
        "read_only": True,
        "epoch": epoch,
        "source_epoch": manifest["verified"]["epoch"],
        "source_schema": manifest["schema"],
        "schema": SCHEMA_VERSION,
    }
