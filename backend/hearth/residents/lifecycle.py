"""Resident readiness and ownership, separate from observed execution and safety holds."""

import hashlib
import json

from hearth.residents.models import Refused, identifier


def _digest(value: dict) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _checked_history(row, resident_id: str, revision: int) -> dict:
    try:
        value = json.loads(row["content"])
        if (
            set(value)
            != {
                "resident_id",
                "revision",
                "state",
                "manager",
                "actor",
                "originating_run_id",
                "updated_at",
            }
            or value["resident_id"] != resident_id
            or type(value["revision"]) is not int
            or value["revision"] != revision
            or value["state"] not in {"ready", "paused", "archived"}
            or type(value["updated_at"]) is not int
            or row["sha256"] != _digest(value)
        ):
            raise ValueError
        identifier(value["manager"])
        identifier(value["actor"])
        if value["originating_run_id"] is not None:
            identifier(value["originating_run_id"])
    except ValueError, TypeError, KeyError:
        raise Refused("resident_lifecycle_corrupt") from None
    return value


def read_lifecycle(db, resident_id: str) -> dict:
    identifier(resident_id)
    row = db.execute(
        "SELECT l.revision,h.content,h.sha256,"
        "(SELECT MAX(revision) FROM resident_lifecycle_history WHERE resident_id=l.resident_id) "
        "AS latest FROM resident_lifecycle l "
        "LEFT JOIN resident_lifecycle_history h ON h.resident_id=l.resident_id "
        "AND h.revision=l.revision WHERE l.resident_id=?",
        (resident_id,),
    ).fetchone()
    if row is None:
        if not db.execute("SELECT 1 FROM residents WHERE id=?", (resident_id,)).fetchone():
            raise Refused("resident_not_found")
        raise Refused("resident_lifecycle_missing")
    if row["revision"] != row["latest"]:
        raise Refused("resident_lifecycle_corrupt")
    return _checked_history(row, resident_id, row["revision"])


def record_lifecycle(
    db,
    resident_id: str,
    *,
    revision: int,
    state: str,
    manager: str,
    actor: str,
    originating_run_id: str | None,
    now: int,
) -> dict:
    """Trusted creation/maintenance callers own authorization and the SQLite writer."""
    from hearth.work.service import _audit

    result = dict(
        resident_id=resident_id,
        revision=revision,
        state=state,
        manager=manager,
        actor=actor,
        originating_run_id=originating_run_id,
        updated_at=now,
    )
    db.execute(
        "INSERT INTO resident_lifecycle_history VALUES (?,?,?,?)",
        (resident_id, revision, json.dumps(result, sort_keys=True), _digest(result)),
    )
    db.execute(
        "INSERT INTO resident_lifecycle VALUES (?,?) ON CONFLICT(resident_id) "
        "DO UPDATE SET revision=excluded.revision",
        (resident_id, revision),
    )
    # Initial ready state commits with the enclosing resident.saved fact.
    if revision:
        _audit(db, "resident.lifecycle_saved", resident_id, now, result)
    return result


def check_ready(db, resident_id: str) -> None:
    state = read_lifecycle(db, resident_id)["state"]
    if state != "ready":
        raise Refused("resident_" + state)


def check_not_archived(db, resident_id: str) -> None:
    if read_lifecycle(db, resident_id)["state"] == "archived":
        raise Refused("resident_archived")


def lifecycle_summary(db, resident_id: str) -> dict:
    try:
        return read_lifecycle(db, resident_id)
    except Refused as error:
        return {"resident_id": resident_id, "state": "unavailable", "error": error.code}


def validate_lifecycle(db) -> None:
    for resident in db.execute("SELECT id FROM residents"):
        read_lifecycle(db, resident[0])
        previous = None
        for revision, row in enumerate(
            db.execute(
                "SELECT * FROM resident_lifecycle_history WHERE resident_id=? ORDER BY revision",
                (resident[0],),
            )
        ):
            current = _checked_history(row, resident[0], revision)
            if (
                previous is None and (current["state"], current["manager"]) != ("ready", "operator")
            ) or (
                previous is not None
                and previous["state"] == "archived"
                and current["state"] != "archived"
            ):
                raise Refused("resident_lifecycle_corrupt")
            for identity in (current["actor"], current["manager"]):
                if (
                    identity != "operator"
                    and not db.execute("SELECT 1 FROM residents WHERE id=?", (identity,)).fetchone()
                ):
                    raise Refused("resident_lifecycle_owner_missing")
            if (
                current["originating_run_id"] is not None
                and not db.execute(
                    "SELECT 1 FROM runs WHERE id=? AND resident_id=?",
                    (current["originating_run_id"], current["actor"]),
                ).fetchone()
            ):
                raise Refused("resident_lifecycle_origin_changed")
            previous = current
