"""Shared skill operations through the authenticated API and real SQLite."""

from fastapi.testclient import TestClient
from hearth.app import create_app

from tests.fake_runtime import fake_runtime

TOKEN = "synthetic-operator-token-for-skills"
AUTH = {"Authorization": "Bearer " + TOKEN}
BODY = {
    "name": "Daily summary",
    "description": "Use for short daily notes.",
    "instructions": "# Summary\nRead the notes and summarize them.",
}


def catalog_activity(client):
    """Audit kinds the catalog wrote; the runtime records its own configuration too."""
    rows = client.get("/api/state", headers=AUTH).json()["activity"]
    return [row["kind"] for row in rows if row["kind"].startswith("skill.")]


def test_create_retry_history_and_authenticated_provenance(tmp_path):
    with TestClient(create_app(tmp_path, TOKEN, supervise=False, runtime=fake_runtime())) as client:
        assert client.post("/api/skills", json=BODY).status_code == 401
        headers = {**AUTH, "Idempotency-Key": "create-summary"}
        response = client.post("/api/skills", headers=headers, json=BODY)
        assert response.status_code == 201
        receipt = response.json()
        assert client.post("/api/skills", headers=headers, json=BODY).json() == receipt
        assert client.get("/api/skills/operations/create-summary", headers=AUTH).json() == receipt
        skills = client.get("/api/skills", headers=AUTH).json()
        assert len(skills) == 1
        skill = client.get("/api/skills/" + receipt["skill_id"], headers=AUTH).json()
        assert skill["name"] == "Daily summary"
        assert skill["revision"] == 1 and skill["status"] == "active"
        assert skill["created_by"] == skill["edited_by"] == "operator"
        assert skill["created_at"] == skill["edited_at"] == receipt["recorded_at"]
        assert (
            client.post(
                "/api/skills", headers=headers, json={**BODY, "name": "Changed"}
            ).status_code
            == 409
        )
        assert catalog_activity(client) == ["skill.create"]


def test_concurrent_edits_archive_history_and_held_restore(tmp_path):
    from concurrent.futures import ThreadPoolExecutor

    from hearth.storage.backup import capture, restore

    data = tmp_path / "data"
    with TestClient(create_app(data, TOKEN, supervise=False, runtime=fake_runtime())) as client:
        receipt = client.post(
            "/api/skills", headers={**AUTH, "Idempotency-Key": "create"}, json=BODY
        ).json()
        path = "/api/skills/" + receipt["skill_id"]

        def edit(key):
            return client.put(
                path,
                headers={**AUTH, "Idempotency-Key": key},
                json={**BODY, "name": key, "expected_revision": 1},
            )

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(edit, ["first-editor", "second-editor"]))
        assert sorted(result.status_code for result in results) == [200, 409]
        winner = next(result for result in results if result.status_code == 200).json()
        assert edit(winner["command_id"]).json() == winner
        assert client.get(path + "?revision=1", headers=AUTH).json()["name"] == "Daily summary"
        archived = client.post(
            path + "/archive",
            headers={**AUTH, "Idempotency-Key": "archive"},
            json={"expected_revision": 2},
        )
        assert archived.status_code == 200
        assert (
            client.post(
                path + "/archive",
                headers={**AUTH, "Idempotency-Key": "archive"},
                json={"expected_revision": 2},
            ).json()
            == archived.json()
        )
        assert client.get("/api/skills", headers=AUTH).json() == []
        assert len(client.get("/api/skills?include_archived=true", headers=AUTH).json()) == 1
        assert client.get(path, headers=AUTH).json()["status"] == "archived"
        assert client.put(
            path, headers={**AUTH, "Idempotency-Key": "late"}, json={**BODY, "expected_revision": 3}
        ).json() == {"error": "skill_archived"}
        history = client.get(path + "/history", headers=AUTH).json()
        assert [row["revision"] for row in history] == [3, 2, 1]
        assert catalog_activity(client) == ["skill.archive", "skill.save", "skill.create"]
    capture(data, tmp_path / "backup")
    restore(tmp_path / "backup", tmp_path / "restored")
    with TestClient(
        create_app(tmp_path / "restored", TOKEN, supervise=False, runtime=fake_runtime())
    ) as restored:
        assert restored.get(path + "/history", headers=AUTH).json() == history
        assert restored.get("/api/skills/operations/create", headers=AUTH).json() == receipt
        assert restored.get("/api/state", headers=AUTH).json()["restore_hold"] is True
        assert restored.post(
            "/api/skills", headers={**AUTH, "Idempotency-Key": "new"}, json=BODY
        ).json() == {"error": "restored_copy_read_only"}


def test_validation_search_and_actor_cannot_be_forged(tmp_path):
    with TestClient(create_app(tmp_path, TOKEN, supervise=False, runtime=fake_runtime())) as client:
        headers = {**AUTH, "Idempotency-Key": "create"}
        for invalid in [
            {"name": " "},
            {"instructions": "x" * 32001},
            {"actor": "someone-else"},
            {"created_at": 1},
        ]:
            assert client.post(
                "/api/skills", headers=headers, json={**BODY, **invalid}
            ).status_code in {409, 422}
        assert catalog_activity(client) == []
        client.post("/api/skills", headers=headers, json=BODY)
        assert len(client.get("/api/skills?query=DAILY", headers=AUTH).json()) == 1
        assert client.get("/api/skills?query=unrelated", headers=AUTH).json() == []


def test_duplicate_concurrent_creation_and_reopen_preserve_one_operation(tmp_path):
    from concurrent.futures import ThreadPoolExecutor

    with TestClient(create_app(tmp_path, TOKEN, supervise=False, runtime=fake_runtime())) as client:

        def create(_):
            return client.post(
                "/api/skills", headers={**AUTH, "Idempotency-Key": "same"}, json=BODY
            )

        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(create, range(4)))
        assert all(response.status_code == 201 for response in results)
        receipt = results[0].json()
        assert all(response.json() == receipt for response in results)
    with TestClient(create_app(tmp_path, TOKEN, supervise=False, runtime=fake_runtime())) as client:
        assert client.get("/api/skills/operations/same", headers=AUTH).json() == receipt
        assert len(client.get("/api/skills", headers=AUTH).json()) == 1
        assert catalog_activity(client) == ["skill.create"]


def test_failed_audit_rolls_back_content_revision_and_receipt(tmp_path):
    import sqlite3

    with TestClient(
        create_app(tmp_path, TOKEN, supervise=False, runtime=fake_runtime()),
        raise_server_exceptions=False,
    ) as client:
        with sqlite3.connect(tmp_path / "hearth.db") as db:
            db.execute(
                "CREATE TRIGGER fail_skill_audit BEFORE INSERT ON audit "
                "BEGIN SELECT RAISE(ABORT, 'synthetic storage fault'); END"
            )
        response = client.post(
            "/api/skills", headers={**AUTH, "Idempotency-Key": "recoverable"}, json=BODY
        )
        assert response.status_code == 500
        assert client.get("/api/skills", headers=AUTH).json() == []
        assert client.get("/api/skills/operations/recoverable", headers=AUTH).status_code == 404
        with sqlite3.connect(tmp_path / "hearth.db") as db:
            db.execute("DROP TRIGGER fail_skill_audit")
        assert (
            client.post(
                "/api/skills", headers={**AUTH, "Idempotency-Key": "recoverable"}, json=BODY
            ).status_code
            == 201
        )
        assert catalog_activity(client) == ["skill.create"]


def test_changed_content_is_refused_by_inspection_and_backup(tmp_path):
    import sqlite3

    import pytest
    from hearth.residents.models import Refused
    from hearth.storage.backup import capture

    data = tmp_path / "data"
    with TestClient(create_app(data, TOKEN, supervise=False, runtime=fake_runtime())) as client:
        receipt = client.post(
            "/api/skills", headers={**AUTH, "Idempotency-Key": "one"}, json=BODY
        ).json()
        with sqlite3.connect(data / "hearth.db") as db:
            db.execute("UPDATE skill_revisions SET instructions='Corrupt text'")
        assert client.get("/api/skills/" + receipt["skill_id"], headers=AUTH).json() == {
            "error": "skill_content_changed"
        }
        with pytest.raises(Refused, match="skill_content_changed"):
            capture(data, tmp_path / "backup")
