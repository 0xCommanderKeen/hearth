"""Authenticated browser journeys cross the same interface as the client."""

import time

import pytest
from fastapi.testclient import TestClient
from hearth.api import create_app
from hearth.models import Declaration
from hearth.runtime import Evidence

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


def test_seed_is_idempotent_and_snapshot_has_no_owner_token(client):
    first = client.post("/api/demo/reader", headers=AUTH)
    assert client.post("/api/demo/reader", headers=AUTH).json() == first.json()
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
    assert body["cursor"] == 3


def test_lost_submission_response_is_reconcilable(client):
    client.post("/api/demo/reader", headers=AUTH)
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
    client.post("/api/demo/reader", headers=AUTH)
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
        client.post("/api/demo/reader", headers=AUTH)
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
    client.post("/api/demo/reader", headers=AUTH)
    updated = client.get("/api/state", params=query, headers=AUTH)
    assert updated.status_code == 200
    assert updated.json()["cursor"] > first["cursor"]
    assert updated.json()["epoch"] == first["epoch"]
    query = {"cursor": updated.json()["cursor"], "epoch": "old-database"}
    assert client.get("/api/state", params=query, headers=AUTH).status_code == 200


def test_unknown_usage_is_visible_and_cannot_start_more_work(tmp_path):
    app = create_app(tmp_path, TOKEN, scenario="unknown_usage", supervise=False)
    with TestClient(app) as client:
        client.post("/api/demo/reader", headers=AUTH)
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
    client.post("/api/demo/reader", headers=AUTH)
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
    client.post("/api/demo/reader", headers=AUTH)
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
