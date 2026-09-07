"""Authenticated browser journeys cross the same interface as the client."""

import time

import pytest
from fastapi.testclient import TestClient
from hearth.app import create_app
from hearth.integrations.interface import Evidence
from hearth.residents.models import Declaration

from tests.support import seed_reader_via

TOKEN = "synthetic-operator-token-for-tests"
AUTH = {"Authorization": "Bearer " + TOKEN}


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(tmp_path, TOKEN, supervise=False)) as client:
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
    assert client.get("/health").json() == {"service": "hearth", "simulated": True}
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
    assert body["simulated"] and body["schema_version"] == 1
    assert body["residents"][0]["presence"] == "starting"
    assert "owner_token" not in state.text and TOKEN not in state.text
    assert body["cursor"] == 5


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
    assert output.json()["artifact"]["simulated"]
    assert "No model was called" in output.json()["content"]
    assert client.get("/api/artifacts/" + run["artifact_id"]).status_code == 401


def test_cancellation_roundtrip_keeps_intent_separate_from_termination(tmp_path):
    app = create_app(tmp_path, TOKEN, scenario="hold", supervise=False)
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
    app = create_app(tmp_path, TOKEN, scenario="unknown_usage", supervise=False)
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
        create_app(tmp_path, "")


def test_active_work_remains_visible_when_recent_history_is_full(client):
    seed_reader_via(client)
    hearth = client.app.state.hearth
    execution = client.app.state.execution
    tick = [1000]
    hearth.clock = lambda: tick[0]
    old = hearth.submit("old", "reader", "Still active", expires_at=5000)
    active = hearth.admit(old.task_id, reserve=1)
    hearth.save_resident(
        "other", Declaration("Other", "Synthetic work", 1_000_000), expected_revision=0
    )
    for index in range(101):
        tick[0] += 1
        receipt = hearth.submit("new-" + str(index), "other", "Recent work", expires_at=5000)
        run = hearth.admit(receipt.task_id, reserve=1)
        execution.finish(run.id, run.owner_token, Evidence("failed", cost=1))
    state = client.get("/api/state", headers=AUTH).json()
    assert len(state["runs"]) == len(state["tasks"]) == 100
    assert any(run["id"] == active.id for run in state["runs"])
    assert any(task["id"] == old.task_id for task in state["tasks"])


def proposal(client):
    seed_reader_via(client)
    receipt = task(client).json()
    client.post("/api/tasks/" + receipt["task_id"] + "/start", headers=AUTH)
    run = client.app.state.executor.step()[0]
    assert (
        client.post(
            "/api/residents/reader/publication-policy",
            headers=AUTH,
            json={"enabled": True, "expected_revision": 0},
        ).status_code
        == 200
    )
    body = {"artifact_id": run.artifact_id, "expires_at": int(time.time()) + 600}
    headers = {**AUTH, "Idempotency-Key": "approval-request"}
    response = client.post("/api/approvals", headers=headers, json=body)
    assert response.status_code == 201
    assert client.post("/api/approvals", headers=headers, json=body).json() == response.json()
    return response.json()


def test_mock_approval_operator_journey(client):
    request = proposal(client)
    route = "/api/approvals/" + request["id"]
    assert client.get(route).status_code == 401
    assert client.post(route + "/decision", json={}).status_code == 401
    assert client.post(route + "/execute").status_code == 401
    preview = client.get(route, headers=AUTH).json()
    assert preview["approval"] == request
    assert "No model was called" in preview["content"]
    assert client.post(route + "/execute", headers=AUTH).status_code == 409
    decision = {"reviewed_digest": request["digest"], "approve": True}
    assert (
        client.post(route + "/decision", headers=AUTH, json=decision).json()["status"] == "approved"
    )
    action = client.post(route + "/execute", headers=AUTH).json()
    assert action["status"] == "completed"
    assert client.post(route + "/execute", headers=AUTH).json() == action
    state = client.get("/api/state", headers=AUTH).json()
    assert state["approvals"][0]["status"] == "approved"
    assert state["actions"][0]["status"] == "completed"
    assert state["publication_policies"][0]["enabled"] == 1


def test_mock_api_denied_and_revoked_permission_cannot_publish(client):
    request = proposal(client)
    route = "/api/approvals/" + request["id"]
    decision = {"reviewed_digest": request["digest"], "approve": False}
    assert (
        client.post(route + "/decision", headers=AUTH, json=decision).json()["status"] == "denied"
    )
    decision["approve"] = True
    assert (
        client.post(route + "/decision", headers=AUTH, json=decision).json()["status"] == "denied"
    )
    assert client.post(route + "/execute", headers=AUTH).status_code == 409


def test_mock_api_strict_policy_and_decision_payload(client):
    request = proposal(client)
    assert (
        client.post(
            "/api/residents/reader/publication-policy",
            headers=AUTH,
            json={"enabled": "false", "expected_revision": 1},
        ).status_code
        == 422
    )
    assert (
        client.post(
            "/api/approvals/" + request["id"] + "/decision",
            headers=AUTH,
            json={"reviewed_digest": "a" * 64, "approve": True},
        ).status_code
        == 409
    )


def test_daily_routine_api_runs_through_background_mock_executor(tmp_path):
    app = create_app(tmp_path, TOKEN)
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


def test_notification_payload_and_delivery_are_authenticated_observation(client):
    seed_reader_via(client)
    receipt = task(client).json()
    client.post("/api/tasks/" + receipt["task_id"] + "/start", headers=AUTH)
    client.app.state.executor.step()
    state = client.get("/api/state", headers=AUTH).json()
    delivery = state["notifications"][0]
    assert delivery["status"] == "pending"
    assert delivery["payload"]["simulated"] is True
    assert set(delivery["payload"]) == {"kind", "resource_id", "link", "simulated"}
    assert delivery["payload"]["link"].startswith("/#run-")
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


def test_operator_reports_mock_usage_and_snapshot_labels_source(tmp_path):
    app = create_app(tmp_path, TOKEN, supervise=False, scenario="unknown_usage")
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
        assert response.json()["source"] == "operator_reported_mock"
        assert "evidence" not in response.json()
        assert client.post(route, headers=headers, json=body).json() == response.json()
        state = client.get("/api/state", headers=AUTH).json()
        assert state["runs"][0]["usage_source"] == "operator_reported_mock"
        assert state["residents"][0]["pause_reason"] is None
