"""Letters: one resident's task addressed to another, with Hearth as the sole arbiter.

A letter is an ordinary task. What is new is the address — who sent it, from which run
and task, the root the chain rolls up to, how many hops in it is and when it goes stale
— and the two permissions that have to meet for it to exist at all: the sender's grant
capability `send_letters` and the receiver's declared `letters.accept` door. Neither
side can waive the other, and neither is written by instruction text.

Depth and lineage are read from the sender's own admitted run and task. Nothing a caller
says about a parent is trusted, so a forged or omitted parent cannot lengthen a chain,
revisit a resident or attribute cost to the wrong origin.
"""

from hearth.authority.household import read_letter_policy
from hearth.residents.models import Refused, bounded_text, identifier
from hearth.work.service import _audit, _queue_task

MAX_TITLE = 200
MAX_DETAIL = 8_000


def _chain(db, task_id: str) -> list[str]:
    """The residents a chain has already visited, oldest first, ending at this task's own.

    Read from the stored lineage, never from a caller's claim. A task that is not a
    letter is the start of a chain and names one resident: whoever is working it. The
    walk stops at a task it has already seen, so lineage edited in a copied file cannot
    hold the reader in a loop; the caller's own checks then refuse the row.
    """
    visited: list[str] = []
    seen: set[str] = set()
    current: str | None = task_id
    while current is not None and current not in seen:
        seen.add(current)
        task = db.execute("SELECT resident_id FROM tasks WHERE id=?", (current,)).fetchone()
        if task is None:
            raise Refused("task_not_found")
        visited.append(task["resident_id"])
        letter = db.execute(
            "SELECT sender_resident_id,parent_task_id FROM letters WHERE task_id=?", (current,)
        ).fetchone()
        if letter is None:
            break
        current = letter["parent_task_id"]
        if current is None:
            # An operator-sent letter has no parent task; its sender still walked here.
            visited.append(letter["sender_resident_id"])
            break
    visited.reverse()
    return visited


def _lineage(db, task_id: str) -> tuple[str, int]:
    """The root task this chain rolls up to and the depth one more hop would reach."""
    letter = db.execute(
        "SELECT root_task_id,depth FROM letters WHERE task_id=?", (task_id,)
    ).fetchone()
    if letter is None:
        return task_id, 1
    return letter["root_task_id"], letter["depth"] + 1


def send_letter(
    db,
    hearth,
    *,
    sender_run: str,
    to: str,
    title: str,
    detail: str,
    operation_id: str,
    expires_at: int | None = None,
) -> dict:
    """Queue a letter from the resident of `sender_run` to `to`, or refuse and write nothing.

    Idempotent on `operation_id` in the sending resident's own operation namespace: the
    same payload replays the original receipt, a changed one conflicts. The replay check
    comes before the guards, so a retry recovers the letter that was actually sent
    rather than being refused by a door that closed in between.
    """
    from hearth.management.authority import digest, read_grant
    from hearth.management.tools import _existing_operation, _record_operation
    from hearth.residents.lifecycle import read_lifecycle

    identifier(sender_run)
    identifier(to)
    identifier(operation_id)
    bounded_text(title, MAX_TITLE, "invalid_letter_title")
    bounded_text(detail, MAX_DETAIL, "invalid_letter_detail")
    now = int(hearth.clock())
    run = db.execute("SELECT * FROM runs WHERE id=?", (sender_run,)).fetchone()
    if run is None:
        raise Refused("run_not_found")
    if run["status"] not in {"starting", "running"} or run["finished_at"] is not None:
        raise Refused("run_not_active")
    sender = run["resident_id"]
    authority = {"actor": sender, "run_id": sender_run}
    payload = digest(["letter", to, title, detail, expires_at])
    previous = _existing_operation(db, authority, operation_id, payload)
    if previous:
        return previous

    # Authority is the grant this run was admitted with, not whatever the operator has
    # granted since: a run admitted without `send_letters` never gains it mid-flight, and
    # one whose grant was edited or revoked loses it. Every way of not holding it now is
    # the same refusal, because the sender is told what it may do, not who changed it.
    pin = db.execute(
        "SELECT grant_revision,grant_sha256,expires_at FROM run_management WHERE run_id=?",
        (sender_run,),
    ).fetchone()
    grant = read_grant(db, sender)
    policy = {key: value for key, value in grant.items() if key not in {"resident_id", "revision"}}
    if (
        pin is None
        or pin["grant_revision"] is None
        or now >= pin["expires_at"]
        or not grant["enabled"]
        or grant["revision"] != pin["grant_revision"]
        or digest(policy) != pin["grant_sha256"]
        or "send_letters" not in grant["capabilities"]
    ):
        raise Refused("letters_not_permitted")
    if grant["letter_recipient_ids"] and to not in grant["letter_recipient_ids"]:
        raise Refused("recipient_not_allowed")
    if not db.execute("SELECT 1 FROM residents WHERE id=?", (to,)).fetchone():
        raise Refused("resident_not_found")
    if read_lifecycle(db, to)["state"] == "archived":
        raise Refused("recipient_archived")
    if to == sender:
        raise Refused("self_letter")
    if not hearth.declared_letters_accept(db, to):
        raise Refused("letters_not_accepted")

    policy = read_letter_policy(db)
    parent_task_id = run["task_id"]
    root_task_id, depth = _lineage(db, parent_task_id)
    if depth > policy["max_letter_depth"]:
        raise Refused(
            "max_letter_depth_exceeded",
            {"depth": depth, "max_letter_depth": policy["max_letter_depth"]},
        )
    chain = _chain(db, parent_task_id)
    if to in chain:
        raise Refused("letter_cycle", {"chain": chain + [to]})

    deadline = now + policy["letter_ttl_seconds"]
    if expires_at is not None:
        # The sender may shorten a letter's shelf life and never lengthen it.
        if type(expires_at) is not int or not now < expires_at <= deadline:
            raise Refused("invalid_letter_deadline")
        deadline = expires_at

    task_id = _queue_task(
        db,
        to,
        f"Letter from {sender}: {title}\n\n{detail}",
        now,
        {"letter_operation": operation_id, "originating_run_id": sender_run},
    )
    db.execute(
        "INSERT INTO letters VALUES (?,?,?,?,?,?,?,?,?)",
        (
            task_id,
            sender,
            sender_run,
            parent_task_id,
            root_task_id,
            depth,
            title,
            now,
            deadline,
        ),
    )
    _audit(
        db,
        "letter.sent",
        task_id,
        now,
        {
            "sender_resident_id": sender,
            "sender_run_id": sender_run,
            "recipient_resident_id": to,
            "parent_task_id": parent_task_id,
            "root_task_id": root_task_id,
            "depth": depth,
            "expires_at": deadline,
        },
    )
    return _record_operation(
        db,
        hearth,
        authority,
        operation_id,
        payload,
        {
            "resident_id": to,
            "task_id": task_id,
            "sender_resident_id": sender,
            "parent_task_id": parent_task_id,
            "root_task_id": root_task_id,
            "depth": depth,
            "expires_at": deadline,
            "status": "queued",
        },
    )


def expire_letters(hearth) -> list[str]:
    """Close every letter that went stale before anyone started it.

    An expired letter is a failed task with a reason, not silence: the sender asked a
    question nobody answered in time, and the household spends nothing answering it now.

    The sweep runs on every supervisor pass and almost always finds nothing, so it looks
    first in a read transaction and takes the writer only for the letters it found. The
    write pass re-checks each one: a letter admitted in between is no longer queued and
    is left to the run that started it.
    """
    with hearth.database.transaction() as db:
        pending = [
            row[0]
            for row in db.execute(
                "SELECT l.task_id FROM letters l JOIN tasks t ON t.id=l.task_id "
                "WHERE t.status='queued' AND l.expires_at<=? LIMIT 100",
                (int(hearth.clock()),),
            )
        ]
    if not pending:
        return []
    expired = []
    with hearth.database.transaction(write=True) as db:
        now = int(hearth.clock())
        names = ",".join("?" * len(pending))
        for row in db.execute(
            "SELECT l.*,t.resident_id FROM letters l JOIN tasks t ON t.id=l.task_id "
            f"WHERE l.task_id IN ({names}) AND t.status='queued' AND l.expires_at<=?",
            (*pending, now),
        ).fetchall():
            db.execute("UPDATE tasks SET status='failed' WHERE id=?", (row["task_id"],))
            _audit(
                db,
                "letter.expired",
                row["task_id"],
                now,
                {
                    "reason": "letter_expired",
                    "sender_resident_id": row["sender_resident_id"],
                    "sender_run_id": row["sender_run_id"],
                    "recipient_resident_id": row["resident_id"],
                    "root_task_id": row["root_task_id"],
                    "depth": row["depth"],
                    "expires_at": row["expires_at"],
                },
            )
            expired.append(row["task_id"])
    return expired


def validate_letters(db) -> None:
    """Every letter's lineage must still hold after a copy.

    A backup that kept the rows but lost the chain would let a restored household
    attribute cost to the wrong origin or answer a letter it never received, so the
    depth, the root and both ends are checked against the tasks they name.
    """
    for row in db.execute(
        "SELECT l.*,t.resident_id AS recipient FROM letters l JOIN tasks t ON t.id=l.task_id"
    ):
        sender = db.execute(
            "SELECT resident_id,task_id FROM runs WHERE id=?", (row["sender_run_id"],)
        ).fetchone()
        if sender is None or sender["resident_id"] != row["sender_resident_id"]:
            raise Refused("backup_letters_invalid")
        if row["recipient"] == row["sender_resident_id"]:
            raise Refused("backup_letters_invalid")
        if row["parent_task_id"] is None:
            if row["depth"] != 1 or row["root_task_id"] != row["task_id"]:
                raise Refused("backup_letters_invalid")
            continue
        # The parent is the work the sender was actually doing. Without this, a copy
        # could repoint a letter at any task of the same depth and root, and the
        # restored household would attribute the chain to a hop that never happened.
        if row["parent_task_id"] != sender["task_id"]:
            raise Refused("backup_letters_invalid")
        root, depth = _lineage(db, row["parent_task_id"])
        if row["root_task_id"] != root or row["depth"] != depth:
            raise Refused("backup_letters_invalid")
        if row["recipient"] in _chain(db, row["parent_task_id"]):
            raise Refused("backup_letters_invalid")
