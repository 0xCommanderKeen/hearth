"""One consistent operator snapshot. Credentials and ownership tokens never leave it."""

import json
from dataclasses import asdict

from hearth.authority.household import household_state
from hearth.authority.permissions import _approval
from hearth.skills.assignments import skill_summary
from hearth.work.service import ACTIVE_RUNS, Hearth


def snapshot(hearth: Hearth) -> dict:
    with hearth.database.transaction() as db:
        epoch = db.execute("SELECT value FROM system_meta WHERE key = 'epoch'").fetchone()[0]
        cursor = db.execute("SELECT COALESCE(MAX(sequence), 0) FROM audit").fetchone()[0]
        residents = []
        for row in db.execute("""SELECT r.id, r.revision, d.name, d.purpose, d.daily_limit,
                             d.budget_timezone,
                             (SELECT COALESCE(MAX(revision),0) FROM memory_revisions m
                              WHERE m.resident_id=r.id) AS memory_revision,
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
            resident.update(skill_summary(db, row["id"]))
            residents.append(resident)
        tasks = [
            dict(row)
            for row in db.execute(
                f"SELECT * FROM tasks ORDER BY status IN {ACTIVE_RUNS} DESC, "
                "EXISTS(SELECT 1 FROM runs WHERE runs.task_id=tasks.id AND usage_known=0 "
                "AND finished_at IS NOT NULL) DESC, created_at DESC, id DESC LIMIT 100"
            )
        ]
        runs = [
            dict(row)
            for row in db.execute(f"""SELECT id, task_id, resident_id,
                   resident_revision, status, reserved, budget_day, budget_timezone,
                   runtime_kind, runtime_version, input_digest,
                   created_at, actual_cost,
                   COALESCE((SELECT revision FROM run_memory m WHERE m.run_id=runs.id),0)
                   AS memory_revision,
                   usage_known, finished_at, artifact_id, cancellation_requested,
                   CASE WHEN EXISTS(SELECT 1 FROM usage_reconciliations u WHERE u.run_id=runs.id)
                   THEN 'operator_reported_mock' WHEN usage_known=1 AND EXISTS
                   (SELECT 1 FROM run_pricing p WHERE p.run_id=runs.id)
                   THEN CASE WHEN runtime_kind='codex_subscription'
                   THEN 'api_equivalent_subscription' ELSE 'api_equivalent_mock' END
                   WHEN usage_known=1 THEN 'mock_runtime'
                   ELSE 'unknown' END AS usage_source
                   FROM runs ORDER BY status IN {ACTIVE_RUNS} DESC,
                   (usage_known=0 AND finished_at IS NOT NULL) DESC,
                   created_at DESC, id DESC LIMIT 100""")
        ]
        for run in runs:
            run.update(skill_summary(db, run["id"], run=True))
        audit = [
            dict(row)
            for row in db.execute(
                "SELECT sequence, kind, resource_id, at FROM audit ORDER BY sequence DESC LIMIT 30"
            )
        ]
        return {
            "household": household_state(db, int(hearth.clock())),
            "restore_hold": bool(
                db.execute("SELECT 1 FROM system_meta WHERE key='restore_hold'").fetchone()
            ),
            "schema_version": 1,
            "simulated": db.execute(
                "SELECT value FROM system_meta WHERE key='runtime_kind'"
            ).fetchone()[0]
            != "codex_subscription",
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
