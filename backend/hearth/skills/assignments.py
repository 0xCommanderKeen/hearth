"""Ordered exact-revision assignments and immutable run inputs in the owning transaction."""

import hashlib
import json
from typing import TYPE_CHECKING

from hearth.residents.models import Refused, identifier

if TYPE_CHECKING:
    from hearth.work.service import Hearth

MAX_SKILLS = 8
MAX_TEXT_BYTES = 131_072


def exact_skill(db, skill_id: str, revision: int) -> dict:
    from hearth.skills.catalog import checked_revision

    identifier(skill_id)
    if type(revision) is not int or revision < 1:
        raise Refused("invalid_skill_revision")
    row = db.execute(
        "SELECT * FROM skill_revisions WHERE skill_id=? AND revision=?", (skill_id, revision)
    ).fetchone()
    if row is None:
        raise Refused("skill_revision_missing")
    return checked_revision(row)


def _identity(entries: list[dict]) -> str:
    return hashlib.sha256(
        json.dumps(
            [{key: entry[key] for key in ("skill_id", "revision", "sha256")} for entry in entries],
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()


def _read_set(db, owner: str, *, run: bool) -> dict:
    sets, rows, key = (
        ("run_skill_sets", "run_skills", "run_id")
        if run
        else ("resident_skill_sets", "assigned_skills", "resident_id")
    )
    header = db.execute(f"SELECT * FROM {sets} WHERE {key}=?", (owner,)).fetchone()
    if header is None:
        if run:
            raise Refused("skill_pin_set_missing")
        if db.execute(f"SELECT 1 FROM {rows} WHERE {key}=?", (owner,)).fetchone():
            raise Refused("skill_set_missing")
        return {"revision": 0, "skills": [], "sha256": _identity([])}
    entries = []
    for position, row in enumerate(
        db.execute(f"SELECT * FROM {rows} WHERE {key}=? ORDER BY position", (owner,))
    ):
        if row["position"] != position:
            raise Refused("skill_order_changed")
        content = exact_skill(db, row["skill_id"], row["skill_revision"])
        if content["sha256"] != row["sha256"]:
            raise Refused("skill_content_changed")
        entries.append(
            {
                key: content[key]
                for key in ("skill_id", "revision", "name", "description", "instructions", "sha256")
            }
        )
    if len(entries) != header["count"] or _identity(entries) != header["sha256"]:
        raise Refused("skill_set_changed")
    return {"revision": header["revision"], "skills": entries, "sha256": header["sha256"]}


def read_assignments(db, resident_id: str) -> dict:
    identifier(resident_id)
    if not db.execute("SELECT 1 FROM residents WHERE id=?", (resident_id,)).fetchone():
        raise Refused("resident_not_found")
    result = _read_set(db, resident_id, run=False)
    for entry in result["skills"]:
        latest = db.execute(
            "SELECT r.revision,r.status FROM skills s JOIN skill_revisions r ON "
            "r.skill_id=s.id AND r.revision=s.revision WHERE s.id=?",
            (entry["skill_id"],),
        ).fetchone()
        if latest is None:
            raise Refused("skill_revision_missing")
        entry.update(latest_revision=latest["revision"], catalog_status=latest["status"])
    return {"resident_id": resident_id, **result}


def save_assignments(
    db,
    resident_id: str,
    entries: list[dict],
    *,
    expected_revision: int,
    actor: str,
    command_id: str,
    now: int,
) -> dict:
    """Caller authenticates actor and owns transaction, including provisioning authority."""
    from hearth.work.service import _audit

    identifier(resident_id)
    identifier(actor)
    identifier(command_id)
    if type(expected_revision) is not int or expected_revision < 0:
        raise Refused("invalid_revision")
    if not isinstance(entries, list) or len(entries) > MAX_SKILLS:
        raise Refused("skill_assignment_limit")
    for entry in entries:
        if not isinstance(entry, dict) or set(entry) != {"skill_id", "revision"}:
            raise Refused("invalid_skill_assignment")
        identifier(entry["skill_id"])
        if type(entry["revision"]) is not int or entry["revision"] < 1:
            raise Refused("invalid_skill_revision")
    if len({entry["skill_id"] for entry in entries}) != len(entries):
        raise Refused("duplicate_skill_assignment")
    payload_digest = hashlib.sha256(
        json.dumps([resident_id, entries, expected_revision, actor], sort_keys=True).encode()
    ).hexdigest()
    previous = db.execute(
        "SELECT * FROM assignment_operations WHERE command_id=?", (command_id,)
    ).fetchone()
    if previous:
        if previous["payload_digest"] != payload_digest:
            raise Refused("idempotency_key_conflict")
        return json.loads(previous["receipt"])
    current = read_assignments(db, resident_id)
    if current["revision"] != expected_revision:
        raise Refused("revision_conflict")
    old = {(entry["skill_id"], entry["revision"]) for entry in current["skills"]}
    validated = []
    for entry in entries:
        skill = exact_skill(db, entry["skill_id"], entry["revision"])
        if skill["status"] == "draft":
            raise Refused("skill_not_active")
        latest = db.execute(
            "SELECT r.status FROM skills s JOIN skill_revisions r ON r.skill_id=s.id AND "
            "r.revision=s.revision WHERE s.id=?",
            (entry["skill_id"],),
        ).fetchone()
        if (latest is None or latest["status"] == "archived" or skill["status"] == "archived") and (
            entry["skill_id"],
            entry["revision"],
        ) not in old:
            raise Refused("skill_archived")
        validated.append(skill)
    if sum(len(entry["instructions"].encode()) for entry in validated) > MAX_TEXT_BYTES:
        raise Refused("skill_assignment_text_limit")
    revision = expected_revision + 1
    digest = _identity(validated)
    db.execute(
        "INSERT INTO resident_skill_sets VALUES (?,?,?,?) ON CONFLICT(resident_id) DO "
        "UPDATE SET "
        "revision=excluded.revision,count=excluded.count,sha256=excluded.sha256",
        (resident_id, revision, len(validated), digest),
    )
    db.execute("DELETE FROM assigned_skills WHERE resident_id=?", (resident_id,))
    for position, entry in enumerate(validated):
        db.execute(
            "INSERT INTO assigned_skills VALUES (?,?,?,?,?)",
            (resident_id, position, entry["skill_id"], entry["revision"], entry["sha256"]),
        )
    receipt = {
        "command_id": command_id,
        "resident_id": resident_id,
        "revision": revision,
        "sha256": digest,
        "actor": actor,
        "recorded_at": now,
    }
    db.execute(
        "INSERT INTO assignment_operations VALUES (?,?,?)",
        (command_id, payload_digest, json.dumps(receipt, sort_keys=True)),
    )
    _audit(db, "resident.skills_saved", resident_id, now, receipt)
    return receipt


def pin_skills(db, run_id: str, resident_id: str) -> None:
    assigned: dict = read_assignments(db, resident_id)
    from hearth.skills.evaluation import case_binding

    evaluation = case_binding(db, run_id, resident_id)
    if evaluation is not None:
        entries = [evaluation["candidate"]]
        assigned = {"revision": 0, "skills": entries, "sha256": _identity(entries)}
        db.execute(
            "UPDATE skill_validation_cases SET run_id=? "
            "WHERE task_id=(SELECT task_id FROM runs WHERE id=?)",
            (run_id, run_id),
        )
    db.execute(
        "INSERT INTO run_skill_sets VALUES (?,?,?,?,?)",
        (run_id, resident_id, assigned["revision"], len(assigned["skills"]), assigned["sha256"]),
    )
    for position, entry in enumerate(assigned["skills"]):
        db.execute(
            "INSERT INTO run_skills VALUES (?,?,?,?,?)",
            (run_id, position, entry["skill_id"], entry["revision"], entry["sha256"]),
        )


def run_skills(db, run_id: str) -> list[dict]:
    row = db.execute(
        "SELECT r.resident_id, s.resident_id AS pinned FROM runs r LEFT JOIN "
        "run_skill_sets s ON s.run_id=r.id WHERE r.id=?",
        (run_id,),
    ).fetchone()
    if row is None or row["resident_id"] != row["pinned"]:
        raise Refused("skill_run_mismatch")
    return _read_set(db, run_id, run=True)["skills"]


class Assignments:
    def __init__(self, hearth: Hearth):
        self.hearth = hearth

    def read(self, resident_id: str) -> dict:
        with self.hearth.database.transaction() as db:
            return read_assignments(db, resident_id)

    def save(
        self,
        resident_id: str,
        entries: list[dict],
        *,
        expected_revision: int,
        actor: str,
        command_id: str,
    ) -> dict:
        with self.hearth.database.transaction(write=True) as db:
            return save_assignments(
                db,
                resident_id,
                entries,
                expected_revision=expected_revision,
                actor=actor,
                command_id=command_id,
                now=int(self.hearth.clock()),
            )

    def users(self, skill_id: str) -> list[dict]:
        identifier(skill_id)
        with self.hearth.database.transaction() as db:
            if not db.execute("SELECT 1 FROM skills WHERE id=?", (skill_id,)).fetchone():
                raise Refused("skill_not_found")
            return [
                dict(row)
                for row in db.execute(
                    "SELECT a.resident_id,d.name,a.skill_revision AS revision,a.position FROM "
                    "assigned_skills a JOIN residents r ON r.id=a.resident_id JOIN declarations d "
                    "ON d.resident_id=r.id AND d.revision=r.revision WHERE a.skill_id=? ORDER BY "
                    "d.name,r.id",
                    (skill_id,),
                )
            ]


def skill_summary(db, owner: str, *, run: bool = False) -> dict:
    """Keep damaged inputs inspectable without making the shared snapshot unavailable."""
    try:
        result = {"skills": run_skills(db, owner)} if run else read_assignments(db, owner)
        return {
            "skills": [
                {key: value for key, value in entry.items() if key != "instructions"}
                for entry in result["skills"]
            ],
            "skills_error": None,
            "assignment_revision": result.get("revision"),
        }
    except Refused as error:
        return {"skills": [], "skills_error": error.code, "assignment_revision": None}
