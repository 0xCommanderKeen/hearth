"""A mock runtime client crosses the HTTP credential seam without operator authority."""

import json

import pytest
from fastapi.testclient import TestClient
from hearth.app import create_app
from hearth.authority.run_access import RunAccess
from hearth.residents.models import Declaration, Refused
from hearth.storage.backup import capture, restore

TOKEN = "synthetic-operator-token"


@pytest.fixture
def system(tmp_path):
    app = create_app(tmp_path / "data", TOKEN, supervise=False, scenario="hold")
    hearth = app.state.hearth
    now = [1_788_640_000]
    hearth.clock = lambda: now[0]
    hearth.save_resident(
        "reader", Declaration("Reader", "Read synthetic notes", 1_000_000), expected_revision=0
    )
    task = hearth.submit("task", "reader", "Summarize synthetic notes", expires_at=now[0] + 600)
    run = hearth.admit(task.task_id, reserve=10_000)
    credential = app.state.run_access.issue(run.id, run.owner_token)
    with TestClient(app) as client:
        yield app, client, run, credential, now


def route(run):
    return f"/api/runtime/runs/{run.id}/context"


def auth(credential):
    return {"Authorization": "Bearer " + credential.token}


def test_runtime_reads_own_synthetic_context_without_authority_secrets(system):
    app, client, run, credential, _ = system
    response = client.get(route(run), headers=auth(credential))
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    context = response.json()
    assert context["run_id"] == run.id and context["resident_id"] == "reader"
    assert context["instruction"] == "Summarize synthetic notes"
    assert context["simulated"] is True and all("Synthetic" in note for note in context["notes"])
    assert run.owner_token not in response.text and credential.token not in response.text
    assert credential.token not in repr(credential)
    assert credential.token not in json.dumps(app.state.hearth.audit())
    state = client.get("/api/state", headers={"Authorization": "Bearer " + TOKEN})
    assert credential.token not in state.text and "run_credentials" not in state.text
    with app.state.hearth.database.transaction() as db:
        row = db.execute("SELECT * FROM run_credentials").fetchone()
        assert credential.token not in str(tuple(row))


@pytest.mark.parametrize(
    "method,path",
    [
        ("GET", "/api/state"),
        ("POST", "/api/tasks"),
        ("POST", "/api/runs/work/usage"),
        ("POST", "/api/approvals"),
        ("POST", "/api/approvals/review/decision"),
        ("POST", "/api/approvals/review/execute"),
        ("POST", "/api/residents/reader/pause"),
        ("GET", "/api/runtime/runs/foreign/context"),
        ("POST", "/api/runtime/issue"),
        ("GET", "/api/runtime/runs/not.valid/context"),
    ],
)
def test_runtime_token_cannot_cross_its_scope(system, method, path):
    _, client, _, credential, _ = system
    response = client.request(method, path, headers=auth(credential), content="invalid body")
    assert response.status_code == 401


def test_operator_token_does_not_substitute_for_runtime_credential(system):
    _, client, run, credential, _ = system
    assert client.get(route(run), headers={"Authorization": "Bearer " + TOKEN}).status_code == 401
    assert client.post(route(run), headers=auth(credential), content="invalid").status_code == 401
    assert (
        client.get(
            route(run), headers={"Authorization": "Bearer " + credential.token + "x"}
        ).status_code
        == 401
    )


def test_rotation_revocation_and_exclusive_expiry(system):
    app, client, run, old, now = system
    access = app.state.run_access
    fresh = access.issue(run.id, run.owner_token, lifetime=10)
    assert client.get(route(run), headers=auth(old)).status_code == 401
    assert client.get(route(run), headers=auth(fresh)).status_code == 200
    now[0] = fresh.expires_at
    assert client.get(route(run), headers=auth(fresh)).status_code == 401
    fresh = access.issue(run.id, run.owner_token)
    access.revoke(run.id, run.owner_token)
    assert client.get(route(run), headers=auth(fresh)).status_code == 401


@pytest.mark.parametrize("change", ["cancel", "finish", "unknown", "configuration", "owner"])
def test_current_authority_is_rechecked_on_every_read(system, change):
    app, client, run, credential, _ = system
    hearth = app.state.hearth
    if change == "cancel":
        app.state.execution.cancel(run.id)
    elif change == "finish":
        app.state.executor.runtime.scenario = "success"
        app.state.executor.step()
    elif change == "unknown":
        app.state.execution.observe(run.id, run.owner_token, "interrupted")
    elif change == "configuration":
        hearth.save_resident(
            "reader", Declaration("Reader", "Changed", 1_000_000), expected_revision=1
        )
    else:
        with hearth.database.transaction(write=True) as db:
            db.execute("UPDATE runs SET owner_token='replacement-owner' WHERE id=?", (run.id,))
    assert client.get(route(run), headers=auth(credential)).status_code == 401


def test_issuance_requires_current_owner_and_audit_is_atomic(system):
    app, _, run, credential, _ = system
    access = app.state.run_access
    with pytest.raises(Refused, match="run_ownership_lost"):
        access.issue(run.id, "wrong-owner")
    with app.state.hearth.database.transaction(write=True) as db:
        db.execute("""CREATE TRIGGER fail_issue BEFORE INSERT ON audit
            WHEN NEW.kind='run.context_issued'
            BEGIN SELECT RAISE(ABORT,'audit failure'); END""")
    with pytest.raises(Exception, match="audit failure"):
        access.issue(run.id, run.owner_token)
    assert access.context(credential.token, run.id)["run_id"] == run.id


def test_backup_contains_no_bearer_and_restore_cannot_use_original_access(system, tmp_path):
    app, _, run, credential, _ = system
    backup = tmp_path / "backup"
    capture(tmp_path / "data", backup)
    assert credential.token.encode() not in (backup / "hearth.db").read_bytes()
    copy = tmp_path / "copy"
    restore(backup, copy)
    restored_app = create_app(copy, TOKEN, supervise=True)
    with TestClient(restored_app) as client:
        assert client.get(route(run), headers=auth(credential)).status_code == 401
    with pytest.raises(Refused, match="restored_copy_read_only"):
        restored_app.state.run_access.issue(run.id, run.owner_token)
    # A server restart keeps a still-current credential valid for its original run.
    assert RunAccess(app.state.hearth).context(credential.token, run.id)["simulated"] is True


def test_token_for_one_resident_cannot_read_another_existing_run(system):
    app, client, run, credential, now = system
    hearth = app.state.hearth
    hearth.save_resident(
        "other", Declaration("Other", "Other private purpose", 1_000_000), expected_revision=0
    )
    task = hearth.submit(
        "other-task", "other", "Other private instruction", expires_at=now[0] + 600
    )
    other = hearth.admit(task.task_id, reserve=10_000)
    other_credential = app.state.run_access.issue(other.id, other.owner_token)
    assert client.get(route(other), headers=auth(other_credential)).status_code == 200
    assert client.get(route(other), headers=auth(credential)).status_code == 401
    assert client.get(route(run), headers=auth(other_credential)).status_code == 401
