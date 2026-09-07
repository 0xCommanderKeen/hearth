"""One synthetic native manager task through the ordinary application owners."""

import json
import sys
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient
from hearth.app import create_app
from hearth.authority.household import household_state
from hearth.inputs.catalog import Inputs
from hearth.inputs.selection import run_inputs
from hearth.management.authority import read_grant
from hearth.management.bootstrap import bootstrap
from hearth.management.bridge import BoundRun, Bridge
from hearth.residents.maintenance import Maintenance
from hearth.skills.assignments import read_assignments, run_skills
from hearth.skills.validation import read_validation
from hearth.storage.backup import capture, restore, verify

TOKEN = "synthetic-karen-journey-operator"
AUTH = {"Authorization": "Bearer " + TOKEN}
NOTES = ["Harvested 12 pears Monday", "Planted 3 trees Tuesday"]
TASK = (
    "Create a concise fictional orchard reporter. Author and evaluate a suitable reporting "
    "skill, assign its exact published revision and the named Fictional orchard input, "
    "schedule a daily report at 09:00 Europe/Ljubljana, and save its first report now. "
    "Use only the supplied facts and report honestly when inputs are absent."
)


def reopen(tmp_path):
    return create_app(
        tmp_path / "data",
        TOKEN,
        supervise=False,
        runtime_kind="codex_subscription",
        codex_binary=tmp_path / "synthetic-codex",
        codex_auth_home=tmp_path / "synthetic-auth",
    )


def installed(tmp_path, *, replay=False, unknown=False):
    binary = tmp_path / "synthetic-codex"
    fixture = Path(__file__).with_name("journey_cli.py")
    binary.write_text(
        "#!"
        + sys.executable
        + "\n"
        + fixture.read_text()
        .replace("REPLAY = False", f"REPLAY = {replay}")
        .replace("UNKNOWN_USAGE = False", f"UNKNOWN_USAGE = {unknown}")
    )
    binary.chmod(0o700)
    auth = tmp_path / "synthetic-auth"
    auth.mkdir()
    (auth / "auth.json").write_text("{}")
    return reopen(tmp_path)


def configured(tmp_path, *, replay=False, unknown=False):
    app = installed(tmp_path, replay=replay, unknown=unknown)
    hearth = app.state.hearth
    Inputs(hearth).save("orchard", name="Fictional orchard", notes=NOTES, actor="operator")
    karen = bootstrap(hearth)
    task = hearth.submit(
        "one-request", karen["resident_id"], TASK, expires_at=int(hearth.clock()) + 120
    )
    hearth.admit(task.task_id, reserve=500000)
    return app, karen, task


def complete_journey(tmp_path, *, replay=False, unknown=False):
    app, karen, task = configured(tmp_path, replay=replay, unknown=unknown)
    supervisor = app.state.supervisor
    supervisor.start()
    restarted = False
    try:
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            with app.state.hearth.database.transaction() as db:
                runs = [
                    dict(row)
                    for row in db.execute(
                        "SELECT id,task_id,resident_id,status,usage_known,actual_cost,artifact_id "
                        "FROM runs"
                    )
                ]
            if replay and not restarted and (tmp_path / "restart.request").exists():
                # Restart supervision and database/API owners; keep the already-bound
                # native manager process alive rather than relaunching uncertain work.
                supervisor.stop()
                prior = [(row["id"], row["status"]) for row in runs]
                app = reopen(tmp_path)
                with app.state.hearth.database.transaction() as db:
                    assert [
                        (row["id"], row["status"])
                        for row in db.execute("SELECT id,status FROM runs")
                    ] == prior
                replay_committed_call(app, task.task_id, tmp_path / "restart.request")
                supervisor = app.state.supervisor
                supervisor.start()
                (tmp_path / "restart.ready").write_text("same manager continues")
                restarted = True
            if len(runs) == 4 and all(row["status"] == "succeeded" for row in runs):
                break
            if any(row["status"] in {"failed", "cancelled"} for row in runs):
                break
            time.sleep(0.1)
        assert len(runs) == 4 and all(row["status"] == "succeeded" for row in runs), runs
        for row in runs:
            if unknown and row["task_id"] == task.task_id:
                assert not row["usage_known"] and row["actual_cost"] is None
            else:
                assert row["usage_known"] and row["actual_cost"] == 700
        assert restarted is replay
        return app, karen, task, runs
    finally:
        supervisor.stop()


def replay_committed_call(app, task_id, request_path):
    """Reopen a durable call receipt; perform no new manager mutation."""
    hearth = app.state.hearth
    request = json.loads(request_path.read_text())
    with hearth.database.transaction() as db:
        run = db.execute("SELECT * FROM runs WHERE task_id=?", (task_id,)).fetchone()
        binding = db.execute("SELECT * FROM run_management WHERE run_id=?", (run["id"],)).fetchone()
        epoch = db.execute("SELECT value FROM system_meta WHERE key='epoch'").fetchone()[0]
        response = db.execute(
            "SELECT response FROM management_calls WHERE run_id=? AND call_id=?",
            (run["id"], request["callId"]),
        ).fetchone()[0]
        bound = BoundRun(run["id"], run["owner_token"], epoch, run["input_digest"])
        request.update(threadId=binding["thread_id"], turnId=binding["turn_id"], namespace=None)
        count = db.execute("SELECT COUNT(*) FROM management_calls").fetchone()[0]
    assert Bridge(hearth, bound).call(request) == json.loads(response)
    with hearth.database.transaction() as db:
        assert db.execute("SELECT COUNT(*) FROM management_calls").fetchone()[0] == count


def evidence(app, karen, task, runs):
    hearth = app.state.hearth
    with hearth.database.transaction() as db:
        manager = next(row for row in runs if row["task_id"] == task.task_id)
        operations = {
            row["operation_id"]: json.loads(row["receipt"])
            for row in db.execute("SELECT operation_id,receipt FROM management_operations")
        }
        assert len(operations) == 7
        assert all(value["originating_run_id"] == manager["id"] for value in operations.values())
        assert all(value["actor"] == karen["resident_id"] for value in operations.values())
        skill_id = operations["orchard-publish"]["skill_id"]
        resident_id = operations["orchard-reporter"]["resident_id"]
        report = next(row for row in runs if row["resident_id"] == resident_id)
        assert report["task_id"] == operations["orchard-first-report"]["task_id"]
        validation = read_validation(db, operations["orchard-validation"]["validation_id"])
        assert validation["status"] == "passed"
        assert validation["originating_run_id"] == manager["id"]
        assert len(validation["cases"]) == 2
        case_ids = [case["run_id"] for case in validation["cases"]]
        assert set(case_ids) == {row["id"] for row in runs} - {manager["id"], report["id"]}
        assert all(case["result"]["passed"] for case in validation["cases"])
        for case in validation["cases"]:
            assert run_skills(db, case["run_id"])[0]["revision"] == 1
            assert run_inputs(db, case["run_id"])[0]["revision"] == case["input_revision"]
        published = db.execute("SELECT * FROM skill_publications").fetchone()
        assert published["skill_id"] == skill_id
        assert published["validation_id"] == validation["validation_id"]
        assert published["revision"] == 2 and published["candidate_revision"] == 1
        assignments = read_assignments(db, resident_id)
        assert assignments["revision"] == operations["orchard-assignment"]["revision"]
        assert [(row["skill_id"], row["revision"]) for row in assignments["skills"]] == [
            (skill_id, 2)
        ]
        pins = run_skills(db, report["id"])
        assert [(row["skill_id"], row["revision"]) for row in pins] == [(skill_id, 2)]
        assert run_inputs(db, report["id"])[0]["notes"] == NOTES
        assert not read_grant(db, resident_id)["enabled"]
        assert not read_grant(db, validation["evaluator_id"])["enabled"]
        profile = dict(
            db.execute(
                "SELECT * FROM resident_profiles WHERE resident_id=?", (resident_id,)
            ).fetchone()
        )
        assert profile["creator"] == karen["resident_id"]
        assert profile["originating_run_id"] == manager["id"]
        for table, count in {
            "residents": 3,
            "skills": 3,
            "tasks": 4,
            "runs": 4,
            "run_usage": 4,
            "artifacts": 4,
            "routines": 1,
            "skill_validations": 1,
            "skill_publications": 1,
        }.items():
            assert db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == count
        assert (
            db.execute("SELECT COUNT(*) FROM audit WHERE kind='run.succeeded'").fetchone()[0] == 4
        )
        household = household_state(db, int(hearth.clock()))
        assert household["spent"] == sum(row["actual_cost"] or 0 for row in runs)
        routine = dict(db.execute("SELECT * FROM routines").fetchone())
        assert routine["resident_id"] == resident_id and routine["enabled"]
        local = datetime.fromtimestamp(routine["next_at"], ZoneInfo("Europe/Ljubljana"))
        assert (local.hour, local.minute) == (9, 0)
        assert routine["next_at"] > int(hearth.clock())
    configuration = Maintenance(hearth).configuration(resident_id)
    assert configuration["lifecycle"]["manager"] == karen["resident_id"]
    result = app.state.execution.artifact(report["artifact_id"])[1]
    assert result == "Simulation: Harvested 12 pears Monday. Planted 3 trees Tuesday."
    return {
        "skill_id": skill_id,
        "resident_id": resident_id,
        "validation": validation,
        "operations": operations,
        "configuration": configuration,
        "report": report,
        "result": result,
        "household": household,
    }


@pytest.mark.parametrize("replay", [False, True], ids=["single-task", "lost-replies-and-restart"])
def test_one_native_task_creates_validated_reporter_and_first_saved_result(tmp_path, replay):
    app, karen, task, runs = complete_journey(tmp_path, replay=replay)
    recorded = evidence(app, karen, task, runs)
    if replay:
        with app.state.hearth.database.transaction() as db:
            responses = {
                row["call_id"]: json.loads(row["response"])
                for row in db.execute("SELECT call_id,response FROM management_calls")
            }
        for operation in (
            "orchard-publish",
            "orchard-reporter",
            "orchard-assignment",
            "orchard-routine",
            "orchard-first-report",
        ):
            assert responses[operation] == responses[operation + "-recovered"]
            assert not responses[operation + "-changed"]["success"]
    capture(tmp_path / "data", tmp_path / "backup")
    manifest = verify(tmp_path / "backup")
    assert not any(
        part in name
        for name in manifest["files"]
        for part in ("auth", "request.json", "worker.lock")
    )
    assert all(
        TOKEN.encode() not in (tmp_path / "backup" / name).read_bytes()
        for name in manifest["files"]
    )
    restore(tmp_path / "backup", tmp_path / "held")
    held = create_app(tmp_path / "held", TOKEN, supervise=False)
    assert held.state.hearth.database.restored()
    assert evidence(held, karen, task, runs) == recorded
    with TestClient(held) as client:
        result = client.get("/api/artifacts/" + recorded["report"]["artifact_id"], headers=AUTH)
        assert result.status_code == 200 and result.json()["content"] == recorded["result"]
        skill = client.get("/api/skills/" + recorded["skill_id"], headers=AUTH)
        assert (
            skill.status_code == 200
            and skill.json()["authoring"]["validation"]["status"] == "passed"
        )
        refused = client.post("/api/management/bootstrap", headers=AUTH)
        assert refused.status_code == 409 and refused.json()["error"] == "restored_copy_read_only"


def test_unknown_manager_usage_preserves_complete_chain_and_never_relaunches(tmp_path):
    app, karen, task, runs = complete_journey(tmp_path, unknown=True)
    recorded = evidence(app, karen, task, runs)
    assert recorded["household"]["unknown"] == 500000
    app = reopen(tmp_path)
    for _ in range(3):
        app.state.executor.step()
    with app.state.hearth.database.transaction() as db:
        assert {row[0] for row in db.execute("SELECT id FROM runs")} == {row["id"] for row in runs}
        pause = db.execute(
            "SELECT reason FROM pauses WHERE resident_id=?", (karen["resident_id"],)
        ).fetchone()
        assert pause[0] == "usage_unknown"
        assert db.execute("SELECT COUNT(*) FROM run_usage").fetchone()[0] == 4
    assert evidence(app, karen, task, runs) == recorded
    capture(tmp_path / "data", tmp_path / "backup")
    restore(tmp_path / "backup", tmp_path / "held")
    held = create_app(tmp_path / "held", TOKEN, supervise=False)
    assert evidence(held, karen, task, runs) == recorded
    with TestClient(held) as client:
        state = client.get("/api/state", headers=AUTH)
        assert state.status_code == 200
        assert "usage_unknown" in state.text
