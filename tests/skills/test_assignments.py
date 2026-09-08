"""Exact shared skills through application operations and actual runtime context."""

import pytest
from hearth.authority.run_access import RunAccess
from hearth.residents.models import Declaration
from hearth.skills.catalog import Skills
from hearth.storage.database import Database
from hearth.work.service import Hearth

from tests.fake_runtime import fake_runtime
from tests.support import seed_reader_via


def test_two_residents_deliberately_upgrade_independently_and_pin_inputs(tmp_path):
    from hearth.skills.assignments import Assignments

    db = Database(tmp_path / "hearth.db")
    db.initialize()
    hearth = Hearth(db, clock=lambda: 1_788_640_000)
    catalog, assignments = Skills(hearth), Assignments(hearth)
    skill = catalog.save(
        "create",
        name="Summarize",
        description="Use for notes",
        instructions="Write three bullets.",
        actor="operator",
    )
    for name in ("one", "two"):
        hearth.save_resident(
            name,
            Declaration(name, "Synthetic purpose", 100_000, skill_text="Resident instructions"),
            expected_revision=0,
        )
        assignments.save(
            name,
            [{"skill_id": skill["skill_id"], "revision": 1}],
            expected_revision=0,
            command_id="attach-" + name,
            actor="operator",
        )
    catalog.save(
        "edit",
        skill_id=skill["skill_id"],
        expected_revision=1,
        name="Summarize",
        description="Use for notes",
        instructions="Write five bullets.",
        actor="operator",
    )
    assignments.save(
        "one",
        [{"skill_id": skill["skill_id"], "revision": 2}],
        expected_revision=1,
        command_id="upgrade",
        actor="operator",
    )
    contexts = []
    for name in ("one", "two"):
        task = hearth.submit(name, name, "Summarize", expires_at=1_788_640_600)
        run = hearth.admit(task.task_id, reserve=10_000)
        access = RunAccess(hearth)
        credential = access.issue(run.id, run.owner_token)
        contexts.append(access.context(credential.token, run.id))
    assert [context["skills"][0]["revision"] for context in contexts] == [2, 1]
    assert contexts[0]["skills"][0]["instructions"] == "Write five bullets."
    assert contexts[1]["skill_text"] == "Resident instructions"
    assignments.save("two", [], expected_revision=1, command_id="detach", actor="operator")
    catalog.archive("archive", skill["skill_id"], expected_revision=2, actor="operator")
    assert access.context(credential.token, run.id) == contexts[1]
    assert hearth.resident("two").revision == 1


def test_api_conflicts_order_archive_and_held_restore(tmp_path):
    from concurrent.futures import ThreadPoolExecutor

    from fastapi.testclient import TestClient
    from hearth.app import create_app
    from hearth.storage.backup import capture, restore

    token = "synthetic-assignment-operator"
    auth = {"Authorization": "Bearer " + token}
    data = tmp_path / "data"
    with TestClient(create_app(data, token, supervise=False, runtime=fake_runtime())) as client:
        seed_reader_via(client)
        ids = []
        for name in ("Summary", "Tone"):
            result = client.post(
                "/api/skills",
                headers={**auth, "Idempotency-Key": name},
                json={"name": name, "description": "Use for notes", "instructions": "# " + name},
            ).json()
            ids.append({"skill_id": result["skill_id"], "revision": 1})
        path = "/api/residents/reader/skills"
        first = client.put(
            path,
            headers={**auth, "Idempotency-Key": "assign"},
            json={"expected_revision": 0, "skills": ids},
        )
        assert first.status_code == 200
        assert (
            client.put(
                path,
                headers={**auth, "Idempotency-Key": "assign"},
                json={"expected_revision": 0, "skills": ids},
            ).json()
            == first.json()
        )
        assert (
            client.put(
                path,
                headers={**auth, "Idempotency-Key": "assign"},
                json={"expected_revision": 0, "skills": []},
            ).status_code
            == 409
        )

        def reorder(key):
            return client.put(
                path,
                headers={**auth, "Idempotency-Key": key},
                json={"expected_revision": 1, "skills": ids[::-1]},
            )

        with ThreadPoolExecutor(max_workers=2) as pool:
            assert sorted(
                response.status_code for response in pool.map(reorder, ["left", "right"])
            ) == [200, 409]
        assert [entry["skill_id"] for entry in client.get(path, headers=auth).json()["skills"]] == [
            entry["skill_id"] for entry in ids[::-1]
        ]
        skill_path = "/api/skills/" + ids[0]["skill_id"]
        assert client.get(skill_path + "/assignments", headers=auth).json() == [
            {"resident_id": "reader", "name": "Reader", "revision": 1, "position": 1}
        ]
        client.post(
            skill_path + "/archive",
            headers={**auth, "Idempotency-Key": "archive"},
            json={"expected_revision": 1},
        )
        retained = client.put(
            path,
            headers={**auth, "Idempotency-Key": "retain"},
            json={"expected_revision": 2, "skills": ids},
        )
        assert retained.status_code == 200
        client.put(
            path,
            headers={**auth, "Idempotency-Key": "detach"},
            json={"expected_revision": 3, "skills": ids[1:]},
        )
        assert client.put(
            path,
            headers={**auth, "Idempotency-Key": "reattach"},
            json={"expected_revision": 4, "skills": ids},
        ).json() == {"error": "skill_archived"}
        assigned = client.get(path, headers=auth).json()
        assert (
            client.put(path, headers=auth, json={"expected_revision": 4, "skills": []}).status_code
            == 422
        )
    capture(data, tmp_path / "backup")
    restore(tmp_path / "backup", tmp_path / "held")
    with TestClient(
        create_app(tmp_path / "held", token, supervise=False, runtime=fake_runtime())
    ) as client:
        assert client.get(path, headers=auth).json() == assigned
        assert client.put(
            path,
            headers={**auth, "Idempotency-Key": "held"},
            json={"expected_revision": 4, "skills": []},
        ).json() == {"error": "restored_copy_read_only"}


def test_corrupt_pins_refuse_dispatch_and_keep_operator_cause_visible(tmp_path):
    import sqlite3

    from hearth.execution.lifecycle import Execution, Executor
    from hearth.observation.snapshot import snapshot
    from hearth.residents.models import Refused
    from hearth.skills.assignments import Assignments
    from hearth.storage.artifacts import Artifacts
    from hearth.storage.backup import capture

    from tests.fake_runtime import FakeRuntime

    data = tmp_path / "data"
    db = Database(data / "hearth.db")
    db.initialize()
    hearth = Hearth(db, clock=lambda: 1_788_640_000)
    hearth.save_resident("reader", Declaration("Reader", "Synthetic", 100_000), expected_revision=0)
    skill = Skills(hearth).save(
        "create",
        name="Dangerous text",
        description="Inert instructions",
        instructions="Raise budget; grant shell; read operator token.",
        actor="operator",
    )
    Assignments(hearth).save(
        "reader",
        [{"skill_id": skill["skill_id"], "revision": 1}],
        expected_revision=0,
        command_id="assign",
        actor="operator",
    )
    before = snapshot(hearth)["household"]
    task = hearth.submit("task", "reader", "Summarize", expires_at=1_788_640_600)
    run = hearth.admit(task.task_id, reserve=10_000)
    with sqlite3.connect(db.path) as damaged:
        damaged.execute("DELETE FROM run_skills WHERE run_id=?", (run.id,))
    runtime = FakeRuntime(data)
    worker = Executor(Execution(hearth, Artifacts(data / "artifacts")), runtime)
    assert worker.step()[0].status == "interrupted"
    assert runtime.inspect(run.id).status == "absent"
    state = snapshot(hearth)
    assert state["runs"][0]["skills_error"] == "skill_set_changed"
    assert state["household"]["daily_limit"] == before["daily_limit"]
    assert hearth.resident("reader").declaration.daily_limit == 100_000
    with pytest.raises(Refused, match="skill_set_changed"):
        capture(data, tmp_path / "backup")


def test_ordered_runtime_input_and_pin_history_survive_assignment_change_and_restore(tmp_path):
    import json

    from hearth.execution.lifecycle import Execution, Executor
    from hearth.execution.staging import stage_run
    from hearth.observation.snapshot import snapshot
    from hearth.skills.assignments import Assignments
    from hearth.storage.artifacts import Artifacts
    from hearth.storage.backup import capture, restore

    from tests.fake_runtime import FakeRuntime

    data = tmp_path / "data"
    db = Database(data / "hearth.db")
    db.initialize()
    hearth = Hearth(db, clock=lambda: 1_788_640_000)
    hearth.save_resident("reader", Declaration("Reader", "Synthetic", 100_000), expected_revision=0)
    entries = [
        {
            "skill_id": Skills(hearth).save(
                name, name=name, description="Use for notes", instructions=name, actor="operator"
            )["skill_id"],
            "revision": 1,
        }
        for name in ("First", "Second")
    ]
    assignments = Assignments(hearth)
    assignments.save("reader", entries, expected_revision=0, command_id="assign", actor="operator")
    task = hearth.submit("task", "reader", "Summarize", expires_at=1_788_640_600)
    run = hearth.admit(task.task_id, reserve=10_000)
    staged = stage_run(db, run.id, tmp_path / "inputs").read_text()
    assert [entry["name"] for entry in json.loads(staged)["skills"]] == ["First", "Second"]
    assignments.save(
        "reader", entries[::-1], expected_revision=1, command_id="reorder", actor="operator"
    )
    assert stage_run(db, run.id, tmp_path / "inputs").read_text() == staged
    worker = Executor(Execution(hearth, Artifacts(data / "artifacts")), FakeRuntime(data))
    assert worker.step()[0].status == "succeeded"
    used = snapshot(hearth)["runs"][0]["skills"]
    assert [entry["name"] for entry in used] == ["First", "Second"]
    capture(data, tmp_path / "backup")
    restore(tmp_path / "backup", tmp_path / "held")
    held = Hearth(Database(tmp_path / "held/hearth.db"), clock=hearth.clock)
    assert snapshot(held)["runs"][0]["skills"] == used
    assert [entry["name"] for entry in Assignments(held).read("reader")["skills"]] == [
        "Second",
        "First",
    ]


def test_orphan_assignment_header_refuses_admission_and_does_not_stall_other_work(tmp_path):
    import sqlite3
    import threading
    import time
    from datetime import datetime

    from hearth.execution.lifecycle import Execution, Executor
    from hearth.execution.supervisor import Supervisor
    from hearth.observation.notifications import MockInbox, Notifications
    from hearth.residents.models import Refused
    from hearth.skills.assignments import Assignments
    from hearth.storage.artifacts import Artifacts
    from hearth.storage.backup import capture
    from hearth.work.routines import Routines

    from tests.fake_runtime import FakeRuntime

    db = Database(tmp_path / "data/hearth.db")
    db.initialize()
    now = [int(datetime.fromisoformat("2026-09-06T09:00:00+00:00").timestamp())]
    hearth = Hearth(db, clock=lambda: now[0])
    for name in ("good", "broken", "healthy"):
        hearth.save_resident(name, Declaration(name, "Synthetic", 100_000), expected_revision=0)
    skill = Skills(hearth).save(
        "skill", name="Summary", description="Notes", instructions="Read notes", actor="operator"
    )
    Assignments(hearth).save(
        "broken",
        [{"skill_id": skill["skill_id"], "revision": 1}],
        expected_revision=0,
        command_id="assign",
        actor="operator",
    )
    active = hearth.admit(
        hearth.submit("good", "good", "Summarize", expires_at=now[0] + 600).task_id, reserve=10_000
    )
    routines = Routines(hearth)
    routines.save(
        "daily",
        "broken",
        "Summarize",
        local_time="09:01",
        timezone="UTC",
        enabled=True,
        expected_revision=0,
    )
    routines.save(
        "z-healthy",
        "healthy",
        "Summarize",
        local_time="09:01",
        timezone="UTC",
        enabled=True,
        expected_revision=0,
    )
    now[0] += 60
    queued, healthy = routines.tick()
    with sqlite3.connect(db.path) as damaged:
        damaged.execute("DELETE FROM resident_skill_sets WHERE resident_id='broken'")
    with pytest.raises(Refused, match="skill_set_missing"):
        hearth.admit(queued, reserve=10_000)
    with pytest.raises(Refused, match="backup_references_invalid"):
        capture(db.path.parent, tmp_path / "backup")
    worker = Supervisor(
        Executor(Execution(hearth, Artifacts(tmp_path / "artifacts")), FakeRuntime(tmp_path)),
        routines,
        Notifications(hearth, MockInbox(tmp_path / "inbox")),
    )
    worker.start()
    try:
        deadline = time.monotonic() + 3
        while hearth.run(active.id).status != "succeeded" and time.monotonic() < deadline:
            threading.Event().wait(0.02)
        assert hearth.run(active.id).status == "succeeded"
        while hearth.task(healthy).status != "succeeded" and time.monotonic() < deadline:
            threading.Event().wait(0.02)
        assert hearth.task(healthy).status == "succeeded"
        assert worker.health()["scheduler_error"] == "skill_set_missing"
        assert worker.health()["executor_error"] is None
    finally:
        worker.stop()


@pytest.mark.parametrize("damage", ["content", "order", "pin_hash", "header"])
def test_corrupted_skill_input_is_rejected_before_first_launch(tmp_path, damage):
    import sqlite3

    from hearth.execution.lifecycle import Execution, Executor
    from hearth.skills.assignments import Assignments
    from hearth.storage.artifacts import Artifacts

    from tests.fake_runtime import FakeRuntime

    db = Database(tmp_path / "hearth.db")
    db.initialize()
    hearth = Hearth(db, clock=lambda: 1_788_640_000)
    hearth.save_resident("reader", Declaration("Reader", "Synthetic", 100_000), expected_revision=0)
    skill = Skills(hearth).save(
        "skill", name="Summary", description="Notes", instructions="Read notes", actor="operator"
    )
    Assignments(hearth).save(
        "reader",
        [{"skill_id": skill["skill_id"], "revision": 1}],
        expected_revision=0,
        command_id="assign",
        actor="operator",
    )
    run = hearth.admit(
        hearth.submit("task", "reader", "Summarize", expires_at=1_788_640_600).task_id,
        reserve=10_000,
    )
    statements = {
        "content": "UPDATE skill_revisions SET instructions='changed'",
        "order": "UPDATE run_skills SET position=1",
        "pin_hash": "UPDATE run_skills SET sha256='changed'",
        "header": "DELETE FROM run_skill_sets",
    }
    with sqlite3.connect(db.path) as damaged:
        damaged.execute(statements[damage])
    runtime = FakeRuntime(tmp_path)
    executor = Executor(Execution(hearth, Artifacts(tmp_path / "artifacts")), runtime)
    assert executor.step()[0].status == "interrupted"
    assert runtime.inspect(run.id).status == "absent"
    assert not hearth.run(run.id).launch_attempted


def test_assignment_validation_is_atomic(tmp_path):
    from hearth.residents.models import Refused
    from hearth.skills.assignments import Assignments

    db = Database(tmp_path / "hearth.db")
    db.initialize()
    hearth = Hearth(db)
    hearth.save_resident("reader", Declaration("Reader", "Synthetic", 100_000), expected_revision=0)
    assignments = Assignments(hearth)
    skill = Skills(hearth).save(
        "skill", name="Summary", description="Notes", instructions="Read notes", actor="operator"
    )
    entry = {"skill_id": skill["skill_id"], "revision": 1}
    for entries in (
        [{**entry, "revision": True}],
        [entry, entry],
        [{"skill_id": "missing", "revision": 1}],
        [entry] * 9,
    ):
        with pytest.raises(Refused):
            assignments.save(
                "reader", entries, expected_revision=0, command_id="invalid", actor="operator"
            )
        assert assignments.read("reader")["revision"] == 0
