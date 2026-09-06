"""Bounded synthetic notes and revision receipts in the owning SQLite transaction."""

import hashlib
import json
import uuid
from typing import TYPE_CHECKING

from hearth.residents.models import Refused, bounded_text, identifier

if TYPE_CHECKING:
    from hearth.work.service import Hearth

MAX_NOTES = 32
MAX_NOTE = 4000
MAX_SET_BYTES = 32768
BUILTIN_INPUT = "synthetic-reader-notes"
BUILTIN_NOTES = [
    "Synthetic note: drafted the Hearth foundation.",
    "Synthetic note: task submission survives retries.",
    "Synthetic note: exercise cancellation and recovery next.",
]


def content_digest(name: str, notes: list[str]) -> str:
    return hashlib.sha256(
        json.dumps({"name": name, "notes": notes}, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def validate_content(name, notes) -> None:
    bounded_text(name, 120, "invalid_input_name")
    if not isinstance(notes, list) or len(notes) > MAX_NOTES:
        raise Refused("invalid_input_notes")
    for note in notes:
        bounded_text(note, MAX_NOTE, "invalid_input_note")
    try:
        size = len(name.encode()) + sum(len(note.encode()) for note in notes)
    except UnicodeEncodeError:
        raise Refused("invalid_input_encoding") from None
    if size > MAX_SET_BYTES:
        raise Refused("input_notes_too_large")


def checked_input(row) -> dict:
    if row is None:
        raise Refused("input_revision_missing")
    try:
        result = dict(row)
        notes = json.loads(result["notes"])
        validate_content(result["name"], notes)
        if result["sha256"] != content_digest(result["name"], notes):
            raise Refused("input_content_changed")
    except ValueError, TypeError, KeyError, RecursionError:
        raise Refused("input_content_changed") from None
    return result | {"notes": notes, "synthetic": True}


def read_input(db, input_set_id: str, revision: int | None = None) -> dict:
    identifier(input_set_id)
    if revision is not None and (type(revision) is not int or revision < 1):
        raise Refused("invalid_input_revision")
    row = db.execute(
        "SELECT r.*,s.created_by,s.created_at FROM input_sets s JOIN input_revisions r "
        "ON r.input_set_id=s.id AND r.revision=COALESCE(?,s.revision) WHERE s.id=?",
        (revision, input_set_id),
    ).fetchone()
    return checked_input(row)


def list_inputs(db) -> list[dict]:
    return [
        read_input(db, row["id"]) for row in db.execute("SELECT id FROM input_sets ORDER BY id")
    ]


class Inputs:
    def __init__(self, hearth: Hearth):
        self.hearth = hearth

    def read(self, input_set_id: str, *, revision: int | None = None) -> dict:
        with self.hearth.database.transaction() as db:
            return read_input(db, input_set_id, revision)

    def catalog(self) -> list[dict]:
        with self.hearth.database.transaction() as db:
            return list_inputs(db)

    def save(
        self,
        command_id: str,
        *,
        name: str,
        notes: list[str],
        actor: str,
        input_set_id: str | None = None,
        expected_revision: int = 0,
    ) -> dict:
        with self.hearth.database.transaction(write=True) as db:
            return self.save_in_transaction(
                db,
                command_id,
                name=name,
                notes=notes,
                actor=actor,
                input_set_id=input_set_id,
                expected_revision=expected_revision,
            )

    def save_in_transaction(
        self,
        db,
        command_id: str,
        *,
        name: str,
        notes: list[str],
        actor: str,
        input_set_id: str | None = None,
        expected_revision: int = 0,
    ) -> dict:
        from hearth.work.service import _audit

        identifier(command_id)
        identifier(actor)
        if input_set_id is not None:
            identifier(input_set_id)
        if type(expected_revision) is not int or expected_revision < 0:
            raise Refused("invalid_revision")
        validate_content(name, notes)
        digest = hashlib.sha256(
            json.dumps(
                [input_set_id, expected_revision, name, notes, actor], sort_keys=True
            ).encode()
        ).hexdigest()
        old = db.execute(
            "SELECT * FROM input_operations WHERE command_id=?", (command_id,)
        ).fetchone()
        if old:
            if old["payload_digest"] != digest:
                raise Refused("idempotency_key_conflict")
            return json.loads(old["receipt"])
        current = db.execute(
            "SELECT revision FROM input_sets WHERE id=?", (input_set_id,)
        ).fetchone()
        if (current[0] if current else 0) != expected_revision:
            raise Refused("revision_conflict")
        now = int(self.hearth.clock())
        if current:
            assert input_set_id is not None
            read_input(db, input_set_id)
            db.execute(
                "UPDATE input_sets SET revision=? WHERE id=?", (expected_revision + 1, input_set_id)
            )
        else:
            input_set_id = input_set_id or str(uuid.uuid4())
            db.execute("INSERT INTO input_sets VALUES (?,1,?,?)", (input_set_id, actor, now))
        assert input_set_id is not None
        revision = expected_revision + 1
        db.execute(
            "INSERT INTO input_revisions VALUES (?,?,?,?,?,?,?)",
            (
                input_set_id,
                revision,
                name,
                json.dumps(notes),
                content_digest(name, notes),
                actor,
                now,
            ),
        )
        receipt = dict(
            command_id=command_id,
            input_set_id=input_set_id,
            revision=revision,
            actor=actor,
            recorded_at=now,
        )
        db.execute(
            "INSERT INTO input_operations VALUES (?,?,?)", (command_id, digest, json.dumps(receipt))
        )
        _audit(db, "input.saved", input_set_id, now, receipt)
        return receipt

    def seed_in_transaction(self, db) -> str:
        self.save_in_transaction(
            db,
            "seed-synthetic-reader-notes",
            input_set_id=BUILTIN_INPUT,
            name="Synthetic Reader example notes",
            notes=BUILTIN_NOTES,
            actor="operator",
        )
        return BUILTIN_INPUT
