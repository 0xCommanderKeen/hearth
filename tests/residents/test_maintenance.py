"""Resident lifecycle and coherent edits through operator and scoped application seams."""

from fastapi.testclient import TestClient
from hearth.app import create_app
from hearth.residents.maintenance import LifecycleChange, Maintenance

TOKEN = "synthetic-maintenance-operator"
AUTH = {"Authorization": "Bearer " + TOKEN}


def test_archive_keeps_saved_result_and_refuses_future_work_across_restart(tmp_path):
    app = create_app(tmp_path, TOKEN, supervise=False)
    app.state.hearth.clock = lambda: 1_788_640_000
    with TestClient(app) as client:
        client.post("/api/demo/reader", headers=AUTH).raise_for_status()
        task = client.post(
            "/api/tasks",
            headers={**AUTH, "Idempotency-Key": "first"},
            json={"resident_id": "reader", "instruction": "Summary", "expires_at": 1_788_640_600},
        ).json()
        run = client.post("/api/tasks/" + task["task_id"] + "/start", headers=AUTH).json()
        app.state.executor.step()
        before = client.get("/api/runs/" + run["run_id"], headers=AUTH).json()
        route = "/api/residents/reader/lifecycle"
        initial = client.get(route, headers=AUTH)
        assert initial.status_code == 200
        assert initial.json()["state"] == "ready"
        body = {"expected_revision": initial.json()["revision"], "state": "archived"}
        headers = {**AUTH, "Idempotency-Key": "archive-reader"}
        archived = client.put(route, headers=headers, json=body)
        assert archived.status_code == 200 and archived.json()["state"] == "archived"
        assert client.put(route, headers=headers, json=body).json() == archived.json()
        assert client.get("/api/runs/" + run["run_id"], headers=AUTH).json() == before
        refused = client.post(
            "/api/tasks",
            headers={**AUTH, "Idempotency-Key": "later"},
            json={"resident_id": "reader", "instruction": "Again", "expires_at": 1_788_640_600},
        )
        assert refused.json() == {"error": "resident_archived"}
    with TestClient(create_app(tmp_path, TOKEN, supervise=False)) as reopened:
        assert reopened.get(route, headers=AUTH).json()["state"] == "archived"
        assert reopened.put(route, headers=headers, json=body).json() == archived.json()
        invalid = reopened.put(
            route,
            headers={**AUTH, "Idempotency-Key": "resume-archive"},
            json={"expected_revision": archived.json()["revision"], "state": "ready"},
        )
        assert invalid.json() == {"error": "resident_archived"}


def test_archive_blocks_queued_admission_schedules_and_trusted_dispatch(tmp_path):
    import pytest
    from hearth.execution.lifecycle import Execution
    from hearth.observation.snapshot import snapshot
    from hearth.residents.models import Declaration, Refused
    from hearth.storage.artifacts import Artifacts
    from hearth.storage.database import Database
    from hearth.work.routines import Routines
    from hearth.work.service import Hearth

    database = Database(tmp_path / "hearth.db")
    database.initialize(runtime_kind="process_mock")
    hearth = Hearth(database, clock=lambda: 1_788_640_000)
    hearth.save_resident("reader", Declaration("Reader", "Read", 100000), expected_revision=0)
    routine = Routines(hearth).save(
        "daily",
        "reader",
        "Daily report",
        local_time="09:00",
        timezone="UTC",
        enabled=True,
        expected_revision=0,
    )
    pending = hearth.submit("pending", "reader", "Pending", expires_at=1_788_640_600)
    task = hearth.submit("first", "reader", "Read", expires_at=1_788_640_600)
    run = hearth.admit(task.task_id, reserve=10000)
    execution = Execution(hearth, Artifacts(tmp_path / "artifacts"))
    assert execution.prepare_start(run.id, run.owner_token)
    Maintenance(hearth).change_lifecycle(
        "archive", "reader", LifecycleChange(expected_revision=0, state="archived")
    )
    with pytest.raises(Refused, match="resident_archived"):
        hearth.admit(pending.task_id, reserve=10000)
    with (
        pytest.raises(Refused, match="resident_archived"),
        execution.dispatch_guard(
            run.id,
            run.owner_token,
            epoch=snapshot(hearth)["epoch"],
            input_digest=run.input_digest,
        ),
    ):
        pytest.fail("Archived resident must not reach actual dispatch")
    hearth.clock = lambda: routine["next_at"]
    assert Routines(hearth).tick() == []
    state = snapshot(hearth)
    assert state["occurrences"] == []
    assert state["residents"][0]["lifecycle"]["state"] == "archived"
    assert state["residents"][0]["presence"] == "starting"
    assert hearth.run(run.id).reserved == 10000
    assert hearth.run(run.id).finished_at is None


def test_archive_before_launch_and_during_unknown_execution_keep_truthful_holds(tmp_path):
    from hearth.observation.snapshot import snapshot

    app = create_app(tmp_path, TOKEN, supervise=False, scenario="hold")
    hearth = app.state.hearth
    hearth.clock = lambda: 1_788_640_000
    with TestClient(app) as client:
        client.post("/api/demo/reader", headers=AUTH).raise_for_status()
        task = hearth.submit("never-started", "reader", "Read", expires_at=1_788_640_600)
        run = hearth.admit(task.task_id, reserve=10000)
        Maintenance(hearth).change_lifecycle(
            "archive", "reader", LifecycleChange(expected_revision=0, state="archived")
        )
        app.state.executor.step()
        assert app.state.executor.runtime.inspect(run.id).status == "absent"
        assert hearth.run(run.id).launch_attempted == 0
        assert hearth.run(run.id).finished_at is None
        assert snapshot(hearth)["residents"][0]["unresolved_runs"] == 1
        app.state.execution.cancel(run.id)
        app.state.executor.step()
        assert hearth.run(run.id).status == "cancelled"
        assert hearth.run(run.id).actual_cost == 0
        assert snapshot(hearth)["residents"][0]["presence"] == "archived"


def test_coherent_configuration_conflict_preserves_every_other_owned_revision(tmp_path):
    from hearth.residents.memory import Memory

    app = create_app(tmp_path, TOKEN, supervise=False)
    with TestClient(app) as client:
        client.post("/api/demo/reader", headers=AUTH).raise_for_status()
        route = "/api/residents/reader/configuration"
        original = client.get(route, headers=AUTH)
        assert original.status_code == 200
        current = original.json()
        draft = {
            "expected_lifecycle_revision": current["lifecycle"]["revision"],
            "declaration": {**current["declaration"], "purpose": "New shared purpose"},
            "memory": {**current["memory"], "text": "Stale draft memory"},
        }
        Memory(app.state.hearth).save("reader", "A concurrent operator note", expected_revision=0)
        headers = {**AUTH, "Idempotency-Key": "configure"}
        refused = client.put(route, headers=headers, json=draft)
        assert refused.status_code == 409 and refused.json()["error"] == "revision_conflict"
        after = client.get(route, headers=AUTH).json()
        assert after["declaration"] == current["declaration"]
        assert after["memory"]["text"] == "A concurrent operator note"
        draft["memory"] = {**after["memory"], "text": "Intentionally revised memory"}
        saved = client.put(route, headers=headers, json=draft)
        assert saved.status_code == 200
        assert client.put(route, headers=headers, json=draft).json() == saved.json()
        final = client.get(route, headers=AUTH).json()
        assert final["declaration"]["purpose"] == "New shared purpose"
        assert final["declaration"]["expected_revision"] == 2
        assert final["memory"] == {"expected_revision": 2, "text": "Intentionally revised memory"}


def test_profile_edits_inputs_skills_and_routine_with_owning_revision_guards(tmp_path):
    app = create_app(tmp_path, TOKEN, supervise=False)
    app.state.hearth.clock = lambda: 1_788_640_000
    with TestClient(app) as client:
        client.post("/api/demo/reader", headers=AUTH).raise_for_status()
        source = client.post(
            "/api/input-sets",
            headers={**AUTH, "Idempotency-Key": "source"},
            json={"name": "Orchard", "notes": ["Fictional pears: 12"]},
        ).json()
        skill = client.post(
            "/api/skills",
            headers={**AUTH, "Idempotency-Key": "skill"},
            json={
                "name": "Count fruit",
                "description": "A synthetic report",
                "instructions": "Report the count.",
            },
        ).json()
        route = "/api/residents/reader/configuration"
        initial = client.get(route, headers=AUTH).json()
        body = {
            "expected_lifecycle_revision": initial["lifecycle"]["revision"],
            "inputs": {
                "expected_revision": 1,
                "input_sets": [{"input_set_id": source["input_set_id"]}],
            },
            "skills": {
                "expected_revision": 0,
                "skills": [{"skill_id": skill["skill_id"], "revision": 1}],
            },
            "routines": [
                {
                    "routine_id": "daily",
                    "expected_revision": 0,
                    "instruction": "Count fruit",
                    "local_time": "09:00",
                    "timezone": "UTC",
                    "enabled": True,
                }
            ],
        }
        response = client.put(route, headers={**AUTH, "Idempotency-Key": "configured"}, json=body)
        assert response.status_code == 200
        current = client.get(route, headers=AUTH).json()
        assert current["inputs"]["input_sets"] == body["inputs"]["input_sets"]
        assert current["skills"]["skills"] == body["skills"]["skills"]
        assert current["routines"][0]["expected_revision"] == 1
        stale = {
            "expected_lifecycle_revision": current["lifecycle"]["revision"],
            "declaration": {**current["declaration"], "purpose": "Must not overwrite"},
            "routines": body["routines"],
        }
        assert client.put(
            route, headers={**AUTH, "Idempotency-Key": "stale"}, json=stale
        ).json() == {"error": "revision_conflict"}
        assert client.get(route, headers=AUTH).json()["declaration"] == initial["declaration"]


def managed_fixture(tmp_path):
    import json

    from hearth.management.bootstrap import bootstrap
    from hearth.management.bridge import BoundRun, Bridge
    from hearth.observation.snapshot import snapshot

    app = create_app(tmp_path, TOKEN, supervise=False)
    hearth = app.state.hearth
    hearth.clock = lambda: 1_788_640_000
    karen = bootstrap(hearth)
    task = hearth.submit("manager", karen["resident_id"], "Manage", expires_at=1_788_640_600)
    run = hearth.admit(task.task_id, reserve=100000)
    app.state.execution.prepare_start(run.id, run.owner_token)
    bridge = Bridge(
        hearth, BoundRun(run.id, run.owner_token, snapshot(hearth)["epoch"], run.input_digest)
    )
    bridge.bind_thread("thread")
    bridge.bind_turn("thread", "turn")

    def call(tool, arguments, call_id):
        return bridge.call(
            dict(threadId="thread", turnId="turn", callId=call_id, tool=tool, arguments=arguments)
        )

    result = call(
        "hearth_residents_provision",
        {
            "operation_id": "child",
            "resident": {
                "name": "Reporter",
                "purpose": "Count fictional pears",
                "creation_reason": "A managed reporter",
                "execution_profile": "inline_mock",
                "daily_limit": 100000,
            },
        },
        "create",
    )
    assert result["success"]
    child = json.loads(result["contentItems"][0]["text"])["resident_id"]
    return app, karen, run, child, call


def test_scoped_manager_edits_pauses_archives_and_operator_transfer_revokes_old_owner(tmp_path):
    import json

    app, karen, run, child, call = managed_fixture(tmp_path)
    config = Maintenance(app.state.hearth).configuration(child)
    edited = call(
        "hearth_residents_configure",
        {
            "operation_id": "edit",
            "resident_id": child,
            "changes": {
                "expected_lifecycle_revision": config["lifecycle"]["revision"],
                "declaration": {**config["declaration"], "purpose": "Report pears precisely"},
            },
        },
        "edit",
    )
    assert edited["success"]
    assert (
        Maintenance(app.state.hearth).configuration(child)["declaration"]["purpose"]
        == "Report pears precisely"
    )
    args = {
        "operation_id": "pause",
        "resident_id": child,
        "change": {"expected_revision": config["lifecycle"]["revision"], "state": "paused"},
    }
    paused = call("hearth_residents_lifecycle", args, "pause")
    assert paused["success"]
    assert call("hearth_residents_lifecycle", args, "retry-pause") == paused
    value = json.loads(paused["contentItems"][0]["text"])
    assert value["actor"] == karen["resident_id"] and value["originating_run_id"] == run.id
    archived = call(
        "hearth_residents_lifecycle",
        {
            "operation_id": "archive-managed",
            "resident_id": child,
            "change": {"expected_revision": value["revision"], "state": "archived"},
        },
        "archive-managed",
    )
    assert archived["success"]
    value = json.loads(archived["contentItems"][0]["text"])
    assert value["state"] == "archived"
    with TestClient(app) as client:
        moved = client.put(
            "/api/residents/" + child + "/manager",
            headers={**AUTH, "Idempotency-Key": "transfer"},
            json={"expected_revision": value["revision"], "manager": "operator"},
        )
        assert moved.status_code == 200
        profile = client.get("/api/residents/" + child + "/profile", headers=AUTH).json()
        assert profile["manager"] == "operator" and profile["creator"] == karen["resident_id"]
        refused = call(
            "hearth_residents_lifecycle",
            {
                "operation_id": "archive",
                "resident_id": child,
                "change": {"expected_revision": moved.json()["revision"], "state": "archived"},
            },
            "foreign-owner",
        )
        assert not refused["success"] and "management_resident_out_of_scope" in str(refused)


def test_archive_keeps_running_reservation_and_held_backup_refuses_damaged_lifecycle(tmp_path):
    import pytest
    from hearth.inputs.demo import seed_reader
    from hearth.observation.snapshot import snapshot
    from hearth.residents.models import Refused
    from hearth.storage.backup import capture, restore

    app = create_app(tmp_path / "data", TOKEN, supervise=False, scenario="hold")
    hearth = app.state.hearth
    hearth.clock = lambda: 1_788_640_000
    seed_reader(hearth)
    task = hearth.submit("active", "reader", "Read", expires_at=1_788_640_600)
    run = hearth.admit(task.task_id, reserve=10000)
    app.state.executor.step()
    Maintenance(hearth).change_lifecycle(
        "archive", "reader", LifecycleChange(expected_revision=0, state="archived")
    )
    assert hearth.run(run.id).status == "running" and hearth.run(run.id).reserved == 10000
    assert not hearth.run(run.id).cancellation_requested
    assert snapshot(hearth)["residents"][0]["presence"] == "running"
    capture(tmp_path / "data", tmp_path / "backup")
    restore(tmp_path / "backup", tmp_path / "held")
    with TestClient(create_app(tmp_path / "held", TOKEN, supervise=False)) as held:
        state = held.get("/api/state", headers=AUTH).json()
        assert state["restore_hold"] and state["residents"][0]["unresolved_runs"] == 1
        assert state["residents"][0]["lifecycle"]["state"] == "archived"
        refused = held.put(
            "/api/residents/reader/manager",
            headers={**AUTH, "Idempotency-Key": "held-transfer"},
            json={"expected_revision": 1, "manager": "operator"},
        )
        assert refused.json() == {"error": "restored_copy_read_only"}
    with hearth.database.transaction(write=True) as db:
        db.execute("UPDATE resident_lifecycle_history SET sha256='changed' WHERE revision=1")
    assert snapshot(hearth)["residents"][0]["lifecycle"]["state"] == "unavailable"
    with pytest.raises(Refused, match="resident_lifecycle_corrupt"):
        capture(tmp_path / "data", tmp_path / "corrupt-backup")


def test_explicit_reader_profile_and_operator_transfer_are_manageable_without_inherited_grant(
    tmp_path,
):
    from hearth.management.authority import Management

    app, karen, _, _, call = managed_fixture(tmp_path)
    with TestClient(app) as client:
        client.post("/api/demo/reader", headers=AUTH).raise_for_status()
        profile = client.get("/api/residents/reader/profile", headers=AUTH)
        assert profile.status_code == 200
        assert profile.json()["creator"] == "operator"
        transfer = client.put(
            "/api/residents/reader/manager",
            headers={**AUTH, "Idempotency-Key": "transfer-reader"},
            json={"expected_revision": 0, "manager": karen["resident_id"]},
        )
        assert transfer.status_code == 200
        # Empty selection remains within the manager's original input grant.
        client.put(
            "/api/residents/reader/inputs",
            headers={**AUTH, "Idempotency-Key": "empty-reader"},
            json={"expected_revision": 1, "input_sets": []},
        ).raise_for_status()
        result = call(
            "hearth_residents_lifecycle",
            {
                "operation_id": "pause-reader",
                "resident_id": "reader",
                "change": {"expected_revision": transfer.json()["revision"], "state": "paused"},
            },
            "pause-reader",
        )
        assert result["success"]
        assert not Management(app.state.hearth).read("reader")["enabled"]


def test_competing_manager_and_operator_configuration_has_one_winner(tmp_path):
    from concurrent.futures import ThreadPoolExecutor

    from hearth.residents.maintenance import ConfigurationChange
    from hearth.residents.models import Refused

    app, _, _, child, call = managed_fixture(tmp_path)
    maintenance = Maintenance(app.state.hearth)
    original = maintenance.configuration(child)

    def draft(purpose):
        return {
            "expected_lifecycle_revision": original["lifecycle"]["revision"],
            "declaration": {**original["declaration"], "purpose": purpose},
        }

    def operator():
        try:
            maintenance.configure(
                "operator-edit", child, ConfigurationChange.model_validate(draft("Operator draft"))
            )
            return True
        except Refused as error:
            assert error.code == "revision_conflict"
            return False

    def manager():
        result = call(
            "hearth_residents_configure",
            {
                "operation_id": "manager-edit",
                "resident_id": child,
                "changes": draft("Manager draft"),
            },
            "manager-edit",
        )
        if not result["success"]:
            assert "revision_conflict" in str(result)
        return result["success"]

    with ThreadPoolExecutor(2) as pool:
        a, b = pool.submit(operator), pool.submit(manager)
        assert sorted([a.result(), b.result()]) == [False, True]
    saved = maintenance.configuration(child)
    assert saved["declaration"]["expected_revision"] == 2
    assert saved["declaration"]["purpose"] in {"Operator draft", "Manager draft"}


def test_unknown_launched_execution_keeps_hold_after_archive_and_restart(tmp_path):
    from hearth.inputs.demo import seed_reader
    from hearth.observation.snapshot import snapshot

    app = create_app(tmp_path, TOKEN, supervise=False, scenario="hold")
    hearth = app.state.hearth
    hearth.clock = lambda: 1_788_640_000
    seed_reader(hearth)
    task = hearth.submit("unknown", "reader", "Read", expires_at=1_788_640_600)
    run = hearth.admit(task.task_id, reserve=10000)
    app.state.executor.step()
    # Lost external runtime evidence is not proof of termination.
    (tmp_path / "mock-runtime" / (run.id + ".json")).unlink()
    app.state.executor.step()
    assert hearth.run(run.id).status == "interrupted"
    Maintenance(hearth).change_lifecycle(
        "archive", "reader", LifecycleChange(expected_revision=0, state="archived")
    )
    reopened = create_app(tmp_path, TOKEN, supervise=False, scenario="hold")
    reopened.state.executor.step()
    state = snapshot(reopened.state.hearth)
    assert state["residents"][0]["lifecycle"]["state"] == "archived"
    assert state["residents"][0]["presence"] == "interrupted"
    assert state["residents"][0]["unresolved_runs"] == 1
    assert reopened.state.hearth.run(run.id).finished_at is None
    assert reopened.state.hearth.run(run.id).reserved == 10000
    assert reopened.state.executor.runtime.inspect(run.id).status == "absent"


def test_unrelated_agent_and_forged_transfer_are_refused_without_changes(tmp_path):
    from hearth.inputs.demo import seed_reader

    app, _, _, child, call = managed_fixture(tmp_path)
    seed_reader(app.state.hearth)
    maintenance = Maintenance(app.state.hearth)
    before = maintenance.configuration("reader")
    unrelated = call(
        "hearth_residents_configure",
        {
            "operation_id": "unrelated",
            "resident_id": "reader",
            "changes": {
                "expected_lifecycle_revision": 0,
                "declaration": {**before["declaration"], "purpose": "Stolen ownership"},
            },
        },
        "unrelated",
    )
    assert not unrelated["success"] and "management_resident_out_of_scope" in str(unrelated)
    forged = call(
        "hearth_residents_lifecycle",
        {
            "operation_id": "forged",
            "resident_id": child,
            "change": {"expected_revision": 1, "state": "paused", "manager": "operator"},
        },
        "forged",
    )
    assert not forged["success"]
    assert maintenance.configuration("reader") == before
    with TestClient(app) as client:
        assert (
            client.put(
                "/api/residents/" + child + "/manager",
                headers={
                    "Authorization": "Bearer unrelated-agent",
                    "Idempotency-Key": "forged-http",
                },
                json={"expected_revision": 1, "manager": "operator"},
            ).status_code
            == 401
        )
    assert maintenance.lifecycle(child)["state"] == "ready"


def test_configuration_accepts_legal_unicode_groups_and_rejects_transport_overflow(tmp_path):
    import json

    with TestClient(create_app(tmp_path, TOKEN, supervise=False)) as client:
        client.post("/api/demo/reader", headers=AUTH).raise_for_status()
        route = "/api/residents/reader/configuration"
        before = client.get(route, headers=AUTH).json()
        body = {
            "expected_lifecycle_revision": 0,
            "declaration": {
                **before["declaration"],
                "name": "😀" * 100,
                "purpose": "😀" * 8000,
                "instructions": "😀" * 32000,
            },
            "memory": {
                "expected_revision": before["memory"]["expected_revision"],
                "text": "😀" * 32768,
            },
        }
        encoded = json.dumps(body, ensure_ascii=True)
        assert len(encoded) > 800000
        response = client.put(
            route,
            headers={**AUTH, "Idempotency-Key": "unicode", "Content-Type": "application/json"},
            content=encoded,
        )
        assert response.status_code == 200, response.text
        assert client.get(route, headers=AUTH).json()["memory"]["text"] == body["memory"]["text"]
        oversized = client.put(
            route, headers={**AUTH, "Idempotency-Key": "oversized"}, content=b" " * 1_500_001
        )
        assert oversized.status_code == 413
