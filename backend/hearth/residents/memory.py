"""Resident memory: immutable files, transactional revisions and pinned run reads."""

import hashlib
import hmac
import json
import os
import re
import sqlite3
import stat
import uuid
from contextlib import contextmanager
from pathlib import Path

from hearth.residents.models import Refused, identifier
from hearth.storage.artifacts import sync_directory
from hearth.work.service import Hearth, _audit

MAX_MEMORY = 128 * 1024
MAX_PAGE = 100
PAGE = 20


def memory_path(resident_id: str, digest: str) -> str:
    identifier(resident_id)
    if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise Refused("invalid_memory_digest")
    return f"{resident_id}/{digest}.md"


class MemoryFiles:
    def __init__(self, root: Path):
        self.root = root

    @contextmanager
    def _directory(self, resident_id: str, *, create: bool = False):
        identifier(resident_id)
        try:
            if create:
                self.root.mkdir(mode=0o700, exist_ok=True)
                sync_directory(self.root.parent)
            root = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            try:
                if create:
                    try:
                        os.mkdir(resident_id, mode=0o700, dir_fd=root)
                    except FileExistsError:
                        pass
                    os.fsync(root)
                directory = os.open(
                    resident_id, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=root
                )
                try:
                    yield directory
                finally:
                    os.close(directory)
            finally:
                os.close(root)
        except OSError:
            raise Refused("memory_missing_or_unsafe") from None

    def _read(self, directory: int, name: str) -> bytes:
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
        with os.fdopen(fd, "rb") as file:
            if not stat.S_ISREG(os.fstat(file.fileno()).st_mode):
                raise Refused("memory_missing_or_unsafe")
            data = file.read(MAX_MEMORY + 1)
        if len(data) > MAX_MEMORY:
            raise Refused("memory_too_large")
        return data

    def publish(self, resident_id: str, data: bytes) -> str:
        if not isinstance(data, bytes) or len(data) > MAX_MEMORY:
            raise Refused("memory_too_large")
        digest = hashlib.sha256(data).hexdigest()
        name = Path(memory_path(resident_id, digest)).name
        with self._directory(resident_id, create=True) as directory:
            temporary = ".memory-" + uuid.uuid4().hex
            fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600, dir_fd=directory)
            try:
                with os.fdopen(fd, "wb") as file:
                    file.write(data)
                    file.flush()
                    os.fsync(file.fileno())
                try:
                    os.link(temporary, name, src_dir_fd=directory, dst_dir_fd=directory)
                except FileExistsError:
                    if self._read(directory, name) != data:
                        raise Refused("memory_content_conflict") from None
                os.fsync(directory)
            finally:
                os.unlink(temporary, dir_fd=directory)
        return digest

    def read(self, resident_id: str, digest: str, size: int) -> str:
        name = Path(memory_path(resident_id, digest)).name
        if type(size) is not int or not 0 <= size <= MAX_MEMORY:
            raise Refused("invalid_memory_size")
        with self._directory(resident_id) as directory:
            data = self._read(directory, name)
        if len(data) != size or hashlib.sha256(data).hexdigest() != digest:
            raise Refused("memory_corrupt")
        try:
            return data.decode("utf-8")
        except UnicodeDecodeError:
            raise Refused("invalid_memory_encoding") from None


def read_revision(
    db: sqlite3.Connection, files: MemoryFiles, resident_id: str, revision: int
) -> dict:
    if revision == 0:
        return {"resident_id": resident_id, "revision": 0, "sha256": None, "text": ""}
    row = db.execute(
        "SELECT * FROM memory_revisions WHERE resident_id=? AND revision=?", (resident_id, revision)
    ).fetchone()
    if row is None:
        raise Refused("memory_revision_not_found")
    text = files.read(resident_id, row["sha256"], row["size"])
    return {"resident_id": resident_id, "revision": revision, "sha256": row["sha256"], "text": text}


def run_memory_writes(db: sqlite3.Connection, run_id: str) -> list[int]:
    """The memory revisions this run authored, oldest first, from its durable receipts."""
    revisions = []
    for row in db.execute("SELECT receipt FROM memory_operations WHERE run_id=?", (run_id,)):
        revision = _receipt_revision(row["receipt"])
        if revision is not None and revision not in revisions:
            revisions.append(revision)
    return sorted(revisions)


def _receipt_revision(stored: str) -> int | None:
    try:
        revision = json.loads(stored)["revision"]
    except ValueError, TypeError, KeyError:
        return None
    return revision if type(revision) is int and revision > 0 else None


class Memory:
    def __init__(self, hearth: Hearth):
        self.hearth = hearth
        self.files = MemoryFiles(hearth.database.path.resolve().parent / "memory")

    def read(self, resident_id: str, *, revision: int | None = None) -> dict:
        identifier(resident_id)
        if revision is not None and (type(revision) is not int or revision < 0):
            raise Refused("invalid_memory_revision")
        with self.hearth.database.transaction() as db:
            if not db.execute("SELECT 1 FROM residents WHERE id=?", (resident_id,)).fetchone():
                raise Refused("resident_not_found")
            if revision is None:
                revision = db.execute(
                    "SELECT COALESCE(MAX(revision),0) FROM memory_revisions WHERE resident_id=?",
                    (resident_id,),
                ).fetchone()[0]
            return read_revision(db, self.files, resident_id, revision)

    def history(self, resident_id: str, *, limit: int = PAGE, offset: int = 0) -> dict:
        """Every revision of one note, newest first, with the author Hearth recorded.

        Authorship comes from the revision row, never from the text. A run-authored
        revision also names the run that wrote it, taken from that write's receipt.
        """
        identifier(resident_id)
        if type(limit) is not int or not 1 <= limit <= MAX_PAGE:
            raise Refused("invalid_memory_page")
        if type(offset) is not int or not 0 <= offset <= 1_000_000:
            raise Refused("invalid_memory_page")
        with self.hearth.database.transaction() as db:
            if not db.execute("SELECT 1 FROM residents WHERE id=?", (resident_id,)).fetchone():
                raise Refused("resident_not_found")
            total = db.execute(
                "SELECT COUNT(*) FROM memory_revisions WHERE resident_id=?", (resident_id,)
            ).fetchone()[0]
            rows = db.execute(
                "SELECT revision,sha256,size,created_at,author FROM memory_revisions "
                "WHERE resident_id=? ORDER BY revision DESC LIMIT ? OFFSET ?",
                (resident_id, limit, offset),
            ).fetchall()
            writers = self._writers(db, resident_id)
            return {
                "resident_id": resident_id,
                "limit": limit,
                "offset": offset,
                "total": total,
                "revisions": [dict(row) | {"run_id": writers.get(row["revision"])} for row in rows],
            }

    def _writers(self, db, resident_id: str) -> dict[int, str]:
        """Which run wrote each run-authored revision of this resident's note."""
        writers = {}
        for row in db.execute(
            "SELECT o.run_id AS run_id, o.receipt AS receipt FROM memory_operations o "
            "JOIN runs r ON r.id=o.run_id WHERE r.resident_id=?",
            (resident_id,),
        ):
            revision = _receipt_revision(row["receipt"])
            if revision is not None:
                writers[revision] = row["run_id"]
        return writers

    def save(self, resident_id: str, text: str, *, expected_revision: int) -> dict:
        with self.hearth.database.transaction(write=True) as db:
            return self.save_in_transaction(
                db, resident_id, text, expected_revision=expected_revision
            )

    def save_in_transaction(
        self, db, resident_id: str, text: str, *, expected_revision: int
    ) -> dict:
        """The operator writer. Runs reach the same revisions through `save_from_run`."""
        return self._write(
            db,
            resident_id,
            text,
            expected_revision=expected_revision,
            author="operator",
            actor="operator",
        )

    def save_from_run(
        self,
        db,
        run_id: str,
        text: str,
        *,
        expected_revision: int,
        operation_id: str,
        resident_id: str | None = None,
    ) -> dict:
        """A live run writes its own resident's memory once per operation identity.

        Authorship comes from the authenticated run, never from the text. The caller
        owns the writer so the revision, its operation receipt and its audit commit
        together.
        """
        from hearth.authority.run_access import context_revoked, live_run

        identifier(operation_id)
        run = live_run(db, run_id)
        if context_revoked(db, run_id):
            raise Refused("run_context_unavailable")
        if resident_id is not None:
            identifier(resident_id)
            if resident_id != run["resident_id"]:
                raise Refused("memory_run_mismatch")
        resident_id = run["resident_id"]
        pinned = db.execute(
            "SELECT resident_id FROM run_memory WHERE run_id=?", (run_id,)
        ).fetchone()
        if pinned is not None and pinned["resident_id"] != resident_id:
            raise Refused("memory_run_mismatch")
        if type(expected_revision) is not int or expected_revision < 0:
            raise Refused("invalid_memory_revision")
        if not isinstance(text, str):
            raise Refused("invalid_memory_text")
        payload = hashlib.sha256(
            json.dumps([resident_id, expected_revision, text], sort_keys=True).encode()
        ).hexdigest()
        previous = db.execute(
            "SELECT * FROM memory_operations WHERE run_id=? AND operation_id=?",
            (run_id, operation_id),
        ).fetchone()
        if previous is not None:
            if not hmac.compare_digest(previous["payload_digest"], payload):
                raise Refused("operation_conflict")
            return self._original_receipt(db, previous["receipt"])
        saved = self._write(
            db,
            resident_id,
            text,
            expected_revision=expected_revision,
            author="run",
            actor="run:" + run_id,
        )
        receipt = {
            "resident_id": resident_id,
            "revision": saved["revision"],
            "sha256": saved["sha256"],
            "author": "run",
            "operation_id": operation_id,
            "run_id": run_id,
        }
        db.execute(
            "INSERT INTO memory_operations VALUES (?,?,?,?)",
            (run_id, operation_id, payload, json.dumps(receipt, sort_keys=True)),
        )
        return {**receipt, "text": text}

    def _original_receipt(self, db, stored: str) -> dict:
        """A retry replays the recorded revision; memory text lives only in its file."""
        try:
            receipt = json.loads(stored)
            revision = read_revision(db, self.files, receipt["resident_id"], receipt["revision"])
        except ValueError, TypeError, KeyError:
            raise Refused("memory_operation_receipt_corrupt") from None
        if revision["sha256"] != receipt["sha256"] or receipt["author"] != "run":
            raise Refused("memory_operation_receipt_corrupt")
        return {**receipt, "text": revision["text"]}

    def _write(
        self, db, resident_id: str, text: str, *, expected_revision: int, author: str, actor: str
    ) -> dict:
        from hearth.residents.lifecycle import check_not_archived

        check_not_archived(db, resident_id)
        identifier(resident_id)
        if type(expected_revision) is not int or expected_revision < 0:
            raise Refused("invalid_memory_revision")
        if not isinstance(text, str):
            raise Refused("invalid_memory_text")
        try:
            data = text.encode("utf-8")
        except UnicodeEncodeError:
            raise Refused("invalid_memory_encoding") from None
        if len(data) > MAX_MEMORY:
            raise Refused("memory_too_large")
        if not db.execute("SELECT 1 FROM residents WHERE id=?", (resident_id,)).fetchone():
            raise Refused("resident_not_found")
        revision = db.execute(
            "SELECT COALESCE(MAX(revision),0) FROM memory_revisions WHERE resident_id=?",
            (resident_id,),
        ).fetchone()[0]
        if revision != expected_revision:
            raise Refused("revision_conflict")
        revision += 1
        digest = self.files.publish(resident_id, data)
        now = int(self.hearth.clock())
        db.execute(
            "INSERT INTO memory_revisions VALUES (?,?,?,?,?,?)",
            (resident_id, revision, digest, len(data), now, author),
        )
        _audit(
            db,
            "memory.saved",
            resident_id,
            now,
            {
                "actor": actor,
                "author": author,
                "revision": revision,
                "sha256": digest,
                "size": len(data),
            },
        )
        return {
            "resident_id": resident_id,
            "revision": revision,
            "sha256": digest,
            "text": text,
        }
