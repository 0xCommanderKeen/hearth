"""Readable mock-state exports. Validation confers neither trust nor execution authority."""

import hashlib
import json
import sqlite3
import tempfile
from pathlib import Path

from hearth import backup
from hearth.database import SCHEMA_VERSION, Database
from hearth.models import Refused

FORMAT = "hearth-mock-state"
VERSION = 1
# Deliberately pinned: a database upgrade requires an explicit format compatibility decision.
SUPPORTED_SCHEMA = 10
MAX_EXPORT = 32 * 1024 * 1024
MAX_ROWS = 100_000
EXCLUDED = {"system_meta", "run_credentials"}


def _json(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, ensure_ascii=True, separators=(",", ":")).encode()


def _pairs(pairs: list) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise Refused("portable_duplicate_key")
        result[key] = value
    return result


def _schema(db: sqlite3.Connection) -> list:
    return db.execute(
        "SELECT type, name, tbl_name, sql FROM sqlite_master ORDER BY type, name"
    ).fetchall()


def _columns(db: sqlite3.Connection) -> dict:
    return {
        name: db.execute(f'PRAGMA table_info("{name}")').fetchall()
        for (name,) in db.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }


def _canonical_tables(tables: dict) -> dict:
    return {name: sorted(rows, key=_json) for name, rows in tables.items()}


def _check(payload: dict, reference: sqlite3.Connection) -> dict:
    if not isinstance(payload, dict) or set(payload) != {
        "format",
        "version",
        "schema",
        "simulated",
        "source_epoch",
        "tables",
        "files",
    }:
        raise Refused("portable_fields_invalid")
    if (
        payload["format"] != FORMAT
        or type(payload["version"]) is not int
        or payload["version"] != VERSION
        or type(payload["schema"]) is not int
        or payload["schema"] != SUPPORTED_SCHEMA
        or SCHEMA_VERSION != SUPPORTED_SCHEMA
        or payload["simulated"] is not True
    ):
        raise Refused("portable_format_incompatible")
    if not isinstance(payload["source_epoch"], str) or not 1 <= len(payload["source_epoch"]) <= 128:
        raise Refused("portable_epoch_invalid")
    columns = _columns(reference)
    tables = payload["tables"]
    if not isinstance(tables, dict) or set(tables) != set(columns) - EXCLUDED:
        raise Refused("portable_tables_invalid")
    count = 0
    reference.execute("PRAGMA foreign_keys = OFF")
    reference.execute("BEGIN")
    for name in columns:
        reference.execute(f'DELETE FROM "{name}"')
    for name in sorted(tables, key=lambda name: name == "sqlite_sequence"):
        rows = tables[name]
        if name == "sqlite_sequence":
            reference.execute("DELETE FROM sqlite_sequence")
        if not isinstance(rows, list):
            raise Refused("portable_rows_invalid")
        count += len(rows)
        if count > MAX_ROWS:
            raise Refused("portable_too_large")
        expected = {
            col[1]: col for col in columns[name] if not (name == "runs" and col[1] == "owner_token")
        }
        for row in rows:
            if not isinstance(row, dict) or set(row) != set(expected):
                raise Refused("portable_columns_invalid")
            for key, value in row.items():
                _, _, kind, required, _, primary = expected[key]
                if name == "sqlite_sequence":
                    kind = {"name": "TEXT", "seq": "INTEGER"}[key]
                    required = True
                if value is None:
                    if required or primary:
                        raise Refused("portable_value_invalid")
                elif type(value) is not {"INTEGER": int, "TEXT": str}[kind]:
                    raise Refused("portable_value_invalid")
            values = dict(row)
            if name == "runs":
                # A reconstruction used for constraint checking only, never an owner grant.
                values["owner_token"] = "portable:" + values["id"]
            keys = ",".join(f'"{key}"' for key in values)
            reference.execute(
                f'INSERT INTO "{name}" ({keys}) VALUES ({",".join("?" for _ in values)})',
                tuple(values.values()),
            )
    if reference.execute("PRAGMA foreign_key_check").fetchone() is not None:
        raise Refused("portable_references_invalid")
    # These application references are not all foreign keys in schema 10.
    for query in (
        "SELECT 1 FROM residents r LEFT JOIN declarations d ON d.resident_id=r.id "
        "AND d.revision=r.revision WHERE d.resident_id IS NULL",
        "SELECT 1 FROM routines r LEFT JOIN routine_revisions d ON d.routine_id=r.id "
        "AND d.revision=r.revision WHERE d.routine_id IS NULL",
        "SELECT 1 FROM runs r JOIN tasks t ON t.id=r.task_id WHERE r.resident_id != t.resident_id",
        "SELECT 1 FROM runs r LEFT JOIN artifacts a ON a.id=r.artifact_id "
        "AND a.run_id=r.id WHERE r.artifact_id IS NOT NULL AND a.id IS NULL",
    ):
        if reference.execute(query).fetchone() is not None:
            raise Refused("portable_references_invalid")
    sequences = tables["sqlite_sequence"]
    highest = reference.execute("SELECT COALESCE(MAX(sequence), 0) FROM audit").fetchone()[0]
    if (
        len(sequences) > 1
        or (not sequences and highest > 0)
        or any(
            row["name"] != "audit" or row["seq"] < highest or row["seq"] < 0 for row in sequences
        )
    ):
        raise Refused("portable_sequence_invalid")
    files = payload["files"]
    if not isinstance(files, dict) or len(files) > MAX_ROWS:
        raise Refused("portable_files_invalid")
    for name, file in files.items():
        if name == "hearth.db" or not backup._allowed(name):
            raise Refused("portable_path_invalid")
        if (
            not isinstance(file, dict)
            or set(file) != {"sha256", "text"}
            or not isinstance(file["text"], str)
        ):
            raise Refused("portable_files_invalid")
        if hashlib.sha256(file["text"].encode()).hexdigest() != file["sha256"]:
            raise Refused("portable_checksum_mismatch")
    for artifact in tables["artifacts"]:
        file = files.get("artifacts/" + artifact["relative_path"])
        if (
            file is None
            or file["sha256"] != artifact["sha256"]
            or len(file["text"].encode()) != artifact["size"]
        ):
            raise Refused("portable_artifact_invalid")
    reference.rollback()
    return payload | {"tables": _canonical_tables(tables)}


def validate(content: bytes) -> dict:
    """Strictly validate data, relational constraints and file hashes without importing it.

    The digest ignores row ordering and the source observation epoch. It preserves
    every exported operational value and file byte. No archive SQL is executed.
    """
    if len(content) > MAX_EXPORT:
        raise Refused("portable_too_large")
    try:
        payload = json.loads(content, object_pairs_hook=_pairs)
        with tempfile.TemporaryDirectory(prefix="hearth-validate-") as temporary:
            database = Database(Path(temporary) / "reference.db")
            database.initialize()
            with sqlite3.connect(database.path) as reference:
                normalized = _check(payload, reference)
    except Refused:
        raise
    except ValueError, TypeError, KeyError, OverflowError, sqlite3.DatabaseError, RecursionError:
        raise Refused("portable_data_invalid") from None
    semantic = {key: value for key, value in normalized.items() if key != "source_epoch"}
    return {
        "format": FORMAT,
        "version": VERSION,
        "schema": SUPPORTED_SCHEMA,
        "simulated": True,
        "semantic_sha256": hashlib.sha256(_json(semantic)).hexdigest(),
        "rows": {name: len(rows) for name, rows in normalized["tables"].items()},
        "files": len(normalized["files"]),
    }


def export(source: Path, destination: Path) -> dict:
    """Export a verified mock backup as state.json in a new private directory.

    Source is copied and verified before reading; no live database or caller SQL
    participates. The export is evidence, not an executable database or launch grant.
    """
    with tempfile.TemporaryDirectory(prefix="hearth-export-") as temporary:
        root = Path(temporary)
        copy = root / "copy"
        restored = backup.restore(source, copy)
        reference_path = root / "reference.db"
        Database(reference_path).initialize()
        with (
            sqlite3.connect(copy / "hearth.db") as db,
            sqlite3.connect(reference_path) as reference,
        ):
            if _schema(db) != _schema(reference):
                raise Refused("portable_schema_unsupported")
            meta = dict(db.execute("SELECT key, value FROM system_meta"))
            if set(meta) - {"epoch", "restore_hold"}:
                raise Refused("portable_metadata_unsupported")
            columns = _columns(reference)
            tables = {}
            for name in columns.keys() - EXCLUDED:
                keys = [
                    col[1]
                    for col in columns[name]
                    if not (name == "runs" and col[1] == "owner_token")
                ]
                selection = ",".join(f'"{key}"' for key in keys)
                tables[name] = [
                    dict(zip(keys, row, strict=True))
                    for row in db.execute(f'SELECT {selection} FROM "{name}"')
                ]
        manifest = json.loads((copy / "restore-manifest.json").read_text())
        files = {}
        for name, digest in manifest["files"].items():
            if name != "hearth.db":
                try:
                    content = backup._read(copy / name).decode("utf-8")
                except UnicodeDecodeError:
                    raise Refused("portable_file_encoding_unsupported") from None
                files[name] = {"sha256": digest, "text": content}
        payload = {
            "format": FORMAT,
            "version": VERSION,
            "schema": SUPPORTED_SCHEMA,
            "simulated": True,
            "source_epoch": restored["source_epoch"],
            "tables": _canonical_tables(tables),
            "files": files,
        }
        content = json.dumps(payload, sort_keys=True, indent=2, ensure_ascii=True).encode() + b"\n"
        result = validate(content)
        destination = destination.parent.resolve() / destination.name
        if destination.is_relative_to(source.resolve()):
            raise Refused("portable_destination_inside_source")
        with backup._destination(destination) as pending:
            backup._write(pending / "state.json", content)
    return result
