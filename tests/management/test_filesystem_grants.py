"""What a resident may reach on disk: the grant, the run it is pinned onto, the audit.

Epic #183 slice D (issue #187), `docs/adr/0016-sandbox-per-run.md`. A grant names host
paths; admission turns them into the run's own `run_mounts` at the grant's revision; the
launcher turns those into bind mounts at `/mounts/<name>`; and what a run wrote into a
writable one is audited from its own receipt.
"""

import pytest
from fastapi.testclient import TestClient
from hearth.app import create_app

from tests.fake_runtime import fake_runtime

TOKEN = "synthetic-management-operator"
AUTH = {"Authorization": "Bearer " + TOKEN}


def open_app(tmp_path):
    return create_app(tmp_path / "data", TOKEN, supervise=False, runtime=fake_runtime())


def grant_path(resident_id: str) -> str:
    return f"/api/residents/{resident_id}/management"


def write_grant(client, resident_id, mounts, *, revision=0, **changes):
    grant = client.get(grant_path(resident_id), headers=AUTH).json()
    body = {key: value for key, value in grant.items() if key not in {"resident_id", "revision"}}
    body |= {"expected_revision": revision, "mounts": mounts, **changes}
    return client.put(grant_path(resident_id), headers=AUTH, json=body)


def resident(client):
    from tests.support import seed_reader

    seed_reader(client.app.state.hearth)
    return "reader"


def test_a_granted_folder_is_read_only_unless_the_grant_says_otherwise(tmp_path):
    shared = tmp_path / "shared"
    shared.mkdir()
    with TestClient(open_app(tmp_path)) as client:
        who = resident(client)
        saved = write_grant(client, who, [{"name": "notes", "host_path": str(shared)}])
        assert saved.status_code == 200
        assert saved.json()["mounts"] == [{"name": "notes", "host_path": str(shared), "mode": "ro"}]
        # The mode is the grant's own word and the default is the safe one.
        again = write_grant(
            client,
            who,
            [{"name": "notes", "host_path": str(shared), "mode": "rw"}],
            revision=1,
        )
        assert again.status_code == 200
        assert again.json()["mounts"][0]["mode"] == "rw"


@pytest.mark.parametrize(
    "mounts",
    [
        [{"name": "relative", "host_path": "shared"}],
        [{"name": "dots", "host_path": "/tmp/../etc"}],
        [{"name": "root", "host_path": "/"}],
        [{"name": "etc", "host_path": "/etc"}],
        [{"name": "proc", "host_path": "/proc/self"}],
        [{"name": "sys", "host_path": "/sys"}],
        [{"name": "socket", "host_path": "/var/run/docker.sock"}],
        [{"name": "same", "host_path": "/tmp/a"}, {"name": "same", "host_path": "/tmp/b"}],
        [{"name": "one", "host_path": "/tmp/a"}, {"name": "two", "host_path": "/tmp/a"}],
    ],
)
def test_a_grant_naming_a_path_no_resident_may_reach_is_refused_at_write(tmp_path, mounts):
    with TestClient(open_app(tmp_path)) as client:
        who = resident(client)
        refused = write_grant(client, who, mounts)
        assert refused.status_code == 409
        assert refused.json()["error"] == "grant_mount_forbidden"
        # Nothing was written: the grant is still the one it was.
        assert client.get(grant_path(who), headers=AUTH).json()["revision"] == 0


def test_a_grant_naming_hearths_own_data_directory_is_refused(tmp_path):
    with TestClient(open_app(tmp_path)) as client:
        who = resident(client)
        for path in (tmp_path / "data", tmp_path / "data" / "artifacts", tmp_path):
            refused = write_grant(client, who, [{"name": "store", "host_path": str(path)}])
            assert refused.status_code == 409, path
            assert refused.json()["error"] == "grant_mount_forbidden"


def admit(client, resident_id, command="work"):
    hearth = client.app.state.hearth
    receipt = hearth.submit(
        command, resident_id, "Read the folder", expires_at=int(hearth.clock()) + 600
    )
    return hearth.admit(receipt.task_id, reserve=3000)


def test_admission_pins_what_the_grant_said_and_the_run_context_names_it(tmp_path):
    shared, drafts = tmp_path / "shared", tmp_path / "drafts"
    shared.mkdir()
    drafts.mkdir()
    with TestClient(open_app(tmp_path)) as client:
        who = resident(client)
        write_grant(
            client,
            who,
            [
                {"name": "notes", "host_path": str(shared)},
                {"name": "drafts", "host_path": str(drafts), "mode": "rw"},
            ],
        )
        run = admit(client, who)
        hearth = client.app.state.hearth
        with hearth.database.transaction() as db:
            from hearth.execution.context import read_context
            from hearth.management.authority import run_mounts
            from hearth.residents.memory import MemoryFiles

            pinned = run_mounts(db, run.id)
            context = read_context(db, run.id, MemoryFiles(hearth.database.path.parent / "memory"))
        assert pinned == [
            {
                "name": "notes",
                "host_path": str(shared),
                "mode": "ro",
                "path": "/mounts/notes",
                "grant_revision": 1,
            },
            {
                "name": "drafts",
                "host_path": str(drafts),
                "mode": "rw",
                "path": "/mounts/drafts",
                "grant_revision": 1,
            },
        ]
        # The run tells the resident what it can see, by name, where and how far.
        assert [(entry["name"], entry["path"], entry["mode"]) for entry in context["mounts"]] == [
            ("notes", "/mounts/notes", "ro"),
            ("drafts", "/mounts/drafts", "rw"),
        ]
        assert "read-only" in context["mounts_usage"]


def test_a_run_keeps_the_mounts_it_was_admitted_with_when_the_grant_changes(tmp_path):
    shared = tmp_path / "shared"
    shared.mkdir()
    with TestClient(open_app(tmp_path)) as client:
        who = resident(client)
        write_grant(client, who, [{"name": "notes", "host_path": str(shared)}])
        run = admit(client, who)
        assert write_grant(client, who, [], revision=1).status_code == 200
        with client.app.state.hearth.database.transaction() as db:
            from hearth.management.authority import run_mounts

            kept = run_mounts(db, run.id)
        assert [entry["name"] for entry in kept] == ["notes"]
        assert kept[0]["grant_revision"] == 1


def test_a_routine_pass_leaves_a_waiting_task_queued_and_reports_no_fault(tmp_path):
    """A folder an operator has to put back is "not now", never "not ever"."""
    shared = tmp_path / "shared"
    shared.mkdir()
    with TestClient(open_app(tmp_path)) as client:
        who = resident(client)
        write_grant(client, who, [{"name": "notes", "host_path": str(shared)}])
        hearth = client.app.state.hearth
        from hearth.work.routines import Routines

        routines = Routines(hearth)
        routines.save(
            "daily",
            who,
            "Read the folder",
            local_time="09:00",
            timezone="Europe/Ljubljana",
            enabled=True,
            expected_revision=0,
        )
        with hearth.database.transaction(write=True) as db:
            # Long overdue, rather than at nine in the morning of whatever day this is.
            db.execute("UPDATE routines SET next_at=1")
        assert routines.tick()
        shared.rmdir()
        # The pass does not raise: nothing else queued is starved by this one resident,
        # and the task is still there to be admitted once the folder is back.
        routines.admit_queued()
        with hearth.database.transaction() as db:
            waiting = db.execute("SELECT id,status FROM tasks").fetchall()
        assert [row["status"] for row in waiting] == ["queued"]
        shared.mkdir()
        routines.admit_queued()
        with hearth.database.transaction() as db:
            assert db.execute("SELECT status FROM tasks").fetchone()["status"] == "starting"


def test_a_granted_folder_that_is_not_there_makes_the_run_wait(tmp_path):
    shared = tmp_path / "shared"
    shared.mkdir()
    with TestClient(open_app(tmp_path)) as client:
        who = resident(client)
        write_grant(client, who, [{"name": "notes", "host_path": str(shared)}])
        shared.rmdir()
        hearth = client.app.state.hearth
        receipt = hearth.submit("waiting", who, "Read it", expires_at=int(hearth.clock()) + 600)
        with pytest.raises(Exception) as refusal:
            hearth.admit(receipt.task_id, reserve=3000)
        assert getattr(refusal.value, "code", None) == "mount_unavailable"
        # The work is not lost: the task is still queued, waiting for the operator to
        # put the folder back or take it out of the grant.
        assert hearth.task(receipt.task_id).status == "queued"
        shared.mkdir()
        assert hearth.admit(receipt.task_id, reserve=3000).status == "starting"


def test_an_operator_reads_a_run_s_reach_off_the_run_itself(tmp_path):
    shared = tmp_path / "shared"
    shared.mkdir()
    with TestClient(open_app(tmp_path)) as client:
        who = resident(client)
        write_grant(client, who, [{"name": "notes", "host_path": str(shared), "mode": "rw"}])
        run = admit(client, who)
        entry = {"name": "notes", "host_path": str(shared), "mode": "rw", "path": "/mounts/notes"}
        state = client.get("/api/state", headers=AUTH).json()
        row = next(item for item in state["runs"] if item["id"] == run.id)
        assert row["mounts"] == [entry]
        # And the resident's own grant is where an operator edits it.
        who_row = next(item for item in state["residents"] if item["id"] == who)
        assert who_row["management"]["mounts"] == [
            {"name": "notes", "host_path": str(shared), "mode": "rw"}
        ]
        assert client.get(f"/api/runs/{run.id}", headers=AUTH).json()["mounts"] == [entry]
        # Granting a writable folder is a fact of its own, beside the grant itself.
        facts = [
            fact
            for fact in client.app.state.hearth.audit()
            if fact["kind"] == "grant.mount_rw_granted"
        ]
        assert [fact["detail"]["mounts"] for fact in facts] == [
            [{"name": "notes", "host_path": str(shared)}]
        ]


def test_a_grant_with_no_writable_folder_records_no_writable_fact(tmp_path):
    shared = tmp_path / "shared"
    shared.mkdir()
    with TestClient(open_app(tmp_path)) as client:
        who = resident(client)
        write_grant(client, who, [{"name": "notes", "host_path": str(shared)}])
        assert not [
            fact
            for fact in client.app.state.hearth.audit()
            if fact["kind"] == "grant.mount_rw_granted"
        ]


def test_a_login_directory_and_the_runtime_socket_are_protected(tmp_path):
    from hearth.management.authority import Mount as GrantMount
    from hearth.management.authority import check_mounts, protected_paths
    from hearth.residents.models import Refused

    login = tmp_path / "login"
    login.mkdir()
    protected = protected_paths(
        tmp_path / "data", [login], socket="unix:///var/lib/podman/podman.sock"
    )
    for path in (
        login,
        login / "inner",
        tmp_path,
        "/var/lib/podman/podman.sock",
        "/run/docker.sock",
    ):
        with pytest.raises(Refused, match="grant_mount_forbidden"):
            check_mounts([GrantMount(name="x", host_path=str(path))], protected)
    # A folder beside them is nobody's business but the operator's.
    check_mounts([GrantMount(name="x", host_path=str(tmp_path / "elsewhere"))], protected)


def test_a_link_into_a_protected_folder_is_still_that_folder(tmp_path):
    with TestClient(open_app(tmp_path)) as client:
        who = resident(client)
        link = tmp_path / "shortcut"
        link.symlink_to(tmp_path / "data", target_is_directory=True)
        refused = write_grant(client, who, [{"name": "store", "host_path": str(link)}])
        assert refused.status_code == 409
        assert refused.json()["error"] == "grant_mount_forbidden"
