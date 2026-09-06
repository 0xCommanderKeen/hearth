"""Read one pinned mock input within the caller's authorized database snapshot."""

import sqlite3

from hearth.models import Refused


def read_context(db: sqlite3.Connection, run_id: str) -> dict:
    """Trusted internal read, not an authorization check or a credential issuer."""
    row = db.execute(
        "SELECT r.id, r.task_id, r.resident_id, r.resident_revision, d.purpose, t.instruction "
        "FROM runs r JOIN declarations d ON d.resident_id=r.resident_id "
        "AND d.revision=r.resident_revision JOIN tasks t ON t.id=r.task_id WHERE r.id=?",
        (run_id,),
    ).fetchone()
    if row is None:
        raise Refused("run_not_found")
    return {
        "context_version": 1,
        "run_id": row["id"],
        "task_id": row["task_id"],
        "resident_id": row["resident_id"],
        "resident_revision": row["resident_revision"],
        "purpose": row["purpose"],
        "instruction": row["instruction"],
        "simulated": True,
        "notes": [
            "Synthetic note: drafted the Hearth foundation.",
            "Synthetic note: task submission survives retries.",
            "Synthetic note: exercise cancellation and recovery next.",
        ],
    }
