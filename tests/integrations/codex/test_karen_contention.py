"""Simultaneous native manager callbacks share ordinary policy and execution."""

import json
import time

from hearth.authority.household import Household, household_state
from hearth.inputs.catalog import Inputs
from hearth.management.authority import GrantPolicy, Management
from hearth.residents.provisioning import Provisioning

from tests.integrations.codex.test_karen_journey import NOTES, installed


def test_native_managers_contend_without_duplicates_or_starving_admitted_work(tmp_path):
    app = installed(tmp_path)
    hearth = app.state.hearth
    Household(hearth).save(
        daily_limit=10000000,
        timezone="Europe/Ljubljana",
        resident_limit=4,
        concurrency_limit=3,
        expected_revision=0,
    )
    orchard = Inputs(hearth).save(
        "orchard", name="Fictional orchard", notes=NOTES, actor="operator"
    )
    managers, children = [], []
    for label in ("A", "B"):
        manager = Provisioning(hearth).create(
            "manager-" + label,
            {
                "name": "Manager " + label,
                "purpose": "Assign bounded synthetic reports.",
                "execution_profile": "codex_subscription",
                "daily_limit": 1000000,
                "creation_reason": "Synthetic concurrency fixture setup.",
            },
            actor="operator",
        )
        managers.append(manager["resident_id"])
        Management(hearth).save(
            manager["resident_id"],
            GrantPolicy().model_dump()
            | {
                "expected_revision": 0,
                "enabled": True,
                "profiles": ["codex_subscription"],
                "input_set_ids": [orchard["input_set_id"]],
                "capabilities": ["assign_work"],
                "max_calls": 8,
            },
        )
        child = Provisioning(hearth).create(
            "reporter-" + label,
            {
                "name": "Contended reporter " + label,
                "purpose": "Summarize supplied orchard notes.",
                "execution_profile": "codex_subscription",
                "daily_limit": 100000,
                "creation_reason": "Synthetic concurrency fixture setup.",
                "manager": manager["resident_id"],
                "input_sets": [{"input_set_id": orchard["input_set_id"]}],
            },
            actor="operator",
        )
        children.append(child["resident_id"])
    for index, resident_id in enumerate(managers):
        task = hearth.submit(
            "contend-" + str(index),
            resident_id,
            "Contend for one child slot.",
            expires_at=int(hearth.clock()) + 60,
        )
        hearth.admit(task.task_id, reserve=10000)
    supervisor = app.state.supervisor
    supervisor.start()
    try:
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            with hearth.database.transaction() as db:
                runs = [
                    dict(row)
                    for row in db.execute(
                        "SELECT id,resident_id,status,actual_cost,usage_known,artifact_id FROM runs"
                    )
                ]
            if len(runs) == 3 and all(row["status"] == "succeeded" for row in runs):
                break
            time.sleep(0.05)
        assert len(runs) == 3 and all(row["status"] == "succeeded" for row in runs), runs
    finally:
        supervisor.stop()
    with hearth.database.transaction() as db:
        calls = [json.loads(row[0]) for row in db.execute("SELECT response FROM management_calls")]
        refused = [
            json.loads(row["contentItems"][0]["text"])["error"]
            for row in calls
            if not row["success"]
        ]
        assert sorted(refused) == [
            "household_concurrency_limit",
            "management_resident_out_of_scope",
            "management_resident_out_of_scope",
        ]
        assert db.execute("SELECT COUNT(*) FROM tasks").fetchone()[0] == 3
        assert db.execute("SELECT COUNT(*) FROM management_operations").fetchone()[0] == 1
        assert db.execute("SELECT COUNT(*) FROM run_usage").fetchone()[0] == 3
        household = household_state(db, int(hearth.clock()))
        assert household["spent"] == 2100 and household["active_runs"] == 0
        assert household["unknown"] == 0 and household["reserved"] == 0
    winner = next(row for row in runs if row["resident_id"] in children)
    assert (
        app.state.execution.artifact(winner["artifact_id"])[1]
        == "Simulation: Harvested 12 pears Monday. Planted 3 trees Tuesday."
    )
    assert all(row["usage_known"] and row["actual_cost"] == 700 for row in runs)
    assert supervisor.health()["executor_error"] is None
