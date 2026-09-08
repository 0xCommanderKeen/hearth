"""Authenticated browser journeys cross the same interface as the client."""

import time

import pytest
from fastapi.testclient import TestClient
from hearth.app import create_app
from hearth.residents.models import Declaration

from tests.fake_runtime import fake_runtime
from tests.support import seed_reader_via

TOKEN = "synthetic-operator-token-for-tests"
AUTH = {"Authorization": "Bearer " + TOKEN}


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(tmp_path, TOKEN, supervise=False, runtime=fake_runtime())) as client:
        yield client


def task(client, key="one"):
    return client.post(
        "/api/tasks",
        headers={**AUTH, "Idempotency-Key": key},
        json={
            "resident_id": "reader",
            "instruction": "Summarize synthetic notes.",
            "expires_at": int(time.time()) + 3600,
        },
    )


def test_authentication_precedes_body_parsing_and_state_reads(client):
    assert client.get("/api/state").status_code == 401
    assert client.post("/api/tasks", content="broken json").status_code == 401
    assert client.get("/api/state", headers={"Authorization": "Bearer wrong"}).status_code == 401
    assert client.get("/health").json() == {"service": "hearth"}
    assert client.get("/api/state", headers=AUTH).json()["tasks"] == []


def test_authenticated_body_is_bounded(client):
    response = client.post("/api/tasks", headers=AUTH, content="x" * 65_537)
    assert response.status_code == 413
    assert client.get("/api/state", headers=AUTH).json()["tasks"] == []


def test_snapshot_has_no_owner_token(client):
    first = seed_reader_via(client)
    assert seed_reader_via(client) == first
    receipt = task(client).json()
    assert (
        client.post("/api/tasks/" + receipt["task_id"] + "/start", headers=AUTH).status_code == 200
    )
    state = client.get("/api/state", headers=AUTH)
    assert state.headers["cache-control"] == "no-store"
    body = state.json()
    assert body["schema_version"] == 1
    assert body["residents"][0]["presence"] == "starting"
    assert "owner_token" not in state.text and TOKEN not in state.text
    # The cursor is the audit sequence, and the runtime records its own configuration.
    assert body["cursor"] == len(body["activity"]) == 6


def test_lost_submission_response_is_reconcilable(client):
    seed_reader_via(client)
    body = {
        "resident_id": "reader",
        "instruction": "Synthetic summary.",
        "expires_at": int(time.time()) + 3600,
    }
    headers = {**AUTH, "Idempotency-Key": "retry-me"}
    first = client.post("/api/tasks", headers=headers, json=body)
    replay = client.post("/api/tasks", headers=headers, json=body)
    assert first.status_code == replay.status_code == 201
    assert first.json() == replay.json()
    assert client.get("/api/commands/retry-me", headers=AUTH).json() == first.json()
    body["instruction"] = "Changed work"
    assert client.post("/api/tasks", headers=headers, json=body).status_code == 409
    assert len(client.get("/api/state", headers=AUTH).json()["tasks"]) == 1


def test_start_retry_has_stable_identity_and_result_can_be_read(client):
    seed_reader_via(client)
    receipt = task(client).json()
    path = "/api/tasks/" + receipt["task_id"] + "/start"
    first = client.post(path, headers=AUTH).json()
    assert client.post(path, headers=AUTH).json() == first
    client.app.state.executor.step()
    state = client.get("/api/state", headers=AUTH).json()
    run = state["runs"][0]
    assert state["tasks"][0]["status"] == run["status"] == "succeeded"
    assert client.post(path, headers=AUTH).json()["run_id"] == first["run_id"]
    output = client.get("/api/artifacts/" + run["artifact_id"], headers=AUTH)
    assert output.status_code == 200
    assert "Synthetic note: drafted the Hearth foundation." in output.json()["content"]
    assert client.get("/api/artifacts/" + run["artifact_id"]).status_code == 401


def test_cancellation_roundtrip_keeps_intent_separate_from_termination(tmp_path):
    app = create_app(tmp_path, TOKEN, runtime=fake_runtime("hold"), supervise=False)
    with TestClient(app) as client:
        seed_reader_via(client)
        receipt = task(client).json()
        run = client.post("/api/tasks/" + receipt["task_id"] + "/start", headers=AUTH).json()
        app.state.executor.step()
        assert client.get("/api/state", headers=AUTH).json()["runs"][0]["status"] == "running"
        response = client.post("/api/runs/" + run["run_id"] + "/cancel", headers=AUTH)
        assert response.json()["status"] == "stopping"
        app.state.executor.step()
        assert client.get("/api/state", headers=AUTH).json()["runs"][0]["status"] == "cancelled"


def test_cursor_matches_transaction_and_epoch_requires_resync(client):
    first = client.get("/api/state", headers=AUTH).json()
    query = {"cursor": first["cursor"], "epoch": first["epoch"]}
    assert client.get("/api/state", params=query, headers=AUTH).status_code == 204
    seed_reader_via(client)
    updated = client.get("/api/state", params=query, headers=AUTH)
    assert updated.status_code == 200
    assert updated.json()["cursor"] > first["cursor"]
    assert updated.json()["epoch"] == first["epoch"]
    query = {"cursor": updated.json()["cursor"], "epoch": "old-database"}
    assert client.get("/api/state", params=query, headers=AUTH).status_code == 200


def test_unknown_usage_is_visible_and_cannot_start_more_work(tmp_path):
    app = create_app(tmp_path, TOKEN, runtime=fake_runtime("unknown_usage"), supervise=False)
    with TestClient(app) as client:
        seed_reader_via(client)
        receipt = task(client).json()
        client.post("/api/tasks/" + receipt["task_id"] + "/start", headers=AUTH)
        app.state.executor.step()
        state = client.get("/api/state", headers=AUTH).json()
        assert state["residents"][0]["presence"] == "paused"
        assert state["residents"][0]["pause_reason"] == "usage_unknown"
        second = task(client, "second").json()
        response = client.post("/api/tasks/" + second["task_id"] + "/start", headers=AUTH)
        assert response.status_code == 409
        assert response.json()["error"] == "resident_paused"


def test_api_refuses_invalid_payload_without_creating_task(client):
    seed_reader_via(client)
    response = client.post(
        "/api/tasks",
        headers={**AUTH, "Idempotency-Key": "bad"},
        json={
            "resident_id": "reader",
            "instruction": "Synthetic",
            "expires_at": True,
            "extra": "field",
        },
    )
    assert response.status_code == 422
    assert client.get("/api/state", headers=AUTH).json()["tasks"] == []


def test_demo_requires_explicit_nontrivial_operator_token(tmp_path):
    with pytest.raises(ValueError, match="operator token"):
        create_app(tmp_path, "", runtime=fake_runtime())


def test_active_work_remains_visible_when_recent_history_is_full(client):
    seed_reader_via(client)
    hearth = client.app.state.hearth
    executor = client.app.state.executor
    tick = [1000]
    hearth.clock = lambda: tick[0]
    hearth.save_resident(
        "other", Declaration("Other", "Synthetic work", 1_000_000), expected_revision=0
    )
    for index in range(101):
        tick[0] += 1
        receipt = hearth.submit("new-" + str(index), "other", "Recent work", expires_at=5000)
        hearth.admit(receipt.task_id, reserve=10_000)
        executor.step()
    # Stamped before all of that history, so recency alone would drop it from the window.
    tick[0] = 1000
    old = hearth.submit("old", "reader", "Still active", expires_at=5000)
    active = hearth.admit(old.task_id, reserve=10_000)
    state = client.get("/api/state", headers=AUTH).json()
    assert len(state["runs"]) == len(state["tasks"]) == 100
    assert any(run["id"] == active.id for run in state["runs"])
    assert any(task["id"] == old.task_id for task in state["tasks"])


def test_inbox_records_a_finished_run_and_only_the_operator_marks_it_read(client):
    seed_reader_via(client)
    receipt = task(client).json()
    client.post("/api/tasks/" + receipt["task_id"] + "/start", headers=AUTH)
    run = client.app.state.executor.step()[0]
    notification = client.get("/api/state", headers=AUTH).json()["notifications"][0]
    assert notification["resource_id"] == run.id and notification["read_at"] is None
    route = "/api/notifications/" + notification["id"] + "/read"
    assert client.post(route, json={"read": True}).status_code == 401
    assert client.post(route, headers=AUTH, json={"read": "yes"}).status_code == 422
    assert (
        client.post(
            "/api/notifications/00000000-0000-4000-8000-000000000000/read",
            headers=AUTH,
            json={"read": True},
        ).status_code
        == 404
    )
    read = client.post(route, headers=AUTH, json={"read": True})
    assert read.status_code == 200 and read.json()["read_at"] is not None
    assert client.get("/api/state", headers=AUTH).json()["notifications"][0]["read_at"] is not None
    # The record stays in the inbox either way; only the mark changes.
    assert client.post(route, headers=AUTH, json={"read": False}).json()["read_at"] is None
    assert client.get("/api/state", headers=AUTH).json()["notifications"][0]["kind"] == (
        "run.succeeded"
    )


def test_daily_routine_api_runs_through_background_executor(tmp_path):
    app = create_app(tmp_path, TOKEN, runtime=fake_runtime())
    now = [1_788_652_800]
    app.state.hearth.clock = lambda: now[0]
    with TestClient(app) as client:
        seed_reader_via(client)
        body = {
            "resident_id": "reader",
            "instruction": "Scheduled synthetic summary",
            "local_time": "09:00",
            "timezone": "UTC",
            "enabled": True,
            "expected_revision": 0,
        }
        assert client.post("/api/routines/daily", json=body).status_code == 401
        response = client.post("/api/routines/daily", headers=AUTH, json=body)
        assert response.status_code == 200
        now[0] = response.json()["next_at"]
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            state = client.get("/api/state", headers=AUTH).json()
            if state["runs"] and state["runs"][0]["status"] == "succeeded":
                break
            time.sleep(0.02)
        assert len(state["occurrences"]) == 1
        assert state["runs"][0]["status"] == "succeeded"
        assert state["routines"][0]["next_at"] > now[0]
        body.update(enabled=False, expected_revision=1)
        assert client.post("/api/routines/daily", headers=AUTH, json=body).status_code == 200
        assert client.post("/api/routines/daily", headers=AUTH, json=body).status_code == 409


def test_notification_payload_is_authenticated_observation(client):
    seed_reader_via(client)
    receipt = task(client).json()
    client.post("/api/tasks/" + receipt["task_id"] + "/start", headers=AUTH)
    client.app.state.executor.step()
    state = client.get("/api/state", headers=AUTH).json()
    notification = state["notifications"][0]
    assert set(notification["payload"]) == {"kind", "resource_id", "link"}
    assert notification["payload"]["link"].startswith("/#run-")
    assert client.get("/api/state").status_code == 401


def test_operator_pause_api_keeps_work_queued_until_revisioned_resume(client):
    seed_reader_via(client)
    receipt = task(client).json()
    route = "/api/residents/reader/pause"
    assert client.post(route, json={"paused": True, "expected_revision": 0}).status_code == 401
    assert (
        client.post(route, headers=AUTH, json={"paused": True, "expected_revision": 0}).status_code
        == 200
    )
    start = "/api/tasks/" + receipt["task_id"] + "/start"
    assert client.post(start, headers=AUTH).json()["error"] == "resident_paused"
    assert (
        client.post(route, headers=AUTH, json={"paused": False, "expected_revision": 0}).status_code
        == 409
    )
    assert (
        client.post(route, headers=AUTH, json={"paused": False, "expected_revision": 1}).status_code
        == 200
    )
    assert client.post(start, headers=AUTH).status_code == 200


def test_operator_reports_usage_and_snapshot_labels_source(tmp_path):
    app = create_app(tmp_path, TOKEN, supervise=False, runtime=fake_runtime("unknown_usage"))
    with TestClient(app) as client:
        seed_reader_via(client)
        receipt = task(client).json()
        client.post("/api/tasks/" + receipt["task_id"] + "/start", headers=AUTH)
        run = app.state.executor.step()[0]
        route = "/api/runs/" + run.id + "/usage"
        body = {"amount": 2000, "evidence": "Synthetic meter reading"}
        assert client.post(route, json=body).status_code == 401
        headers = {**AUTH, "Idempotency-Key": "report"}
        response = client.post(route, headers=headers, json=body)
        assert response.status_code == 200
        assert response.json()["source"] == "operator_reported"
        assert "evidence" not in response.json()
        assert client.post(route, headers=headers, json=body).json() == response.json()
        state = client.get("/api/state", headers=AUTH).json()
        assert state["runs"][0]["usage_source"] == "operator_reported"
        assert state["residents"][0]["pause_reason"] is None


def test_resident_bundle_export_and_import_over_http(client, tmp_path):
    seed_reader_via(client)
    export = client.get("/api/residents/reader/export", headers=AUTH)
    assert export.status_code == 200
    assert export.headers["cache-control"] == "no-store"
    assert export.headers["content-disposition"] == (
        'attachment; filename="reader.hearth-resident.json"'
    )
    bundle = export.json()
    assert bundle["bundle_version"] == 1 and bundle["resident"]["name"] == "Reader"
    assert client.get("/api/residents/reader/export").status_code == 401
    assert client.get("/api/residents/nobody/export", headers=AUTH).status_code == 404
    assert (
        client.post("/api/residents/import", headers=AUTH, json={"bundle": bundle}).status_code
        == 422
    )
    imported = client.post(
        "/api/residents/import",
        headers={**AUTH, "Idempotency-Key": "import-reader"},
        json={"bundle": bundle, "overrides": {"name": "Reader copy"}},
    )
    assert imported.status_code == 201
    receipt = imported.json()
    assert receipt["status"] == "ready" and receipt["command_id"] == "import-reader"
    assert receipt["resolution"]["input_sets"][0]["outcome"] == "reused"
    assert (
        client.get("/api/resident-provisioning/import-reader", headers=AUTH).json()["status"]
        == "ready"
    )
    names = [r["name"] for r in client.get("/api/state", headers=AUTH).json()["residents"]]
    assert sorted(names) == ["Reader", "Reader copy"]
    tampered = {"bundle": bundle | {"resident": bundle["resident"] | {"purpose": "x"}}}
    conflict = client.post(
        "/api/residents/import", headers={**AUTH, "Idempotency-Key": "import-reader"}, json=tampered
    )
    assert conflict.status_code == 409
    large = client.post(
        "/api/residents/import",
        headers={**AUTH, "Idempotency-Key": "import-large"},
        content="x" * 1_500_001,
    )
    assert large.status_code == 413
    padded = client.post(
        "/api/residents/import",
        headers={**AUTH, "Idempotency-Key": "import-padded"},
        json={"bundle": bundle | {"resident": bundle["resident"] | {"memory": "m" * 100_000}}},
    )
    assert padded.status_code == 201
