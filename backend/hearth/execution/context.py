"""Read one pinned mock input within the caller's authorized database snapshot."""

import sqlite3

from hearth.residents.memory import MemoryFiles, read_revision
from hearth.residents.models import Refused


def read_context(db: sqlite3.Connection, run_id: str, memory: MemoryFiles) -> dict:
    """Trusted internal read, not an authorization check or a credential issuer."""
    row = db.execute(
        "SELECT r.id, r.task_id, r.resident_id, r.resident_revision, "
        "d.purpose, d.skill_text, t.instruction, r.runtime_kind "
        "FROM runs r JOIN declarations d ON d.resident_id=r.resident_id "
        "AND d.revision=r.resident_revision JOIN tasks t ON t.id=r.task_id WHERE r.id=?",
        (run_id,),
    ).fetchone()
    if row is None:
        raise Refused("run_not_found")
    pinned = db.execute(
        "SELECT resident_id,revision FROM run_memory WHERE run_id=?", (run_id,)
    ).fetchone()
    if pinned is not None and pinned["resident_id"] != row["resident_id"]:
        raise Refused("memory_run_mismatch")
    return {
        "context_version": 3,
        "run_id": row["id"],
        "task_id": row["task_id"],
        "resident_id": row["resident_id"],
        "resident_revision": row["resident_revision"],
        "purpose": row["purpose"],
        "skill_text": row["skill_text"],
        "memory": read_revision(
            db, memory, row["resident_id"], pinned["revision"] if pinned else 0
        ),
        "instruction": row["instruction"],
        "simulated": row["runtime_kind"] != "codex_subscription",
        "notes": [
            "Synthetic note: drafted the Hearth foundation.",
            "Synthetic note: task submission survives retries.",
            "Synthetic note: exercise cancellation and recovery next.",
        ],
    }
