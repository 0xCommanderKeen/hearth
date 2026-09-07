"""Resident journal: one dated entry per run, bounded, older entries rolled to files."""

import hashlib
import os
import re
from contextlib import contextmanager
from pathlib import Path

from hearth.authority.household import read_journal_limit
from hearth.residents.memory import MemoryFiles
from hearth.residents.models import Refused, identifier
from hearth.work.service import Hearth, _audit

MAX_ENTRY = 4 * 1024
MAX_PAGE = 100
PAGE = 20
# How many of the newest entries a run opens with. Bounded so a long journal can
# never crowd out the rest of the pinned context.
CONTEXT_ENTRIES = 5
# A run writes its own entry while it works; settled or cancelled work writes nothing.
WRITING_RUNS = frozenset({"starting", "running", "stopping"})


def journal_path(resident_id: str, digest: str) -> str:
    identifier(resident_id)
    if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise Refused("invalid_journal_digest")
    return f"{resident_id}/journal/{digest}.md"


def checked_text(text: str) -> bytes:
    if not isinstance(text, str) or not text.strip():
        raise Refused("invalid_journal_text")
    try:
        data = text.encode("utf-8")
    except UnicodeEncodeError:
        raise Refused("invalid_journal_encoding") from None
    if len(data) > MAX_ENTRY:
        raise Refused("journal_entry_too_large")
    return data


def entry_document(entry: dict) -> str:
    """The exact archived form: a fixed header the writer never varies, then the text."""
    return (
        "---\n"
        f"resident: {entry['resident_id']}\n"
        f"run: {entry['run_id']}\n"
        f"sequence: {entry['sequence']}\n"
        f"at: {entry['at']}\n"
        "---\n"
    ) + entry["text"]


def _number(value: str) -> int:
    if not re.fullmatch(r"[0-9]{1,18}", value):
        raise Refused("journal_archive_invalid")
    return int(value)


def parse_entry(document: str, resident_id: str) -> dict:
    """Read one archived entry back exactly; anything else is refused, never repaired."""
    header, separator, text = document.partition("\n---\n")
    lines = header.split("\n")
    if not separator or len(lines) != 5 or lines[0] != "---":
        raise Refused("journal_archive_invalid")
    values = {}
    for line, key in zip(lines[1:], ("resident", "run", "sequence", "at"), strict=True):
        if not line.startswith(key + ": "):
            raise Refused("journal_archive_invalid")
        values[key] = line[len(key) + 2 :]
    entry = {
        "resident_id": values["resident"],
        "sequence": _number(values["sequence"]),
        "run_id": values["run"],
        "at": _number(values["at"]),
        "text": text,
    }
    try:
        identifier(entry["resident_id"])
        identifier(entry["run_id"])
        checked_text(text)
    except Refused:
        raise Refused("journal_archive_invalid") from None
    if entry["resident_id"] != resident_id or entry["sequence"] < 1:
        raise Refused("journal_archive_invalid")
    if entry_document(entry) != document:
        raise Refused("journal_archive_invalid")
    return entry


def checked_archive(data: bytes, resident_id: str, name: str) -> dict:
    if hashlib.sha256(data).hexdigest() != Path(name).stem or Path(name).suffix != ".md":
        raise Refused("journal_archive_corrupt")
    try:
        document = data.decode("utf-8")
    except UnicodeDecodeError:
        raise Refused("journal_archive_invalid") from None
    return parse_entry(document, resident_id)


def checked_entry(row) -> dict:
    entry = {key: row[key] for key in ("resident_id", "sequence", "run_id", "at", "text")}
    try:
        data = checked_text(entry["text"])
        identifier(entry["resident_id"])
        identifier(entry["run_id"])
    except Refused:
        raise Refused("journal_entry_corrupt") from None
    if (
        type(entry["sequence"]) is not int
        or entry["sequence"] < 1
        or type(entry["at"]) is not int
        or row["size"] != len(data)
        or row["sha256"] != hashlib.sha256(data).hexdigest()
    ):
        raise Refused("journal_entry_corrupt")
    return entry


class JournalFiles(MemoryFiles):
    """Archived entries sit one level below the resident's memory directory."""

    @contextmanager
    def _directory(self, resident_id: str, *, create: bool = False):
        with super()._directory(resident_id, create=create) as parent:
            try:
                if create:
                    try:
                        os.mkdir("journal", mode=0o700, dir_fd=parent)
                    except FileExistsError:
                        pass
                    os.fsync(parent)
                directory = os.open(
                    "journal", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent
                )
            except OSError:
                raise Refused("memory_missing_or_unsafe") from None
            try:
                yield directory
            finally:
                os.close(directory)

    def entry(self, resident_id: str, digest: str) -> dict:
        """One archived entry by its content hash; anything else is refused, never repaired."""
        name = Path(journal_path(resident_id, digest)).name
        with self._directory(resident_id) as directory:
            try:
                data = self._read(directory, name)
            except OSError:
                raise Refused("journal_entry_unavailable") from None
        return checked_archive(data, resident_id, name)

    def entries(self, resident_id: str) -> list[dict]:
        """Every archived entry, oldest first. A resident without archives has none."""
        identifier(resident_id)
        path = self.root / resident_id / "journal"
        if not path.exists() and not path.is_symlink():
            return []
        entries = []
        with self._directory(resident_id) as directory:
            for name in sorted(os.listdir(directory)):
                if name.startswith("."):
                    continue
                entries.append(checked_archive(self._read(directory, name), resident_id, name))
        return sorted(entries, key=lambda entry: entry["sequence"])


class Journal:
    """Reads are operator-facing; writes belong to the run that did the work."""

    def __init__(self, hearth: Hearth):
        self.hearth = hearth
        self.files = JournalFiles(hearth.database.path.resolve().parent / "memory")

    def read(self, resident_id: str, *, limit: int = PAGE, offset: int = 0) -> dict:
        identifier(resident_id)
        if type(limit) is not int or not 1 <= limit <= MAX_PAGE:
            raise Refused("invalid_journal_page")
        if type(offset) is not int or not 0 <= offset <= 1_000_000:
            raise Refused("invalid_journal_page")
        with self.hearth.database.transaction() as db:
            if not db.execute("SELECT 1 FROM residents WHERE id=?", (resident_id,)).fetchone():
                raise Refused("resident_not_found")
            total = db.execute(
                "SELECT COUNT(*) FROM journal_entries WHERE resident_id=?", (resident_id,)
            ).fetchone()[0]
            rows = db.execute(
                "SELECT * FROM journal_entries WHERE resident_id=? "
                "ORDER BY sequence DESC LIMIT ? OFFSET ?",
                (resident_id, limit, offset),
            ).fetchall()
            return {
                "resident_id": resident_id,
                "limit": limit,
                "offset": offset,
                "total": total,
                "entries": [checked_entry(row) for row in rows],
            }

    def write(self, resident_id: str, run_id: str, text: str) -> dict:
        with self.hearth.database.transaction(write=True) as db:
            return self.write_in_transaction(db, resident_id, run_id, text)

    def write_in_transaction(self, db, resident_id: str, run_id: str, text: str) -> dict:
        from hearth.residents.lifecycle import check_not_archived

        check_not_archived(db, resident_id)
        identifier(resident_id)
        identifier(run_id)
        data = checked_text(text)
        run = db.execute(
            "SELECT resident_id,status,cancellation_requested FROM runs WHERE id=?", (run_id,)
        ).fetchone()
        if run is None:
            raise Refused("run_not_found")
        if run["resident_id"] != resident_id:
            raise Refused("journal_run_mismatch")
        if run["status"] not in WRITING_RUNS or run["cancellation_requested"]:
            raise Refused("journal_run_not_writing")
        now = int(self.hearth.clock())
        digest = hashlib.sha256(data).hexdigest()
        existing = db.execute(
            "SELECT sequence FROM journal_entries WHERE run_id=?", (run_id,)
        ).fetchone()
        if existing is None:
            sequence = db.execute(
                "SELECT COALESCE(MAX(sequence),0)+1 FROM journal_entries WHERE resident_id=?",
                (resident_id,),
            ).fetchone()[0]
            db.execute(
                "INSERT INTO journal_entries VALUES (?,?,?,?,?,?,?)",
                (resident_id, sequence, run_id, now, digest, len(data), text),
            )
        else:
            # One entry per run: the run rewrites its own entry, keeping its place.
            sequence = existing["sequence"]
            db.execute(
                "UPDATE journal_entries SET at=?,sha256=?,size=?,text=? WHERE run_id=?",
                (now, digest, len(data), text, run_id),
            )
        _audit(
            db,
            "journal.replaced" if existing else "journal.written",
            resident_id,
            now,
            {"run_id": run_id, "sequence": sequence, "sha256": digest, "size": len(data)},
        )
        entry = {
            "resident_id": resident_id,
            "sequence": sequence,
            "run_id": run_id,
            "at": now,
            "text": text,
        }
        return entry | {"archived": self._roll(db, resident_id, now)}

    def _roll(self, db, resident_id: str, now: int) -> list[str]:
        """Keep the newest entries in the database; older ones become immutable files."""
        limit = read_journal_limit(db)
        rolled = []
        for row in db.execute(
            "SELECT * FROM journal_entries WHERE resident_id=? "
            "ORDER BY sequence DESC LIMIT -1 OFFSET ?",
            (resident_id, limit),
        ).fetchall():
            entry = checked_entry(row)
            document = entry_document(entry).encode("utf-8")
            digest = self.files.publish(resident_id, document)
            db.execute(
                "DELETE FROM journal_entries WHERE resident_id=? AND sequence=?",
                (resident_id, entry["sequence"]),
            )
            # The row moves with the text: an archived entry keeps a checked reference,
            # so a file nothing points at is an orphan and never journal history.
            db.execute(
                "INSERT INTO journal_archives VALUES (?,?,?,?,?,?)",
                (
                    resident_id,
                    entry["sequence"],
                    entry["run_id"],
                    entry["at"],
                    digest,
                    len(document),
                ),
            )
            _audit(
                db,
                "journal.archived",
                resident_id,
                now,
                {
                    "run_id": entry["run_id"],
                    "sequence": entry["sequence"],
                    "sha256": digest,
                    "size": len(document),
                },
            )
            rolled.append(digest)
        return rolled

    def archived(self, resident_id: str) -> list[dict]:
        """Every entry that rolled out of the database, oldest first; files are never removed."""
        return self.files.entries(resident_id)


def run_journal_summary(db, run_id: str) -> dict:
    """What one run opened with and what it wrote, for the operator's run view.

    Retention deletes the row but never the entry, so an entry that has rolled out is
    still read from its archive reference. `journal_written` is None only when the run
    wrote nothing at all.
    """
    opened = [
        row["sequence"]
        for row in db.execute(
            "SELECT sequence FROM run_journal WHERE run_id=? ORDER BY position", (run_id,)
        )
    ]
    written = db.execute(
        "SELECT sequence FROM journal_entries WHERE run_id=? "
        "UNION ALL SELECT sequence FROM journal_archives WHERE run_id=?",
        (run_id, run_id),
    ).fetchone()
    return {
        "journal_opened": opened,
        "journal_written": written["sequence"] if written else None,
    }


def pin_journal(db, run_id: str, resident_id: str) -> None:
    """Admission records the exact entries the run opens with, newest first.

    Each pin carries both the entry digest and the digest of the document retention
    would archive it as, so the run keeps reading the same bytes after a later roll.
    """
    for position, row in enumerate(
        db.execute(
            "SELECT * FROM journal_entries WHERE resident_id=? ORDER BY sequence DESC LIMIT ?",
            (resident_id, CONTEXT_ENTRIES),
        ).fetchall()
    ):
        entry = checked_entry(row)
        archive = hashlib.sha256(entry_document(entry).encode("utf-8")).hexdigest()
        db.execute(
            "INSERT INTO run_journal VALUES (?,?,?,?,?,?)",
            (run_id, position, resident_id, entry["sequence"], row["sha256"], archive),
        )


def _pinned_entry(db, files: JournalFiles, resident_id: str, pin) -> dict:
    """A pinned entry keeps its exact bytes whether it still has a row or a file."""
    row = db.execute(
        "SELECT * FROM journal_entries WHERE resident_id=? AND sequence=?",
        (resident_id, pin["sequence"]),
    ).fetchone()
    if row is not None:
        entry = checked_entry(row)
        if row["sha256"] != pin["sha256"]:
            raise Refused("journal_entry_changed")
        return entry
    # Retention may have rolled the entry into its immutable file since admission.
    entry = files.entry(resident_id, pin["archive_sha256"])
    if (
        entry["sequence"] != pin["sequence"]
        or hashlib.sha256(entry["text"].encode("utf-8")).hexdigest() != pin["sha256"]
    ):
        raise Refused("journal_entry_changed")
    return entry


def run_journal(db, files: JournalFiles, run_id: str) -> list[dict]:
    """The pinned journal of one run, newest first; later entries never join it."""
    owner = db.execute("SELECT resident_id FROM runs WHERE id=?", (run_id,)).fetchone()
    if owner is None:
        raise Refused("run_not_found")
    entries = []
    for position, row in enumerate(
        db.execute("SELECT * FROM run_journal WHERE run_id=? ORDER BY position", (run_id,))
    ):
        if row["position"] != position:
            raise Refused("journal_order_changed")
        if row["resident_id"] != owner["resident_id"]:
            raise Refused("journal_run_mismatch")
        entry = _pinned_entry(db, files, row["resident_id"], row)
        entries.append({key: entry[key] for key in ("sequence", "run_id", "at", "text")})
    return entries
