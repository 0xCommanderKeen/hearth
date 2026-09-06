"""Complete resident provisioning through one repeatable application operation."""

from hearth.residents.provisioning import Provisioning
from hearth.storage.database import Database
from hearth.work.service import Hearth


def request():
    return dict(
        name="Reporter",
        purpose="Summarize synthetic notes",
        instructions="Be concise",
        initial_memory="Remember: use bullets",
        skills=[],
        execution_profile="inline_mock",
        input_sets=[{"input_set_id": "synthetic-reader-notes"}],
        daily_limit=100000,
        budget_timezone="Europe/Ljubljana",
        creation_reason="Daily report",
        manager="operator",
        routine={
            "instruction": "Summarize",
            "local_time": "09:00",
            "timezone": "Europe/Ljubljana",
            "enabled": True,
        },
        first_assignment={"instruction": "Summarize now"},
    )


def test_complete_provision_replay_and_restart_preserve_identity(tmp_path):
    db = Database(tmp_path / "hearth.db")
    db.initialize()
    hearth = Hearth(db)
    service = Provisioning(hearth)
    result = service.create("setup", request(), actor="operator")
    assert result["status"] == "ready"
    assert service.create("setup", request(), actor="operator") == result
    assert hearth.resident(result["resident_id"]).declaration.skill_text == "Be concise"
    assert hearth.task(result["task_id"]).status == "queued"
    assert Provisioning(Hearth(Database(db.path))).read("setup") == result


def test_failed_memory_setup_is_visible_and_retry_uses_same_resident(tmp_path):
    import pytest
    from hearth.residents.models import Refused

    db = Database(tmp_path / "data/hearth.db")
    db.initialize()
    (tmp_path / "outside").mkdir()
    (tmp_path / "data/memory").symlink_to(tmp_path / "outside", target_is_directory=True)
    service = Provisioning(Hearth(db))
    failed = service.create("setup", request(), actor="operator")
    assert failed["status"] == "failed" and failed["reason"] == "memory_missing_or_unsafe"
    with pytest.raises(Refused, match="resident_not_found"):
        service.hearth.resident(failed["resident_id"])
    (tmp_path / "data/memory").unlink()
    ready = service.create("setup", request(), actor="operator")
    assert ready["status"] == "ready" and ready["resident_id"] == failed["resident_id"]
    assert service.create("setup", request(), actor="operator") == ready


def test_provisioning_api_replay_profile_and_input_choices(tmp_path):
    from fastapi.testclient import TestClient
    from hearth.app import create_app

    auth = {"Authorization": "Bearer synthetic-provision-token", "Idempotency-Key": "setup"}
    with TestClient(create_app(tmp_path, "synthetic-provision-token", supervise=False)) as client:
        assert client.post("/api/residents/provision", json=request()).status_code == 401
        ready = client.post("/api/residents/provision", headers=auth, json=request())
        assert ready.status_code == 200
        result = ready.json()
        profile = client.get(
            "/api/residents/" + result["resident_id"] + "/profile", headers=auth
        ).json()
        assert profile["creator"] == "operator" and profile["creation_reason"] == "Daily report"
        state = client.get("/api/state", headers=auth).json()
        assert state["residents"][0]["profile"]["execution_profile"] == "inline_mock"
        assert state["provisioning"][0]["status"] == "ready"
        assert (
            client.post("/api/residents/provision", headers=auth, json=request()).json() == result
        )
        assert (
            client.post(
                "/api/residents/provision", headers=auth, json={**request(), "name": "Changed"}
            ).status_code
            == 409
        )
        assert (
            client.post(
                "/api/residents/provision", headers=auth, json={**request(), "creator": "forged"}
            ).status_code
            == 422
        )


def test_provisioned_resident_has_only_explicit_builtin_inputs(tmp_path):
    from hearth.authority.run_access import RunAccess

    db = Database(tmp_path / "hearth.db")
    db.initialize()
    hearth = Hearth(db)
    service = Provisioning(hearth)
    ready = service.create(
        "empty", {**request(), "input_sets": [], "routine": None}, actor="operator"
    )
    run = hearth.admit(ready["task_id"], reserve=10000)
    access = RunAccess(hearth)
    credential = access.issue(run.id, run.owner_token)
    assert access.context(credential.token, run.id)["notes"] == []


def test_concurrent_creation_limit_and_backup_preserve_complete_setup(tmp_path):
    from concurrent.futures import ThreadPoolExecutor

    import pytest
    from hearth.authority.household import Household
    from hearth.residents.memory import Memory
    from hearth.residents.models import Refused
    from hearth.skills.assignments import Assignments
    from hearth.skills.catalog import Skills
    from hearth.storage.backup import capture, restore

    db = Database(tmp_path / "data/hearth.db")
    db.initialize()
    hearth = Hearth(db)
    Household(hearth).save(
        daily_limit=1000000,
        timezone="Europe/Ljubljana",
        resident_limit=1,
        concurrency_limit=1,
        expected_revision=0,
    )
    skill = Skills(hearth).save(
        "skill",
        name="Summary",
        description="For notes",
        instructions="Write bullets",
        actor="operator",
    )
    payload = {**request(), "skills": [{"skill_id": skill["skill_id"], "revision": 1}]}
    service = Provisioning(hearth)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(
            pool.map(lambda key: service.create(key, payload, actor="operator"), ["one", "two"])
        )
    assert sorted(row["status"] for row in results) == ["failed", "ready"]
    ready = next(row for row in results if row["status"] == "ready")
    failed = next(row for row in results if row["status"] == "failed")
    assert failed["reason"] == "household_resident_limit"
    capture(db.path.parent, tmp_path / "backup")
    restore(tmp_path / "backup", tmp_path / "restored")
    copy = Hearth(Database(tmp_path / "restored/hearth.db"))
    assert Provisioning(copy).read(ready["command_id"]) == ready
    assert Memory(copy).read(ready["resident_id"])["text"] == "Remember: use bullets"
    assert Assignments(copy).read(ready["resident_id"])["skills"][0]["revision"] == 1
    with pytest.raises(Refused, match="restored_copy_read_only"):
        Provisioning(copy).retry(failed["command_id"], actor="operator")


def test_new_http_declaration_cannot_bypass_provisioning(tmp_path):
    from fastapi.testclient import TestClient
    from hearth.app import create_app

    auth = {"Authorization": "Bearer synthetic-provision-token"}
    with TestClient(create_app(tmp_path, "synthetic-provision-token", supervise=False)) as client:
        result = client.put(
            "/api/residents/incomplete",
            headers=auth,
            json={
                "name": "Bare",
                "purpose": "No profile",
                "daily_limit": 10000,
                "budget_timezone": "UTC",
                "skill_text": "",
                "expected_revision": 0,
            },
        )
        assert result.json() == {"error": "use_resident_provisioning"}
        assert client.get("/api/state", headers=auth).json()["residents"] == []


def test_backup_refuses_changed_immutable_provenance_or_first_task(tmp_path):
    import sqlite3

    import pytest
    from hearth.residents.models import Refused
    from hearth.storage.backup import capture

    db = Database(tmp_path / "data/hearth.db")
    db.initialize()
    service = Provisioning(Hearth(db))
    ready = service.create("setup", request(), actor="operator")
    with sqlite3.connect(db.path) as connection:
        connection.execute("UPDATE resident_profiles SET creator='forged'")
    with pytest.raises(Refused, match="provisioning_provenance_changed"):
        capture(db.path.parent, tmp_path / "bad-creator")
    with sqlite3.connect(db.path) as connection:
        connection.execute("UPDATE resident_profiles SET creator='operator'")
        connection.execute(
            "UPDATE resident_provisioning SET task_id=NULL WHERE command_id=?",
            (ready["command_id"],),
        )
    with pytest.raises(Refused, match="provisioning_work_changed"):
        capture(db.path.parent, tmp_path / "bad-task")


def test_invalid_references_never_leave_active_partial_resident(tmp_path):
    from hearth.observation.snapshot import snapshot

    db = Database(tmp_path / "hearth.db")
    db.initialize()
    service = Provisioning(Hearth(db))
    for key, change, reason in [
        ("profile", {"execution_profile": "arbitrary-provider"}, "execution_profile_unavailable"),
        ("input", {"input_sets": [{"input_set_id": "personal-notes"}]}, "input_set_unavailable"),
        ("manager", {"manager": "unknown"}, "manager_not_found"),
        ("skill", {"skills": [{"skill_id": "missing", "revision": 1}]}, "skill_revision_missing"),
        ("utf8", {"initial_memory": "é" * 131072}, "memory_too_large"),
    ]:
        result = service.create(key, {**request(), **change}, actor="operator")
        assert result["status"] == "failed" and result["reason"] == reason
    state = snapshot(service.hearth)
    assert state["residents"] == [] and state["tasks"] == [] and state["routines"] == []


def test_same_transaction_provisioning_records_active_resident_authority(tmp_path):
    import pytest
    from hearth.residents.models import Declaration, Refused

    db = Database(tmp_path / "hearth.db")
    db.initialize()
    hearth = Hearth(db)
    hearth.save_resident(
        "manager", Declaration("Manager", "Synthetic", 100000), expected_revision=0
    )
    task = hearth.submit(
        "manager-task", "manager", "Create a reporter", expires_at=int(hearth.clock()) + 600
    )
    run = hearth.admit(task.task_id, reserve=10000)
    service = Provisioning(hearth)
    with db.transaction(write=True) as connection:
        ready = service.create_in_transaction(
            connection,
            "child",
            {**request(), "manager": "manager"},
            actor="manager",
            originating_run_id=run.id,
        )
        assert (
            ready["status"] == "ready"
            and ready["creator"] == "manager"
            and ready["originating_run_id"] == run.id
        )
    with pytest.raises(Refused, match="provisioning_run_required"):
        service.create("forged", request(), actor="manager")
    with (
        db.transaction(write=True) as connection,
        pytest.raises(Refused, match="provisioning_manager_out_of_scope"),
    ):
        service.create_in_transaction(
            connection, "bad-manager", request(), actor="manager", originating_run_id=run.id
        )
