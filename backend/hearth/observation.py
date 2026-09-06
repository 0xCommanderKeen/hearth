"""One consistent operator snapshot. Credentials and ownership tokens never leave it."""

from hearth.core import ACTIVE_RUNS, Hearth


def snapshot(hearth: Hearth) -> dict:
    with hearth.database.transaction() as db:
        epoch = db.execute("SELECT value FROM system_meta WHERE key = 'epoch'").fetchone()[0]
        cursor = db.execute("SELECT COALESCE(MAX(sequence), 0) FROM audit").fetchone()[0]
        residents = []
        for row in db.execute("""SELECT r.id, r.revision, d.name, d.purpose, d.daily_limit,
                             p.reason AS pause_reason FROM residents r
                             JOIN declarations d ON d.resident_id = r.id AND d.revision = r.revision
                             LEFT JOIN pauses p ON p.resident_id = r.id ORDER BY r.id"""):
            resident = dict(row)
            active = db.execute(
                f"SELECT status FROM runs WHERE resident_id = ? AND status IN {ACTIVE_RUNS}",
                (row["id"],),
            ).fetchone()
            resident["presence"] = (
                active[0] if active else ("paused" if row["pause_reason"] else "ready")
            )
            residents.append(resident)
        tasks = [
            dict(row)
            for row in db.execute(
                f"SELECT * FROM tasks ORDER BY status IN {ACTIVE_RUNS} DESC, "
                "created_at DESC, id DESC LIMIT 100"
            )
        ]
        runs = [
            dict(row)
            for row in db.execute(f"""SELECT id, task_id, resident_id,
                   resident_revision, status, reserved, budget_day, created_at, actual_cost,
                   usage_known, finished_at, artifact_id, cancellation_requested
                   FROM runs ORDER BY status IN {ACTIVE_RUNS} DESC,
                   created_at DESC, id DESC LIMIT 100""")
        ]
        audit = [
            dict(row)
            for row in db.execute(
                "SELECT sequence, kind, resource_id, at FROM audit ORDER BY sequence DESC LIMIT 30"
            )
        ]
        return {
            "schema_version": 1,
            "simulated": True,
            "epoch": epoch,
            "cursor": cursor,
            "residents": residents,
            "tasks": tasks,
            "runs": runs,
            "activity": audit,
            "limits": {"tasks": 100, "runs": 100, "activity": 30},
        }
