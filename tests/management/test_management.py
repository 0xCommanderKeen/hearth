"""Management authority through operator HTTP and the private runtime operation seam."""

import pytest
from fastapi.testclient import TestClient
from hearth.app import create_app

TOKEN = "synthetic-management-operator"
AUTH = {"Authorization": "Bearer " + TOKEN}


def test_karen_setup_grant_and_normal_skill_preserve_operator_edits(tmp_path):
    with TestClient(create_app(tmp_path, TOKEN, supervise=False)) as client:
        assert client.post("/api/management/bootstrap").status_code == 401
        first = client.post("/api/management/bootstrap", headers=AUTH)
        assert first.status_code == 200
        receipt = first.json()
        resident_id, skill_id = receipt["resident_id"], receipt["skill_id"]
        state = client.get("/api/state", headers=AUTH).json()
        assert len(state["residents"]) == 1
        assert state["residents"][0]["name"] == "Karen"
        skill = client.get("/api/skills/" + skill_id, headers=AUTH).json()
        assert skill["name"] == "Create residents"
        assert "reuse" in skill["instructions"].lower()
        assignment = client.get(f"/api/residents/{resident_id}/skills", headers=AUTH).json()
        assert assignment["skills"][0]["skill_id"] == skill_id
        path = f"/api/residents/{resident_id}/management"
        grant = client.get(path, headers=AUTH).json()
        assert grant["enabled"] and grant["profiles"] == ["inline_mock"]
        assert "create_residents" in grant["capabilities"]
        disabled = {
            key: value for key, value in grant.items() if key not in {"resident_id", "revision"}
        }
        disabled.update(enabled=False, expected_revision=grant["revision"])
        assert client.put(path, headers=AUTH, json=disabled).status_code == 200
        edit = client.put(
            "/api/skills/" + skill_id,
            headers={**AUTH, "Idempotency-Key": "operator-edit"},
            json={
                "name": "Create residents",
                "description": "Operator's instructions",
                "instructions": "Inspect before creating; preserve this edit.",
                "expected_revision": 1,
            },
        )
        assert edit.status_code == 200
        assert client.post("/api/management/bootstrap", headers=AUTH).json() == receipt
        assert not client.get(path, headers=AUTH).json()["enabled"]
        assert client.get("/api/skills/" + skill_id, headers=AUTH).json()["revision"] == 2
        assert len(client.get("/api/state", headers=AUTH).json()["residents"]) == 1


def test_private_tool_call_provisions_once_and_starts_initial_work(tmp_path):
    import json

    from hearth.management.authority import Management
    from hearth.management.bootstrap import bootstrap
    from hearth.management.bridge import BoundRun, Bridge
    from hearth.observation.snapshot import snapshot

    app = create_app(tmp_path, TOKEN, supervise=False)
    hearth = app.state.hearth
    karen = bootstrap(hearth)
    task = hearth.submit(
        "manager-task",
        karen["resident_id"],
        "Create a reporter",
        expires_at=int(hearth.clock()) + 600,
    )
    run = hearth.admit(task.task_id, reserve=100000)
    app.state.execution.prepare_start(run.id, run.owner_token)
    bound = BoundRun(run.id, run.owner_token, snapshot(hearth)["epoch"], run.input_digest)
    bridge = Bridge(hearth, bound)
    bridge.bind_thread("synthetic-thread")
    bridge.bind_turn("synthetic-thread", "synthetic-turn")
    params = dict(
        threadId="synthetic-thread",
        turnId="synthetic-turn",
        callId="call-1",
        namespace=None,
        tool="hearth_residents_provision",
        arguments=dict(
            operation_id="reporter",
            resident=dict(
                name="Reporter",
                purpose="Summarize synthetic data",
                creation_reason="Requested by operator through Karen",
                execution_profile="inline_mock",
                daily_limit=100000,
                first_assignment={"instruction": "First report"},
            ),
            start_first_assignment=True,
            reserve=10000,
        ),
    )
    response = bridge.call(params)
    assert response["success"] is True
    receipt = json.loads(response["contentItems"][0]["text"])
    assert receipt["status"] == "ready" and receipt["run_id"]
    assert bridge.call(params) == response
    assert bridge.call({**params, "callId": "repeated-native-call"}) == response
    state = snapshot(hearth)
    assert len(state["residents"]) == 2 and len(state["tasks"]) == 2 and len(state["runs"]) == 2
    child = next(row for row in state["residents"] if row["id"] == receipt["resident_id"])
    assert child["profile"]["creator"] == karen["resident_id"]
    assert child["profile"]["originating_run_id"] == run.id
    assert not Management(hearth).read(child["id"])["enabled"]
    forged = bridge.call(
        {
            **params,
            "callId": "call-1",
            "arguments": {**params["arguments"], "operation_id": "other"},
        }
    )
    assert not forged["success"] and "management_call_conflict" in forged["contentItems"][0]["text"]


def test_catalog_reuse_is_scoped_and_unrelated_work_is_refused(tmp_path):
    import json

    from hearth.inputs.catalog import Inputs
    from hearth.management.bootstrap import bootstrap
    from hearth.management.bridge import BoundRun, Bridge
    from hearth.observation.snapshot import snapshot
    from hearth.residents.models import Declaration

    app = create_app(tmp_path, TOKEN, supervise=False)
    hearth = app.state.hearth
    allowed = Inputs(hearth).save(
        "orchard", name="Orchard", notes=["Synthetic permitted pears"], actor="operator"
    )
    karen = bootstrap(hearth)
    hidden = Inputs(hearth).save(
        "hidden", name="Hidden", notes=["Do not expose this unrelated source"], actor="operator"
    )
    hearth.save_resident(
        "unrelated",
        Declaration("Unrelated", "Existing summary resident", 100000),
        expected_revision=0,
    )
    task = hearth.submit(
        "manager-task",
        karen["resident_id"],
        "Inspect and reuse",
        expires_at=int(hearth.clock()) + 600,
    )
    run = hearth.admit(task.task_id, reserve=100000)
    app.state.execution.prepare_start(run.id, run.owner_token)
    bridge = Bridge(
        hearth, BoundRun(run.id, run.owner_token, snapshot(hearth)["epoch"], run.input_digest)
    )
    bridge.bind_thread("thread")
    bridge.bind_turn("thread", "turn")

    def call(name, arguments, call_id):
        return bridge.call(
            dict(threadId="thread", turnId="turn", callId=call_id, tool=name, arguments=arguments)
        )

    catalog = call("hearth_catalog", {"query": ""}, "catalog")
    assert catalog["success"]
    result = json.loads(catalog["contentItems"][0]["text"])
    assert [row["input_set_id"] for row in result["input_sets"]] == [allowed["input_set_id"]]
    assert hidden["input_set_id"] not in str(result) and "Do not expose" not in str(result)
    assert next(row for row in result["residents"] if row["id"] == "unrelated")["managed"] is False
    assert run.owner_token not in str(result) and TOKEN not in str(result)
    refused = call(
        "hearth_work_assign",
        {
            "operation_id": "unrelated-task",
            "resident_id": "unrelated",
            "instruction": "Run now",
            "start": True,
            "reserve": 10000,
        },
        "foreign",
    )
    assert not refused["success"] and "management_resident_out_of_scope" in str(refused)
    assert len(snapshot(hearth)["tasks"]) == 1


def manager_runtime(tmp_path, *, max_residents=5, max_reserve=500000):
    from hearth.management.authority import Management
    from hearth.management.bootstrap import bootstrap
    from hearth.management.bridge import BoundRun, Bridge
    from hearth.observation.snapshot import snapshot

    app = create_app(tmp_path, TOKEN, supervise=False)
    hearth = app.state.hearth
    hearth.clock = lambda: 1788640000
    karen = bootstrap(hearth)
    policy = Management(hearth).read(karen["resident_id"])
    policy = {key: value for key, value in policy.items() if key not in {"resident_id", "revision"}}
    Management(hearth).save(
        karen["resident_id"],
        {
            **policy,
            "max_residents": max_residents,
            "max_reserve": max_reserve,
            "expected_revision": 1,
        },
    )
    task = hearth.submit(
        "manager", karen["resident_id"], "Create", expires_at=int(hearth.clock()) + 600
    )
    run = hearth.admit(task.task_id, reserve=100000)
    app.state.execution.prepare_start(run.id, run.owner_token)
    bound = BoundRun(run.id, run.owner_token, snapshot(hearth)["epoch"], run.input_digest)
    bridge = Bridge(hearth, bound)
    bridge.bind_thread("thread")
    bridge.bind_turn("thread", "turn")
    return app, karen, run, bound, bridge


def provision_call(call_id="call", **changes):
    resident = dict(
        name="Reporter",
        purpose="Synthetic summary",
        creation_reason="Requested setup",
        execution_profile="inline_mock",
        daily_limit=100000,
    )
    return dict(
        threadId="thread",
        turnId="turn",
        callId=call_id,
        tool="hearth_residents_provision",
        namespace=None,
        arguments={"operation_id": call_id, "resident": resident, **changes},
    )


def test_reuse_managed_resident_assigns_once_and_reports_durable_work(tmp_path):
    import json

    app, _, _, _, bridge = manager_runtime(tmp_path)
    created = bridge.call(provision_call())
    resident_id = json.loads(created["contentItems"][0]["text"])["resident_id"]
    params = dict(
        threadId="thread",
        turnId="turn",
        callId="reuse",
        tool="hearth_work_assign",
        arguments=dict(
            operation_id="reuse",
            resident_id=resident_id,
            instruction="Summarize the permitted fictional notes",
            start=True,
            reserve=10000,
        ),
    )
    assigned = bridge.call(params)
    assert assigned["success"]
    replay = bridge.call({**params, "callId": "reuse-again"})
    assert replay == assigned
    receipt = json.loads(assigned["contentItems"][0]["text"])
    assert app.state.hearth.run(receipt["run_id"]).resident_id == resident_id
    inspected = bridge.call(
        dict(
            threadId="thread",
            turnId="turn",
            callId="inspect",
            tool="hearth_residents_read",
            arguments={"resident_id": resident_id},
        )
    )
    tasks = json.loads(inspected["contentItems"][0]["text"])["tasks"]
    assert len(tasks) == 1 and tasks[0]["run_id"] == receipt["run_id"]


def test_revoked_grant_refuses_even_previously_completed_call_without_new_effect(tmp_path):
    from hearth.management.authority import Management
    from hearth.observation.snapshot import snapshot

    app, karen, _, _, bridge = manager_runtime(tmp_path)
    assert bridge.call(provision_call())["success"]
    current = Management(app.state.hearth).read(karen["resident_id"])
    policy = {
        key: value for key, value in current.items() if key not in {"resident_id", "revision"}
    }
    Management(app.state.hearth).save(
        karen["resident_id"], {**policy, "enabled": False, "expected_revision": 2}
    )
    refused = bridge.call(provision_call())
    assert not refused["success"] and "management_grant_changed_or_revoked" in str(refused)
    assert len(snapshot(app.state.hearth)["residents"]) == 2


def test_concurrent_management_creation_obeys_one_shared_count_and_rolls_back_audit_failure(
    tmp_path,
):
    import sqlite3
    from concurrent.futures import ThreadPoolExecutor

    import pytest
    from hearth.observation.snapshot import snapshot

    app, _, _, _, bridge = manager_runtime(tmp_path, max_residents=1)
    with app.state.hearth.database.transaction(write=True) as db:
        db.execute(
            "CREATE TRIGGER deny_management BEFORE INSERT ON audit "
            "WHEN NEW.kind='management.operation_completed' "
            "BEGIN SELECT RAISE(ABORT,'injected audit failure'); END"
        )
    with pytest.raises(sqlite3.IntegrityError, match="injected audit failure"):
        bridge.call(provision_call("failed-audit"))
    assert len(snapshot(app.state.hearth)["residents"]) == 1
    with app.state.hearth.database.transaction(write=True) as db:
        db.execute("DROP TRIGGER deny_management")
    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(lambda name: bridge.call(provision_call(name)), ["one", "two"]))
    assert sorted(result["success"] for result in results) == [False, True]
    assert "management_resident_count_limit" in str(results)
    assert len(snapshot(app.state.hearth)["residents"]) == 2


@pytest.mark.parametrize(
    "damage,reason",
    [
        ("owner", "management_run_ownership_lost"),
        ("epoch", "management_epoch_changed"),
        ("input", "management_input_changed"),
        ("expired", "management_access_expired"),
        ("cancelled", "management_run_inactive"),
        ("finished", "management_run_inactive"),
        ("declaration", "management_declaration_changed"),
        ("thread", "management_thread_mismatch"),
        ("turn", "management_turn_mismatch"),
        ("tool", "management_tool_not_permitted"),
    ],
)
def test_runtime_binding_refuses_stale_foreign_and_inactive_calls(tmp_path, damage, reason):
    from dataclasses import replace

    from hearth.management.bridge import Bridge
    from hearth.observation.snapshot import snapshot
    from hearth.residents.models import Declaration

    app, karen, run, bound, bridge = manager_runtime(tmp_path)
    params = provision_call()
    hearth = app.state.hearth
    if damage in {"owner", "epoch", "input"}:
        key = {"owner": "owner_token", "epoch": "epoch", "input": "input_digest"}[damage]
        bridge = Bridge(hearth, replace(bound, **{key: "foreign"}))
    elif damage == "expired":
        hearth.clock = lambda: 1788640600
    elif damage == "cancelled":
        app.state.execution.cancel(run.id)
    elif damage == "finished":
        app.state.executor.step()
    elif damage == "declaration":
        hearth.save_resident(
            karen["resident_id"],
            Declaration("Karen", "Operator changed purpose", 2000000),
            expected_revision=1,
        )
    elif damage in {"thread", "turn"}:
        params[damage + "Id"] = "foreign"
    elif damage == "tool":
        params["tool"] = "exec_command"
    result = bridge.call(params)
    assert not result["success"] and reason in str(result)
    assert len(snapshot(hearth)["residents"]) == 1


@pytest.mark.parametrize(
    "change,reason",
    [
        ({"manager": "operator"}, "management_identity_is_derived"),
        ({"creator": "operator"}, "management_identity_is_derived"),
        ({"capabilities": ["manage_everything"]}, "management_invalid_arguments"),
        ({"execution_profile": "shell"}, "management_profile_not_permitted"),
        ({"daily_limit": 1000001}, "management_resident_budget_limit"),
        ({"input_sets": [{"input_set_id": "unrelated"}]}, "management_input_not_permitted"),
    ],
)
def test_escalating_provision_arguments_are_specific_refusals(tmp_path, change, reason):
    from hearth.observation.snapshot import snapshot

    app, _, _, _, bridge = manager_runtime(tmp_path)
    params = provision_call()
    params["arguments"]["resident"].update(change)
    result = bridge.call(params)
    assert not result["success"] and reason in str(result)
    assert len(snapshot(app.state.hearth)["residents"]) == 1


def test_management_routine_must_fit_ordinary_scheduled_reservation(tmp_path):
    from hearth.observation.snapshot import snapshot

    app, _, _, _, bridge = manager_runtime(tmp_path, max_reserve=1000)
    params = provision_call(reserve=1000)
    params["arguments"]["resident"]["routine"] = dict(
        instruction="Report fictional notes",
        local_time="09:00",
        timezone="Europe/Ljubljana",
        enabled=True,
    )
    result = bridge.call(params)
    assert not result["success"] and "management_reservation_limit" in str(result)
    assert len(snapshot(app.state.hearth)["residents"]) == 1
    with app.state.hearth.database.transaction() as db:
        assert db.execute("SELECT COUNT(*) FROM routines").fetchone()[0] == 0


def test_permitted_management_routine_uses_the_advertised_reservation(tmp_path):
    import json

    from hearth.work.routines import Routines

    app, _, _, _, bridge = manager_runtime(tmp_path, max_reserve=10000)
    params = provision_call(reserve=10000)
    params["arguments"]["resident"]["routine"] = dict(
        instruction="Report fictional notes",
        local_time="09:00",
        timezone="Europe/Ljubljana",
        enabled=True,
    )
    result = bridge.call(params)
    assert result["success"]
    created = json.loads(result["contentItems"][0]["text"])
    with app.state.hearth.database.transaction() as db:
        next_at = db.execute(
            "SELECT next_at FROM routines WHERE id=?", (created["routine_id"],)
        ).fetchone()[0]
    app.state.hearth.clock = lambda: next_at
    routines = Routines(app.state.hearth)
    tasks = routines.tick()
    routines.admit_queued()
    with app.state.hearth.database.transaction() as db:
        run = db.execute("SELECT reserved FROM runs WHERE task_id=?", (tasks[0],)).fetchone()
    assert run["reserved"] == 10000


def test_long_task_status_inspection_fits_native_response_boundary(tmp_path):
    import json

    from hearth.integrations.codex.app_server_transport import _tool_response

    app, _, _, _, bridge = manager_runtime(tmp_path)
    created = bridge.call(provision_call())
    resident_id = json.loads(created["contentItems"][0]["text"])["resident_id"]
    for number in range(10):
        app.state.hearth.submit(
            str(number), resident_id, "x" * 32000, expires_at=int(app.state.hearth.clock()) + 600
        )
    inspected = bridge.call(
        dict(
            threadId="thread",
            turnId="turn",
            callId="inspect",
            tool="hearth_residents_read",
            arguments={"resident_id": resident_id},
        )
    )
    assert inspected["success"]
    assert _tool_response(inspected) == inspected
    tasks = json.loads(inspected["contentItems"][0]["text"])["tasks"]
    assert len(tasks) == 10 and all(task["id"] and task["status"] == "queued" for task in tasks)
    assert all(task["instruction_truncated"] for task in tasks)


def test_oversized_native_result_refuses_and_rolls_back_creation(tmp_path):
    from hearth.integrations.codex.app_server_transport import _tool_response
    from hearth.observation.snapshot import snapshot

    app, _, _, _, bridge = manager_runtime(tmp_path)
    params = provision_call()
    params["arguments"]["resident"]["initial_memory"] = "\x01" * 40000
    result = bridge.call(params)
    assert not result["success"] and "management_result_too_large" in str(result)
    assert _tool_response(result) == result
    assert len(snapshot(app.state.hearth)["residents"]) == 1


def test_maximum_unicode_skill_is_read_completely_and_catalog_stays_bounded(tmp_path):
    import json

    from hearth.integrations.codex.app_server_transport import _tool_response
    from hearth.skills.catalog import Skills

    app, _, _, _, bridge = manager_runtime(tmp_path)
    skills = Skills(app.state.hearth)
    instructions = "🌳" * 32000
    skill = skills.save(
        "unicode",
        name="🌳" * 120,
        description="🌳" * 2000,
        instructions=instructions,
        actor="operator",
    )
    read = bridge.call(
        dict(
            threadId="thread",
            turnId="turn",
            callId="skill",
            tool="hearth_skills_read",
            arguments={"skill_id": skill["skill_id"], "revision": 1},
        )
    )
    assert read["success"] and _tool_response(read) == read
    assert json.loads(read["contentItems"][0]["text"])["instructions"] == instructions
    for number in range(49):
        skills.save(
            f"catalog-{number}",
            name=f"{number:02d}" + "🌳" * 118,
            description="🌳" * 2000,
            instructions="Catalog fixture",
            actor="operator",
        )
    catalog = bridge.call(
        dict(
            threadId="thread", turnId="turn", callId="catalog", tool="hearth_catalog", arguments={}
        )
    )
    assert catalog["success"] and _tool_response(catalog) == catalog
    assert len(json.loads(catalog["contentItems"][0]["text"])["skills"]) == 25
