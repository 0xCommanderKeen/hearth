"""Skill content survives revisions, execution and held portable reconstruction."""

import json
import sqlite3
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, replace

import pytest
from fastapi.testclient import TestClient
from hearth.api import create_app
from hearth.backup import capture, restore
from hearth.core import Hearth
from hearth.database import Database
from hearth.models import Declaration, Refused
from hearth.portable import compare, export, import_state, upgrade_state, validate
from hearth.run_access import RunAccess

TOKEN = "synthetic-operator-token"
SKILL = "# Reading\n\nPreserve ž, blank lines and `code`.\n"


@pytest.fixture
def system(tmp_path):
    app = create_app(tmp_path / "data", TOKEN, supervise=False)
    hearth = app.state.hearth
    hearth.clock = lambda: 1000
    declaration = Declaration("Reader", "Synthetic purpose", 10000, "Europe/Ljubljana", SKILL)
    hearth.save_resident("reader", declaration, expected_revision=0)
    return app, hearth, tmp_path


def test_exact_skill_revision_reaches_runtime_and_history(system, monkeypatch):
    app, hearth, _ = system
    task = hearth.submit("task", "reader", "Synthetic task", expires_at=1500)
    run = hearth.admit(task.task_id, reserve=3000)
    access = RunAccess(hearth)
    token = access.issue(run.id, run.owner_token)
    assert access.context(token.token, run.id)["skill_text"] == SKILL
    captured = []
    original = app.state.executor.runtime.start

    def start(id, instruction):
        captured.append(json.loads(instruction))
        original(id, instruction)

    monkeypatch.setattr(app.state.executor.runtime, "start", start)
    app.state.executor.step()
    old = hearth.resident("reader")
    hearth.save_resident(
        "reader", replace(old.declaration, skill_text="New skill"), expected_revision=1
    )
    assert captured[0]["skill_text"] == SKILL
    assert hearth.resident("reader", revision=1).declaration.skill_text == SKILL
    assert hearth.resident("reader").declaration.skill_text == "New skill"
    assert SKILL not in json.dumps(hearth.audit())


def test_concurrent_skill_edits_have_one_winner_and_audit_is_atomic(system):
    _, hearth, _ = system
    original = hearth.resident("reader").declaration

    def save(text):
        try:
            return hearth.save_resident(
                "reader", replace(original, skill_text=text), expected_revision=1
            ).declaration.skill_text
        except Refused as error:
            return error.code

    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(save, ["First", "Second"]))
    assert results.count("revision_conflict") == 1
    assert hearth.resident("reader").declaration.skill_text in results
    with hearth.database.transaction(write=True) as db:
        db.execute(
            "CREATE TRIGGER reject_save BEFORE INSERT ON audit WHEN NEW.kind='resident.saved' "
            "BEGIN SELECT RAISE(ABORT,'audit failure'); END"
        )
    with pytest.raises(sqlite3.IntegrityError, match="audit failure"):
        hearth.save_resident("reader", replace(original, skill_text="Lost"), expected_revision=2)
    assert hearth.resident("reader").revision == 2
    with pytest.raises(Refused, match="resident_not_found"):
        hearth.resident("reader", revision=3)


@pytest.mark.parametrize("text", [None, 1, ["skill"], "x" * 32001, "\ud800"])
def test_invalid_skills_never_change_revision(system, text):
    _, hearth, _ = system
    original = hearth.resident("reader").declaration
    with pytest.raises(Refused, match="invalid_skill_text"):
        hearth.save_resident("reader", replace(original, skill_text=text), expected_revision=1)
    assert hearth.resident("reader").revision == 1


def test_operator_routes_preserve_other_fields_exclude_ambient_text_and_reject_runtime(system):
    app, hearth, root = system
    headers = {"Authorization": "Bearer " + TOKEN}
    with TestClient(app) as client:
        saved = client.get("/api/residents/reader", headers=headers).json()
        body = saved["declaration"] | {
            "skill_text": "New synthetic skill",
            "expected_revision": saved["revision"],
        }
        assert client.put("/api/residents/reader", json=body).status_code == 401
        response = client.put("/api/residents/reader", headers=headers, json=body)
        assert response.status_code == 200 and response.headers["cache-control"] == "no-store"
        expected = body.copy()
        expected.pop("expected_revision")
        assert response.json()["declaration"] == expected
        assert client.put("/api/residents/reader", headers=headers, json=body).status_code == 409
        assert (
            client.put(
                "/api/residents/reader", headers=headers, json=body | {"grant": "write"}
            ).status_code
            == 422
        )
        assert (
            client.get("/api/residents/reader?revision=1", headers=headers).json()["declaration"][
                "skill_text"
            ]
            == SKILL
        )
        assert "skill_text" not in client.get("/api/state", headers=headers).text
        task = hearth.submit("task", "reader", "Synthetic", expires_at=1500)
        run = hearth.admit(task.task_id, reserve=3000)
        credential = RunAccess(hearth).issue(run.id, run.owner_token)
        runtime = {"Authorization": "Bearer " + credential.token}
        assert client.put("/api/residents/reader", headers=runtime, json=body).status_code == 401
        assert client.get("/api/residents/reader", headers=runtime).status_code == 401
    capture(root / "data", root / "backup")
    restore(root / "backup", root / "restored")
    with TestClient(create_app(root / "restored", TOKEN, supervise=False)) as client:
        assert client.get("/api/residents/reader", headers=headers).status_code == 200
        assert (
            client.put(
                "/api/residents/reader", headers=headers, json=body | {"expected_revision": 2}
            ).status_code
            == 409
        )


def exported(system):
    _, hearth, root = system
    original = hearth.resident("reader")
    hearth.save_resident(
        "reader",
        replace(original.declaration, skill_text="Second synthetic skill"),
        expected_revision=1,
    )
    capture(root / "data", root / "backup")
    export(root / "backup", root / "export")
    return (root / "export/state.json").read_bytes()


def test_skill_history_round_trip_and_semantic_diff(system):
    _, _, root = system
    content = exported(system)
    import_state(content, root / "imported")
    copy = Hearth(Database(root / "imported/hearth.db"))
    assert copy.database.restored()
    assert copy.resident("reader", revision=1).declaration.skill_text == SKILL
    assert copy.resident("reader").declaration.skill_text == "Second synthetic skill"
    capture(root / "imported", root / "again-backup")
    export(root / "again-backup", root / "again-export")
    assert compare(content, (root / "again-export/state.json").read_bytes())["equal"]
    changed = json.loads(content)
    changed["tables"]["declarations"][0]["skill_text"] += " changed"
    diff = compare(content, json.dumps(changed).encode())
    assert diff["totals"]["modified"] == 1
    assert "skill_text" in diff["changes"][0]["fields"]


def test_legacy_portable_requires_explicit_lossless_upgrade(system):
    _, _, root = system
    current = json.loads(exported(system))
    legacy = json.loads(json.dumps(current))
    for row in legacy["tables"]["declarations"]:
        del row["skill_text"]
    legacy["version"], legacy["schema"] = 1, 10
    del legacy["tables"]["memory_revisions"]
    del legacy["tables"]["run_memory"]
    content = json.dumps(legacy).encode()
    assert validate(content)["schema"] == 10
    with pytest.raises(Refused, match="portable_upgrade_required"):
        import_state(content, root / "denied")
    assert not (root / "denied").exists()
    result = upgrade_state(content, root / "upgraded")
    assert result["version"] == 3 and result["schema"] == 12
    upgraded = json.loads((root / "upgraded/state.json").read_bytes())
    for row in upgraded["tables"]["declarations"]:
        assert row.pop("skill_text") == ""
    upgraded["version"], upgraded["schema"] = 1, 10
    assert upgraded["tables"].pop("memory_revisions") == []
    assert upgraded["tables"].pop("run_memory") == []
    assert upgraded == legacy
    with pytest.raises(Refused, match="portable_comparison_requires_same_format"):
        compare(content, (root / "upgraded/state.json").read_bytes())
    import_state((root / "upgraded/state.json").read_bytes(), root / "imported")
    assert (
        Hearth(Database(root / "imported/hearth.db")).resident("reader").declaration.skill_text
        == ""
    )
    malformed = json.loads(content)
    malformed["tables"]["declarations"][0]["skill_text"] = "unrecognized legacy content"
    with pytest.raises(Refused, match="portable_columns_invalid"):
        upgrade_state(json.dumps(malformed).encode(), root / "malformed")


def test_skill_schema_migration_failure_rolls_back(system, monkeypatch):
    import hearth.database as module

    _, hearth, _ = system
    with hearth.database.transaction(write=True) as db:
        db.execute("ALTER TABLE declarations DROP COLUMN skill_text")
        db.execute("PRAGMA user_version=10")
    original = module.skill_schema

    def fail(db):
        original(db)
        raise RuntimeError("migration failure")

    monkeypatch.setattr(module, "skill_schema", fail)
    with pytest.raises(RuntimeError, match="migration failure"):
        hearth.database.initialize()
    with sqlite3.connect(hearth.database.path) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 10
        assert "skill_text" not in [row[1] for row in db.execute("PRAGMA table_info(declarations)")]


def test_cli_reads_saves_and_refuses_stale_revision(system):
    _, hearth, root = system
    source = root / "declaration.json"
    value = asdict(hearth.resident("reader").declaration) | {"skill_text": "CLI synthetic skill"}
    source.write_text(json.dumps(value))
    command = [
        sys.executable,
        "-m",
        "hearth",
        "save-resident",
        "--data",
        str(root / "data"),
        "--resident",
        "reader",
        "--source",
        str(source),
        "--expected-revision",
        "1",
    ]
    saved = subprocess.run(command, capture_output=True, text=True)
    assert saved.returncode == 0 and json.loads(saved.stdout)["revision"] == 2
    assert subprocess.run(command, capture_output=True, text=True).returncode != 0
    shown = subprocess.run(
        [
            sys.executable,
            "-m",
            "hearth",
            "show-resident",
            "--data",
            str(root / "data"),
            "--resident",
            "reader",
            "--revision",
            "1",
        ],
        capture_output=True,
        text=True,
    )
    assert shown.returncode == 0 and json.loads(shown.stdout)["declaration"]["skill_text"] == SKILL
