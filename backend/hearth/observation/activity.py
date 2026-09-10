"""Resident-scoped audit history, independent of the snapshot's recent window."""

from hearth.residents.models import Refused


def history(hearth, resident_id: str, *, before: int | None = None, limit: int = 30) -> dict:
    with hearth.database.transaction() as db:
        if not db.execute("SELECT 1 FROM residents WHERE id=?", (resident_id,)).fetchone():
            raise Refused("resident_not_found")
        rows = db.execute(
            """SELECT a.sequence,a.kind,a.resource_id,a.at,r.id AS run_id,
                      r.runtime_kind,r.status AS run_status
               FROM audit a LEFT JOIN runs r ON r.id=a.resource_id
               WHERE (a.resource_id=?
                   OR a.resource_id IN (SELECT id FROM tasks WHERE resident_id=?)
                   OR a.resource_id IN (SELECT id FROM runs WHERE resident_id=?)
                   OR a.resource_id IN (SELECT id FROM routines WHERE resident_id=?))
                 AND (? IS NULL OR a.sequence < ?)
               ORDER BY a.sequence DESC LIMIT ?""",
            (resident_id, resident_id, resident_id, resident_id, before, before, limit + 1),
        ).fetchall()
        entries = [dict(row) for row in rows[:limit]]
        return {
            "entries": entries,
            "next_before": entries[-1]["sequence"] if len(rows) > limit else None,
        }
