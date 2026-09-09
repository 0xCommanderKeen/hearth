"""One consistent operator snapshot. Credentials and ownership tokens never leave it."""

import json

from hearth.authority.household import household_state
from hearth.inputs.selection import input_summary
from hearth.integrations.interface import RUNTIMES, live_kinds
from hearth.integrations.interface import label as runtime_label
from hearth.management.authority import management_summary, mount_summary
from hearth.residents.journal import run_journal_summary
from hearth.residents.lifecycle import lifecycle_summary
from hearth.residents.memory import run_memory_writes
from hearth.residents.provisioning import profile_summary
from hearth.skills.assignments import skill_summary
from hearth.work.letters import (
    MAX_EVENTS,
    letter_events,
    refused_sends,
    resident_names,
    task_lineage,
)
from hearth.work.service import ACTIVE_RUNS, Hearth, configured_runtime, default_runtime

# A run priced under a live runtime's own schedule is an API-equivalent estimate of a
# subscription. Anything else with a price is history from a runtime that only pretended.
LIVE_KINDS = "(" + ", ".join(repr(kind) for kind in sorted(live_kinds())) + ")"


def snapshot(hearth: Hearth) -> dict:
    with hearth.database.transaction() as db:
        epoch = db.execute("SELECT value FROM system_meta WHERE key = 'epoch'").fetchone()[0]
        cursor = db.execute("SELECT COALESCE(MAX(sequence), 0) FROM audit").fetchone()[0]
        residents = []
        for row in db.execute("""SELECT r.id, r.revision, d.name, d.purpose, d.daily_limit,
                             d.budget_timezone, d.letters_accept,
                             (SELECT COALESCE(MAX(revision),0) FROM memory_revisions m
                              WHERE m.resident_id=r.id) AS memory_revision,
                             p.reason AS safety_hold_reason FROM residents r
                             JOIN declarations d ON d.resident_id = r.id AND d.revision = r.revision
                             LEFT JOIN pauses p ON p.resident_id = r.id ORDER BY r.id"""):
            resident = dict(row)
            lifecycle = lifecycle_summary(db, row["id"])
            resident["lifecycle"] = lifecycle
            resident["operator_paused"] = lifecycle["state"] == "paused"
            resident["control_revision"] = lifecycle.get("revision", 0)
            resident["pause_reason"] = row["safety_hold_reason"] or (
                "operator" if lifecycle["state"] == "paused" else lifecycle.get("error")
            )
            resident["unresolved_runs"] = db.execute(
                f"SELECT COUNT(*) FROM runs WHERE resident_id=? AND (status IN {ACTIVE_RUNS} "
                "OR (finished_at IS NOT NULL AND usage_known=0))",
                (row["id"],),
            ).fetchone()[0]
            resident["profile"] = profile_summary(db, row["id"])
            resident["management"] = management_summary(db, row["id"])
            active = db.execute(
                f"SELECT status FROM runs WHERE resident_id = ? AND status IN {ACTIVE_RUNS}",
                (row["id"],),
            ).fetchone()
            resident["presence"] = (
                active[0]
                if active
                else ("paused" if resident["pause_reason"] else lifecycle["state"])
            )
            resident.update(skill_summary(db, row["id"]))
            resident.update(input_summary(db, row["id"]))
            residents.append(resident)
        # Names are read once for the whole projection: a chain is read by name, and a
        # hundred tasks would otherwise ask the same question a hundred times.
        names = resident_names(db)
        tasks = [
            dict(row)
            for row in db.execute(
                f"SELECT * FROM tasks ORDER BY status IN {ACTIVE_RUNS} DESC, "
                "EXISTS(SELECT 1 FROM runs WHERE runs.task_id=tasks.id AND usage_known=0 "
                "AND finished_at IS NOT NULL) DESC, created_at DESC, id DESC LIMIT 100"
            )
        ]
        for task in tasks:
            # A letter says which chain it belongs to; an ordinary task is its own chain
            # and carries none, so the operator sees a breadcrumb only where one exists.
            task["lineage"] = task_lineage(db, task["id"], names)
        runs = [
            dict(row)
            for row in db.execute(f"""SELECT runs.id AS id, task_id, resident_id,
                   resident_revision, status, reserved, budget_day, budget_timezone,
                   runtime_kind, runtime_version, input_digest,
                   -- The run's own price pin, joined once: what a run was priced under
                   -- is what the operator is shown beside its cost, and whether it was
                   -- priced at all is what decides the usage source below.
                   p.model AS model, p.schedule AS price_schedule,
                   created_at, actual_cost,
                   COALESCE((SELECT revision FROM run_memory m WHERE m.run_id=runs.id),0)
                   AS memory_revision,
                   usage_known, finished_at, artifact_id, cancellation_requested,
                   CASE WHEN EXISTS(SELECT 1 FROM usage_reconciliations u WHERE u.run_id=runs.id)
                   THEN 'operator_reported' WHEN usage_known=1 AND p.model IS NOT NULL
                   THEN CASE WHEN runtime_kind IN {LIVE_KINDS}
                   THEN 'api_equivalent_subscription' ELSE 'api_equivalent_mock' END
                   WHEN usage_known=1 THEN 'mock_runtime'
                   ELSE 'unknown' END AS usage_source
                   FROM runs LEFT JOIN run_pricing p ON p.run_id=runs.id
                   ORDER BY status IN {ACTIVE_RUNS} DESC,
                   (usage_known=0 AND finished_at IS NOT NULL) DESC,
                   created_at DESC, id DESC LIMIT 100""")
        ]
        # A refused send writes nothing, so it exists only as the run's own tool
        # evidence. Reading them all at once keeps a hundred runs to one pass.
        refusals = refused_sends(db, [run["id"] for run in runs])
        for run in runs:
            run["letters_refused"] = refusals.get(run["id"], [])
            run.update(skill_summary(db, run["id"], run=True))
            run.update(input_summary(db, run["id"], run=True))
            run["management"] = management_summary(db, run["id"], run=True)
            # What this run could reach on disk, as it was admitted: an operator reads
            # a resident's reach off a finished run, not off configuration that has
            # moved on since (`docs/adr/0016-sandbox-per-run.md`).
            run.update(mount_summary(db, run["id"], run=True))
            # What the run opened with and what it wrote, never who claimed to.
            run.update(run_journal_summary(db, run["id"]))
            run["memory_written"] = run_memory_writes(db, run["id"])
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
            # Which brains this household has, and how every kind a run may carry is
            # named to an operator. The registry answers both, so no view downstream of
            # here has a provider's name written into it
            # (`docs/adr/0015-runtime-per-resident.md`).
            "runtimes": {
                "default": default_runtime(db),
                "configured": [kind for kind in live_kinds() if configured_runtime(db, kind)],
                "kinds": {
                    kind: {"label": runtime_label(kind), "live": spec.live}
                    for kind, spec in RUNTIMES.items()
                },
            },
            "epoch": epoch,
            "cursor": cursor,
            "residents": residents,
            "provisioning": [
                dict(row)
                for row in db.execute(
                    "SELECT command_id,resident_id,status,reason,name,creator,manager,"
                    "created_at,task_id,routine_id "
                    "FROM resident_provisioning ORDER BY created_at DESC,command_id DESC LIMIT 100"
                )
            ],
            "tasks": tasks,
            "runs": runs,
            # What the post did, both ends named. A village draws its walks from these
            # and from nothing else.
            "letters": letter_events(db),
            "activity": audit,
            "notifications": [
                dict(row) | {"payload": json.loads(row["payload"])}
                for row in db.execute(
                    "SELECT * FROM notifications ORDER BY "
                    "read_at IS NULL DESC, created_at DESC, id DESC LIMIT 100"
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
            "limits": {
                "tasks": 100,
                "runs": 100,
                "activity": 30,
                "notifications": 100,
                "letters": MAX_EVENTS,
            },
        }
