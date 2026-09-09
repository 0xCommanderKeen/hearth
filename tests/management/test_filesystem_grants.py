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
