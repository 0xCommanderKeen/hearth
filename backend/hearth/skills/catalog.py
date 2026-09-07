"""One transaction owns shared skill content, immutable revisions and operation facts."""

import hashlib
import json
import uuid

from hearth.residents.models import Refused, bounded_text, identifier
from hearth.skills.authoring import check_editor, decorate, manifest, read_authoring, record
from hearth.work.service import Hearth, _audit


def content_digest(content: dict) -> str:
    """Bind the exact reusable text independently of mutable catalog metadata."""
    payload = {key: content[key] for key in ("name", "description", "instructions")}
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def checked_revision(row) -> dict:
    result = dict(row)
    if result["sha256"] != content_digest(result):
        raise Refused("skill_content_changed")
    return result


class Skills:
    def __init__(self, hearth: Hearth):
        self.hearth = hearth

    def search(self, *, query: str = "", include_archived: bool = False) -> list[dict]:
        if not isinstance(query, str) or len(query) > 200:
            raise Refused("invalid_skill_query")
        with self.hearth.database.transaction() as db:
            rows = db.execute(
                "SELECT r.*, s.created_by, s.created_at FROM skills s JOIN skill_revisions r "
                "ON r.skill_id=s.id AND r.revision=s.revision ORDER BY r.name COLLATE NOCASE, s.id"
            ).fetchall()
            return [
                decorate(db, checked_revision(row))
                for row in rows
                if (include_archived or row["status"] != "archived")
                and query.casefold() in (row["name"] + " " + row["description"]).casefold()
            ]

    def read(self, skill_id: str, *, revision: int | None = None) -> dict:
        identifier(skill_id)
        if revision is not None and (type(revision) is not int or revision < 1):
            raise Refused("invalid_revision")
        with self.hearth.database.transaction() as db:
            row = db.execute(
                "SELECT r.*, s.created_by, s.created_at FROM skills s JOIN skill_revisions r "
                "ON r.skill_id=s.id AND r.revision=COALESCE(?, s.revision) WHERE s.id=?",
                (revision, skill_id),
            ).fetchone()
            if row is None:
                raise Refused("skill_not_found")
            return decorate(db, checked_revision(row))

    def history(self, skill_id: str) -> list[dict]:
        identifier(skill_id)
        with self.hearth.database.transaction() as db:
            rows = db.execute(
                "SELECT r.*, s.created_by, s.created_at FROM skill_revisions r "
                "JOIN skills s ON s.id=r.skill_id WHERE s.id=? ORDER BY r.revision DESC",
                (skill_id,),
            ).fetchall()
            if not rows:
                raise Refused("skill_not_found")
            return [decorate(db, checked_revision(row)) for row in rows]

    def receipt(self, command_id: str) -> dict:
        identifier(command_id)
        with self.hearth.database.transaction() as db:
            row = db.execute(
                "SELECT receipt FROM skill_operations WHERE command_id=?", (command_id,)
            ).fetchone()
            if row is None:
                raise Refused("skill_operation_not_found")
            return json.loads(row[0])

    def save(
        self,
        command_id: str,
        *,
        name: str,
        description: str,
        instructions: str,
        actor: str,
        skill_id: str | None = None,
        expected_revision: int = 0,
        authoring: dict | None = None,
    ) -> dict:
        bounded_text(name, 120, "invalid_skill_name")
        bounded_text(description, 2000, "invalid_skill_description")
        bounded_text(instructions, 32000, "invalid_skill_instructions")
        return self._change(
            command_id,
            skill_id,
            expected_revision,
            actor,
            {"name": name, "description": description, "instructions": instructions},
            authoring=authoring,
        )

    def archive(
        self, command_id: str, skill_id: str, *, expected_revision: int, actor: str
    ) -> dict:
        return self._change(command_id, skill_id, expected_revision, actor, None)

    def _change(
        self,
        command_id: str,
        skill_id: str | None,
        expected_revision: int,
        actor: str,
        content: dict | None,
        *,
        authoring: dict | None = None,
    ) -> dict:
        with self.hearth.database.transaction(write=True) as db:
            return self.change_in_transaction(
                db, command_id, skill_id, expected_revision, actor, content, authoring=authoring
            )

    def save_in_transaction(
        self,
        db,
        command_id: str,
        *,
        name: str,
        description: str,
        instructions: str,
        actor: str,
        skill_id: str | None = None,
        expected_revision: int = 0,
        authoring: dict | None = None,
    ) -> dict:
        bounded_text(name, 120, "invalid_skill_name")
        bounded_text(description, 2000, "invalid_skill_description")
        bounded_text(instructions, 32000, "invalid_skill_instructions")
        return self.change_in_transaction(
            db,
            command_id,
            skill_id,
            expected_revision,
            actor,
            {"name": name, "description": description, "instructions": instructions},
            authoring=authoring,
        )

    def publish_in_transaction(
        self, db, command_id, skill_id, expected_revision, validation_id, *, actor
    ):
        return self.change_in_transaction(
            db,
            command_id,
            skill_id,
            expected_revision,
            actor,
            None,
            publication=validation_id,
        )

    def publish(self, command_id, skill_id, expected_revision, validation_id, *, actor="operator"):
        with self.hearth.database.transaction(write=True) as db:
            return self.publish_in_transaction(
                db, command_id, skill_id, expected_revision, validation_id, actor=actor
            )

    def change_in_transaction(
        self,
        db,
        command_id,
        skill_id,
        expected_revision,
        actor,
        content,
        *,
        authoring=None,
        publication=None,
    ):
        identifier(command_id)
        identifier(actor)
        if skill_id is not None:
            identifier(skill_id)
            check_editor(db, skill_id, actor)
        if authoring is not None:
            authoring = manifest(authoring)
        if type(expected_revision) is not int or expected_revision < 0:
            raise Refused("invalid_revision")
        if publication is not None:
            from hearth.skills.validation import checked_publication

            candidate = checked_publication(
                db, self.hearth, skill_id, expected_revision, publication
            )
            content = {key: candidate[key] for key in ("name", "description", "instructions")}
        operation = (
            "publish"
            if publication is not None
            else "archive"
            if content is None
            else "create"
            if skill_id is None
            else "save"
        )
        payload = {
            "operation": operation,
            "skill_id": skill_id,
            "expected_revision": expected_revision,
            "actor": actor,
            "content": content,
        }
        if authoring is not None:
            payload["authoring"] = authoring
        if publication is not None:
            payload["validation_id"] = publication
        digest = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
        previous = db.execute(
            "SELECT * FROM skill_operations WHERE command_id=?", (command_id,)
        ).fetchone()
        if previous:
            if previous["payload_digest"] != digest:
                raise Refused("idempotency_key_conflict")
            return json.loads(previous["receipt"])
        now = int(self.hearth.clock())
        if skill_id is None:
            if expected_revision != 0 or content is None:
                raise Refused("invalid_revision")
            skill_id = str(uuid.uuid4())
            current = 0
            db.execute("INSERT INTO skills VALUES (?, 1, ?, ?)", (skill_id, actor, now))
        else:
            row = db.execute(
                "SELECT r.* FROM skills s JOIN skill_revisions r "
                "ON r.skill_id=s.id AND r.revision=s.revision WHERE s.id=?",
                (skill_id,),
            ).fetchone()
            if row is None:
                raise Refused("skill_not_found")
            checked_revision(row)
            current = row["revision"]
            if current != expected_revision:
                raise Refused("revision_conflict")
            if row["status"] == "archived":
                raise Refused("skill_archived")
            if authoring is None and operation != "archive":
                previous_authoring = read_authoring(db, skill_id, current)
                if previous_authoring is not None:
                    authoring = {"examples": previous_authoring["examples"]}
            if content is None:
                content = {key: row[key] for key in ("name", "description", "instructions")}
            db.execute("UPDATE skills SET revision=? WHERE id=?", (current + 1, skill_id))
        revision = current + 1
        db.execute(
            "INSERT INTO skill_revisions VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                skill_id,
                revision,
                content["name"],
                content["description"],
                content["instructions"],
                "archived"
                if operation == "archive"
                else "draft"
                if authoring is not None and publication is None
                else "active",
                actor,
                now,
                content_digest(content),
            ),
        )
        if authoring is not None:
            record(db, skill_id, revision, content["instructions"], authoring)
        if publication is not None:
            db.execute(
                "INSERT INTO skill_publications VALUES (?,?,?,?,?)",
                (
                    skill_id,
                    revision,
                    expected_revision,
                    publication,
                    content_digest(content),
                ),
            )
        receipt = {
            "command_id": command_id,
            "skill_id": skill_id,
            "revision": revision,
            "operation": operation,
            "recorded_at": now,
            "actor": actor,
        }
        db.execute(
            "INSERT INTO skill_operations VALUES (?, ?, ?, ?, ?)",
            (command_id, digest, skill_id, revision, json.dumps(receipt, sort_keys=True)),
        )
        _audit(db, "skill." + operation, skill_id, now, receipt)
        return receipt
