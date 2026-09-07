"""Resident input grants and immutable admitted data within the caller's transaction."""

import hashlib
import json
from typing import TYPE_CHECKING

from hearth.inputs.catalog import read_input
from hearth.residents.models import Refused, identifier

if TYPE_CHECKING:
    from hearth.work.service import Hearth

MAX_SETS = 4


def _digest(value) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def initialize_selection(db, resident_id: str) -> None:
    db.execute("INSERT INTO input_selections VALUES (?,0,0,?)", (resident_id, _digest([])))


def read_selection(db, resident_id: str) -> dict:
    identifier(resident_id)
    if not db.execute("SELECT 1 FROM residents WHERE id=?", (resident_id,)).fetchone():
        raise Refused("resident_not_found")
    header = db.execute(
        "SELECT * FROM input_selections WHERE resident_id=?", (resident_id,)
    ).fetchone()
    if header is None:
        raise Refused("input_selection_missing")
    refs = []
    for position, row in enumerate(
        db.execute(
            "SELECT * FROM selected_inputs WHERE resident_id=? ORDER BY position", (resident_id,)
        )
    ):
        if row["position"] != position:
            raise Refused("input_selection_changed")
        refs.append({"input_set_id": row["input_set_id"]})
    if len(refs) != header["count"] or _digest(refs) != header["sha256"]:
        raise Refused("input_selection_changed")
    return dict(
        resident_id=resident_id,
        revision=header["revision"],
        input_sets=[read_input(db, ref["input_set_id"]) for ref in refs],
    )


def save_selection(
    db,
    resident_id: str,
    refs: list[dict],
    *,
    expected_revision: int,
    command_id: str,
    actor: str,
    now: int,
) -> dict:
    """Caller authenticates actor, scopes grants and owns the same write transaction."""
    from hearth.residents.lifecycle import check_not_archived
    from hearth.work.service import _audit

    check_not_archived(db, resident_id)
    identifier(resident_id)
    identifier(command_id)
    identifier(actor)
    if type(expected_revision) is not int or expected_revision < 0:
        raise Refused("invalid_revision")
    if not isinstance(refs, list) or len(refs) > MAX_SETS:
        raise Refused("input_selection_limit")
    for ref in refs:
        if not isinstance(ref, dict) or set(ref) != {"input_set_id"}:
            raise Refused("invalid_input_selection")
        identifier(ref["input_set_id"])
    if len({ref["input_set_id"] for ref in refs}) != len(refs):
        raise Refused("duplicate_input_selection")
    payload_digest = _digest([resident_id, refs, expected_revision, actor])
    old = db.execute(
        "SELECT * FROM input_selection_operations WHERE command_id=?", (command_id,)
    ).fetchone()
    if old:
        if old["payload_digest"] != payload_digest:
            raise Refused("idempotency_key_conflict")
        return json.loads(old["receipt"])
    # Validate selection identity without needing removed input content to be healthy.
    header = db.execute(
        "SELECT * FROM input_selections WHERE resident_id=?", (resident_id,)
    ).fetchone()
    if header is None:
        raise Refused("input_selection_missing")
    if header["revision"] != expected_revision:
        raise Refused("revision_conflict")
    for ref in refs:
        read_input(db, ref["input_set_id"])
    revision = expected_revision + 1
    db.execute(
        "UPDATE input_selections SET revision=?,count=?,sha256=? WHERE resident_id=?",
        (revision, len(refs), _digest(refs), resident_id),
    )
    db.execute("DELETE FROM selected_inputs WHERE resident_id=?", (resident_id,))
    for position, ref in enumerate(refs):
        db.execute(
            "INSERT INTO selected_inputs VALUES (?,?,?)",
            (resident_id, position, ref["input_set_id"]),
        )
    receipt = dict(
        command_id=command_id,
        resident_id=resident_id,
        revision=revision,
        actor=actor,
        recorded_at=now,
    )
    db.execute(
        "INSERT INTO input_selection_operations VALUES (?,?,?)",
        (command_id, payload_digest, json.dumps(receipt)),
    )
    _audit(db, "resident.inputs_saved", resident_id, now, receipt)
    return receipt


def _pin_identity(entries: list[dict]) -> str:
    return _digest(
        [{key: entry[key] for key in ("input_set_id", "revision", "sha256")} for entry in entries]
    )


def pin_inputs(db, run_id: str, resident_id: str) -> None:
    selected = read_selection(db, resident_id)
    entries = selected["input_sets"]
    from hearth.skills.evaluation import case_binding

    evaluation = case_binding(db, run_id, resident_id)
    if evaluation is not None:
        entries = [evaluation["input"]]
        selected = {"revision": 0}
    db.execute(
        "INSERT INTO run_input_sets VALUES (?,?,?,?,?)",
        (run_id, resident_id, selected["revision"], len(entries), _pin_identity(entries)),
    )
    for position, entry in enumerate(entries):
        db.execute(
            "INSERT INTO run_inputs VALUES (?,?,?,?,?)",
            (run_id, position, entry["input_set_id"], entry["revision"], entry["sha256"]),
        )


def run_inputs(db, run_id: str) -> list[dict]:
    header = db.execute(
        "SELECT p.*,r.resident_id AS owner FROM runs r LEFT JOIN run_input_sets p "
        "ON p.run_id=r.id WHERE r.id=?",
        (run_id,),
    ).fetchone()
    if header is None or header["run_id"] is None:
        raise Refused("input_pin_set_missing")
    if header["resident_id"] != header["owner"]:
        raise Refused("input_run_mismatch")
    entries = []
    for position, row in enumerate(
        db.execute("SELECT * FROM run_inputs WHERE run_id=? ORDER BY position", (run_id,))
    ):
        if row["position"] != position:
            raise Refused("input_order_changed")
        content = read_input(db, row["input_set_id"], row["input_revision"])
        if content["sha256"] != row["sha256"]:
            raise Refused("input_content_changed")
        entries.append(
            {
                key: content[key]
                for key in ("input_set_id", "revision", "name", "notes", "sha256", "synthetic")
            }
        )
    if len(entries) != header["count"] or _pin_identity(entries) != header["sha256"]:
        raise Refused("input_pin_set_changed")
    return entries


def input_summary(db, owner: str, *, run: bool = False) -> dict:
    try:
        result = {"input_sets": run_inputs(db, owner)} if run else read_selection(db, owner)
        return dict(
            input_sets=[
                {key: value for key, value in entry.items() if key != "notes"}
                for entry in result["input_sets"]
            ],
            input_revision=result.get("revision"),
            inputs_error=None,
            input_state="configured" if result["input_sets"] else "empty",
        )
    except Refused as error:
        return dict(
            input_sets=[], input_revision=None, inputs_error=error.code, input_state="unavailable"
        )


class InputSelection:
    def __init__(self, hearth: Hearth):
        self.hearth = hearth

    def read(self, resident_id: str) -> dict:
        with self.hearth.database.transaction() as db:
            return read_selection(db, resident_id)

    def save(
        self,
        resident_id: str,
        refs: list[dict],
        *,
        expected_revision: int,
        command_id: str,
        actor: str,
    ) -> dict:
        with self.hearth.database.transaction(write=True) as db:
            return save_selection(
                db,
                resident_id,
                refs,
                expected_revision=expected_revision,
                command_id=command_id,
                actor=actor,
                now=int(self.hearth.clock()),
            )
