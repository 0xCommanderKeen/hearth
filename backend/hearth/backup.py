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
from hearth.database import SCHEMA_VERSION, Database
from hearth.models import Refused, identifier

FORMAT = 1
# Format 1 first shipped with schema 6. Older databases were never backup inputs.
SUPPORTED_SCHEMAS = frozenset({6, 7, 8, 9, 10, 11})
STORES = {
    "artifacts": ".md",
    "mock-runtime": ".json",
    "mock-inbox": ".md",
    "mock-noticeboard": ".md",
}
MAX_FILE = 128 * 1024 * 1024


def _read(path: Path) -> bytes:
    if path.parent.is_symlink():
        raise Refused("backup_file_missing_or_unsafe")
    try:
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


@contextmanager
def _destination(destination: Path):
    if destination.exists() or destination.is_symlink():
        raise Refused("backup_destination_exists")
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=".hearth-copy-", dir=destination.parent))
    try:
        yield temporary
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


def _check_database(root: Path, *, schema: int = SCHEMA_VERSION) -> dict:
    path = root / "hearth.db"
    with sqlite3.connect(path.as_uri() + "?mode=ro", uri=True) as db:
        db.row_factory = sqlite3.Row
        if db.execute("PRAGMA user_version").fetchone()[0] != schema:
            raise Refused("backup_schema_incompatible")
        if db.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise Refused("backup_database_corrupt")
        if db.execute("PRAGMA foreign_key_check").fetchone() is not None:
            raise Refused("backup_references_invalid")
        if db.execute("SELECT 1 FROM sqlite_master WHERE type IN ('trigger','view')").fetchone():
            raise Refused("backup_schema_unexpected")
        rows = db.execute("SELECT * FROM artifacts").fetchall()
        for row in rows:
            # Do not instantiate the store on verification: verification never repairs missing dirs.
            if not (root / "artifacts").is_dir() or (root / "artifacts").is_symlink():
                raise Refused("backup_artifact_missing")
            Artifacts(root / "artifacts").read(Artifact(**dict(row)))
        return {
            "artifacts": len(rows),
            "runs": db.execute("SELECT count(*) FROM runs").fetchone()[0],
            "epoch": db.execute("SELECT value FROM system_meta WHERE key='epoch'").fetchone()[0],
        }


def _check_upgrade_layout(root: Path, schema: int) -> None:
    """The supported 6–11 path is additive; validate the actual historical layout."""
    with tempfile.TemporaryDirectory(prefix="hearth-schema-") as temporary:
        reference = Path(temporary) / "reference.db"
        Database(reference).initialize()
        with (
            sqlite3.connect(reference) as expected,
            sqlite3.connect((root / "hearth.db").as_uri() + "?mode=ro", uri=True) as actual,
        ):
            tables = {
                row[0]
                for row in expected.execute("SELECT name FROM sqlite_master WHERE type='table'")
            }
            for introduced, name in (
                (7, "operator_controls"),
                (8, "run_credentials"),
                (9, "usage_reconciliations"),
            ):
                if schema < introduced:
                    tables.remove(name)
            if tables != {
                row[0]
                for row in actual.execute("SELECT name FROM sqlite_master WHERE type='table'")
            }:
                raise Refused("backup_schema_layout_incompatible")
            for table in tables:
                sql = expected.execute(
                    "SELECT sql FROM sqlite_master WHERE name=?", (table,)
                ).fetchone()[0]
                if schema < 10 and table in {"declarations", "runs"}:
                    sql = re.sub(r",\s*budget_timezone TEXT NOT NULL DEFAULT 'UTC'", "", sql)
                if schema < 11 and table == "declarations":
                    sql = re.sub(r",\s*skill_text TEXT NOT NULL DEFAULT ''", "", sql)
                actual_sql = actual.execute(
                    "SELECT sql FROM sqlite_master WHERE name=?", (table,)
                ).fetchone()[0]
                if " ".join(sql.split()) != " ".join(actual_sql.split()):
                    raise Refused("backup_schema_layout_incompatible")
                columns = expected.execute(f'PRAGMA table_info("{table}")').fetchall()
                if schema < 10 and table in {"declarations", "runs"}:
                    columns = [row for row in columns if row[1] != "budget_timezone"]
                if schema < 11 and table == "declarations":
                    columns = [row for row in columns if row[1] != "skill_text"]
                if columns != actual.execute(f'PRAGMA table_info("{table}")').fetchall():
                    raise Refused("backup_schema_layout_incompatible")
                for query in (
                    f'PRAGMA foreign_key_list("{table}")',
                    "SELECT name,sql FROM sqlite_master WHERE type='index' "
                    "AND tbl_name=? ORDER BY name",
                ):
                    args = (table,) if "?" in query else ()
                    if (
                        expected.execute(query, args).fetchall()
                        != actual.execute(query, args).fetchall()
                    ):
                        raise Refused("backup_schema_layout_incompatible")


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
        or manifest.get("schema") not in SUPPORTED_SCHEMAS
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
            actual.update(f"{child.name}/{item.name}" for item in child.iterdir())
        elif child.name != "manifest.json":
            actual.add(child.name)
    if actual != set(files):
        raise Refused("backup_manifest_mismatch")
    checked = _check_database(source, schema=manifest["schema"])
    if manifest["schema"] != SCHEMA_VERSION:
        _check_upgrade_layout(source, manifest["schema"])
    return manifest | {"verified": checked}


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
            with sqlite3.connect(database.path.as_uri() + "?mode=ro", uri=True) as source:
                with sqlite3.connect(temporary / "hearth.db") as target:
                    source.backup(target)
            os.chmod(temporary / "hearth.db", 0o600)
            with (temporary / "hearth.db").open("rb") as file:
                os.fsync(file.fileno())
            for name, extension in STORES.items():
                folder = data / name
                if folder.is_symlink():
                    raise Refused("backup_source_unsafe")
                if not folder.exists():
                    continue
                for path in folder.iterdir():
                    if path.name.startswith("."):
                        continue
                    if path.suffix != extension or not _allowed(f"{name}/{path.name}"):
                        raise Refused("backup_path_invalid")
                    _write(temporary / name / path.name, _read(path))
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


def restore(source: Path, destination: Path, *, upgrade: bool = False) -> dict:
    source = source.absolute()
    destination = destination.parent.resolve() / destination.name
    manifest = verify(source)
    if manifest["schema"] != SCHEMA_VERSION and not upgrade:
        raise Refused("backup_upgrade_required")
    if destination.is_relative_to(source):
        raise Refused("backup_destination_inside_source")
    with _destination(destination) as temporary:
        for name, expected in manifest["files"].items():
            content = _read(source / name)
            if hashlib.sha256(content).hexdigest() != expected:
                raise Refused("backup_changed_during_restore")
            _write(temporary / name, content)
        _check_database(temporary, schema=manifest["schema"])
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
        # No copied work can run, including if a migration is interrupted. The
        # private staging copy is never exposed until the upgraded state verifies.
        if manifest["schema"] != SCHEMA_VERSION:
            Database(temporary / "hearth.db").initialize()
            _check_upgrade_layout(temporary, SCHEMA_VERSION)
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
        "upgraded": manifest["schema"] != SCHEMA_VERSION,
    }
