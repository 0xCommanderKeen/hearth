"""One consistent operator snapshot. Credentials and ownership tokens never leave it."""

import json
from dataclasses import asdict

from hearth.authority import _approval
from hearth.core import ACTIVE_RUNS, Hearth


def snapshot(hearth: Hearth) -> dict:
    with hearth.database.transaction() as db:
        epoch = db.execute("SELECT value FROM system_meta WHERE key = 'epoch'").fetchone()[0]
        cursor = db.execute("SELECT COALESCE(MAX(sequence), 0) FROM audit").fetchone()[0]
        residents = []
        for row in db.execute("""SELECT r.id, r.revision, d.name, d.purpose, d.daily_limit,
                             COALESCE(p.reason, CASE WHEN c.paused=1 THEN 'operator' END)
                             AS pause_reason,
                             COALESCE(c.paused,0) AS operator_paused,
                             COALESCE(c.revision,0) AS control_revision FROM residents r
                             JOIN declarations d ON d.resident_id = r.id AND d.revision = r.revision
                             LEFT JOIN pauses p ON p.resident_id = r.id
                             LEFT JOIN operator_controls c ON c.resident_id=r.id ORDER BY r.id"""):
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
            "restore_hold": bool(
                db.execute("SELECT 1 FROM system_meta WHERE key='restore_hold'").fetchone()
            ),
            "schema_version": 1,
            "simulated": True,
            "epoch": epoch,
            "cursor": cursor,
            "residents": residents,
            "tasks": tasks,
            "runs": runs,
            "activity": audit,
            "notifications": [
                dict(row) | {"payload": json.loads(row["payload"])}
                for row in db.execute(
                    "SELECT * FROM deliveries ORDER BY "
                    "status IN ('pending','retry') DESC, created_at DESC, id DESC LIMIT 100"
                )
            ],
            "routines": [
                dict(row)
                for row in db.execute(
                    "SELECT r.*, d.instruction, d.local_time, d.timezone FROM routines r "
                    "JOIN routine_revisions d ON d.routine_id=r.id AND d.revision=r.revision "
                    "ORDER BY r.id"
                )
            ],
            "occurrences": [
                dict(row)
                for row in db.execute(
                    "SELECT * FROM occurrences ORDER BY scheduled_at DESC, routine_id LIMIT 100"
                )
            ],
            "publication_policies": [
                dict(row)
                for row in db.execute(
                    "SELECT resident_id, revision, enabled FROM publication_policies "
                    "ORDER BY resident_id"
                )
            ],
            "approvals": [
                asdict(_approval(row))
                for row in db.execute(
                    "SELECT approvals.* FROM approvals LEFT JOIN publication_actions a "
                    "ON a.id = approvals.id ORDER BY "
                    "COALESCE(a.status IN ('executing','unknown'), 0) DESC, "
                    "approvals.status = 'pending' DESC, "
                    "approvals.created_at DESC, approvals.id DESC LIMIT 100"
                )
            ],
            "actions": [
                dict(row)
                for row in db.execute(
                    "SELECT id, status, reason FROM publication_actions "
                    "ORDER BY status IN ('executing','unknown') DESC, "
                    "updated_at DESC, id DESC LIMIT 100"
                )
            ],
            "limits": {"tasks": 100, "runs": 100, "activity": 30, "approvals": 100, "actions": 100},
        }
