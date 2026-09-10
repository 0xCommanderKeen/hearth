"""The fence: what a start measures from inside the sandbox network before it opens.

The probe half runs in the image (`tests/integrations/test_reach.py`); this is the half
outside that says what the answers mean. It is driven against `tests/fake_docker.py`
with a stand-in interpreter carried by the fake image, so the argv Hearth builds, the
answers it accepts and the refusals it raises are all exercised without a daemon. What a
real network does to a real probe is measured instead (`docs/sandbox.md`).
"""

import json
import socket
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from hearth.app import create_app
from hearth.integrations.fence import Fence, check
from hearth.integrations.launcher import CLI, CONTAINER, LABEL, PYTHON, Sandbox
from hearth.residents.models import Refused
from hearth.storage.database import Database

from tests import fake_docker
from tests.fake_runtime import BINARY, fake_runtime

DIGEST = "sha256:" + "1" * 64
IMAGE = "ghcr.io/hearth/sandbox@" + DIGEST
NETWORK = "hearth-egress"
TOKEN = "synthetic-operator-token-for-tests"


def store(tmp_path) -> Database:
    database = Database(tmp_path / "hearth.db")
    database.initialize()
    return database


def daemon(tmp_path, answers=None, *, body: str | None = None) -> Path:
    """A fake daemon whose image carries an interpreter that answers as the test says.

    The probe is started as `<python3> -I -m hearth.integrations.reach <address>...`, so
    what the image carries at that path is what decides the measurement. Handing it a
    canned answer is how a fence that holds and a fence that does not are both tested
    without a network to arrange.
    """
    docker = fake_docker.install(tmp_path)
    fake_docker.hold(docker, image=IMAGE, network=NETWORK)
    if body is None:
        body = f"import json,sys;print(json.dumps({answers!r}))"
    fake_docker.carry(docker, PYTHON, f"#!{sys.executable}\n{body}\n".encode())
    # The CLI a whole instance's store is pinned to. Its bytes belong to the fake
    # runtime, so the image carries a stand-in that reports the pin's own hash --
    # what is under test here is the fence, and `configure` runs before it.
    fake_docker.carry(docker, CLI["codex"], b"the pinned codex", digest=BINARY)
    return docker


def sandbox(docker: Path) -> Sandbox:
    return Sandbox(CONTAINER, image=IMAGE, network=NETWORK, docker=str(docker))


def listening() -> tuple[str, int, socket.socket]:
    server = socket.socket()
    server.bind(("127.0.0.1", 0))
    server.listen(1)
    return "127.0.0.1", server.getsockname()[1], server


# -- what a fence may say --------------------------------------------------


@pytest.mark.parametrize(
    "values",
    [
        {"shut": ("hearth",)},
        {"shut": ("hearth:0",)},
        {"shut": ("hearth:70000",)},
        {"shut": ("hearth:80 ",)},
        {"shut": (":80",)},
        {"open": ("chatgpt.com:443; rm -rf /",)},
        {"shut": ("one.example:80",), "open": ("one.example:80",)},
        {"shut": tuple(f"host{index}.example:80" for index in range(20))},
    ],
)
def test_a_fence_that_cannot_be_measured_as_written_is_refused_at_configuration(values):
    with pytest.raises(Refused, match="sandbox_fence_invalid"):
        Fence(**values)


def test_the_environment_names_both_halves_and_an_unset_one_is_empty():
    assert Fence.from_environment({}) == Fence()
    assert Fence.from_environment(
        {
            "HEARTH_SANDBOX_SHUT": "10.0.0.4:8000, 192.168.1.1:80",
            "HEARTH_SANDBOX_OPEN": "chatgpt.com:443",
        }
    ) == Fence(shut=("10.0.0.4:8000", "192.168.1.1:80"), open=("chatgpt.com:443",))


# -- what the probe container is ------------------------------------------


def test_the_probe_runs_the_image_s_own_interpreter_on_the_sandbox_network(tmp_path):
    docker = daemon(tmp_path, {"10.0.0.4:8000": "dropped", "chatgpt.com:443": "connected"})
    fence = Fence(shut=("10.0.0.4:8000",), open=("chatgpt.com:443",))
    state = fence.observe(sandbox(docker))
    assert state == {
        "shut": {"10.0.0.4:8000": "dropped"},
        "open": {"chatgpt.com:443": "connected"},
        "held": True,
    }
    argv = fake_docker.calls(docker)[-1]
    assert argv[:1] == ["run"]
    assert argv[argv.index("--network") + 1] == NETWORK
    assert argv[argv.index("--entrypoint") + 1] == PYTHON
    assert argv[argv.index("--label") + 1] == LABEL + "=1"
    assert "--read-only" in argv and "--rm" in argv
    # Nothing of the host reaches the probe: no mount, and no environment of its own.
    assert "--mount" not in argv and "--env" not in argv and "-v" not in argv
    # The image, then the probe, then every address it is to dial, shut before open.
    assert argv[-6:] == [
        IMAGE,
        "-I",
        "-m",
        "hearth.integrations.reach",
        "10.0.0.4:8000",
        "chatgpt.com:443",
    ]


def test_the_real_probe_answers_through_the_launcher_the_fence_uses(tmp_path):
    """The image's interpreter really running `reach`, against real local sockets."""
    host, port, server = listening()
    backend = str(Path(__file__).resolve().parents[2] / "backend")
    docker = daemon(
        tmp_path,
        body=(
            "import sys\n"
            f"sys.path.insert(0, {backend!r})\n"
            "from hearth.integrations import reach\n"
            # argv is `-I -m hearth.integrations.reach <address>...`, exactly as the
            # image's own interpreter would be handed it.
            "sys.exit(reach.main(sys.argv[4:]))"
        ),
    )
    closed = socket.socket()
    closed.bind(("127.0.0.1", 0))
    shut_port = closed.getsockname()[1]
    closed.close()
    fence = Fence(shut=(f"127.0.0.1:{shut_port}",), open=(f"{host}:{port}",))
    try:
        state = fence.observe(sandbox(docker))
    finally:
        server.close()
    # Nothing is listening on the shut port, so the kernel resets it -- which is a
    # packet that arrived, and therefore a fence that does not hold.
    assert state["shut"] == {f"127.0.0.1:{shut_port}": "refused"}
    assert state["open"] == {f"{host}:{port}": "connected"}
    assert state["held"] is False


# -- what the answers mean -------------------------------------------------


@pytest.mark.parametrize("answer", ["dropped", "no route"])
def test_a_shut_address_holds_only_when_the_packet_did_not_get_out(tmp_path, answer):
    docker = daemon(tmp_path, {"10.0.0.4:8000": answer, "chatgpt.com:443": "connected"})
    fence = Fence(shut=("10.0.0.4:8000",), open=("chatgpt.com:443",))
    assert fence.observe(sandbox(docker))["held"] is True


@pytest.mark.parametrize("answer", ["connected", "refused", "unresolved", "unreadable"])
def test_an_answer_that_is_not_a_dropped_packet_leaves_the_fence_open(tmp_path, answer):
    docker = daemon(tmp_path, {"10.0.0.4:8000": answer, "chatgpt.com:443": "connected"})
    fence = Fence(shut=("10.0.0.4:8000",), open=("chatgpt.com:443",))
    assert fence.observe(sandbox(docker))["held"] is False


@pytest.mark.parametrize("answer", ["dropped", "refused", "unresolved"])
def test_a_provider_the_session_cannot_reach_is_a_fence_that_does_not_hold(tmp_path, answer):
    docker = daemon(tmp_path, {"10.0.0.4:8000": "dropped", "chatgpt.com:443": answer})
    fence = Fence(shut=("10.0.0.4:8000",), open=("chatgpt.com:443",))
    assert fence.observe(sandbox(docker))["held"] is False


# -- what refuses ----------------------------------------------------------


def test_a_container_launcher_with_no_fence_to_measure_refuses_rather_than_assuming(tmp_path):
    docker = daemon(tmp_path, {})
    with pytest.raises(Refused, match="sandbox_fence_unconfigured"):
        Fence().observe(sandbox(docker))
    with pytest.raises(Refused, match="sandbox_fence_unconfigured"):
        Fence(shut=("10.0.0.4:8000",)).observe(sandbox(docker))
    with pytest.raises(Refused, match="sandbox_fence_unconfigured"):
        Fence(open=("chatgpt.com:443",)).observe(sandbox(docker))


def test_a_daemon_that_will_not_answer_is_not_a_fence_that_held_or_failed(tmp_path):
    fence = Fence(shut=("10.0.0.4:8000",), open=("chatgpt.com:443",))
    absent = Sandbox(CONTAINER, image=IMAGE, network=NETWORK, docker="/nonexistent/docker")
    with pytest.raises(Refused, match="sandbox_runtime_unavailable"):
        fence.observe(absent)


def test_a_probe_that_says_nothing_readable_is_never_read_as_a_fence_holding(tmp_path):
    fence = Fence(shut=("10.0.0.4:8000",), open=("chatgpt.com:443",))
    for index, body in enumerate(
        (
            "print('not json')",
            "import sys;sys.exit(3)",
            # An answer about addresses nobody asked about is not this fence's answer.
            "import json;print(json.dumps({'somewhere.else:80': 'dropped'}))",
            "import json;print(json.dumps({'10.0.0.4:8000': 'dropped'}))",
            "import json;print(json.dumps([]))",
        )
    ):
        folder = tmp_path / f"daemon-{index}"
        folder.mkdir()
        docker = daemon(folder, body=body)
        with pytest.raises(Refused, match="sandbox_fence_unmeasured"):
            fence.observe(sandbox(docker))


# -- what a start does with it ---------------------------------------------


def test_the_process_launcher_measures_no_fence_and_asks_no_daemon(tmp_path):
    assert check(store(tmp_path), Sandbox(), Fence()) == {}


def test_a_start_records_what_it_saw_and_opens_when_the_fence_holds(tmp_path):
    database = store(tmp_path)
    docker = daemon(tmp_path, {"10.0.0.4:8000": "dropped", "chatgpt.com:443": "connected"})
    fence = Fence(shut=("10.0.0.4:8000",), open=("chatgpt.com:443",))
    state = check(database, sandbox(docker), fence, lambda: 1_788_640_000)
    assert state["held"] is True
    with database.transaction() as db:
        rows = db.execute(
            "SELECT resource_id, detail FROM audit WHERE kind='sandbox.fence'"
        ).fetchall()
    assert [row[0] for row in rows] == [CONTAINER]
    detail = json.loads(rows[0][1])
    assert detail["held"] is True
    assert detail["shut"] == {"10.0.0.4:8000": "dropped"}
    assert detail["open"] == {"chatgpt.com:443": "connected"}
    assert detail["network"] == NETWORK


def test_a_fence_that_does_not_hold_refuses_the_start_and_says_what_it_saw(tmp_path):
    database = store(tmp_path)
    docker = daemon(tmp_path, {"10.0.0.4:8000": "connected", "chatgpt.com:443": "connected"})
    fence = Fence(shut=("10.0.0.4:8000",), open=("chatgpt.com:443",))
    with pytest.raises(Refused, match="sandbox_network_open"):
        check(database, sandbox(docker), fence, lambda: 1_788_640_000)
    # The observation is in the store even though the start did not happen: an
    # operator reads what was reachable, not merely that something was.
    with database.transaction() as db:
        rows = db.execute("SELECT detail FROM audit WHERE kind='sandbox.fence'").fetchall()
    detail = json.loads(rows[0][0])
    assert detail["held"] is False
    assert detail["shut"] == {"10.0.0.4:8000": "connected"}


# -- what an instance does with it -----------------------------------------


def instance(tmp_path, docker, fence):
    return create_app(
        tmp_path,
        TOKEN,
        supervise=False,
        runtime=fake_runtime(),
        sandbox=sandbox(docker),
        fence=fence,
    )


def test_an_instance_on_an_open_fence_does_not_open_at_all(tmp_path):
    docker = daemon(tmp_path, {"10.0.0.4:8000": "connected", "chatgpt.com:443": "connected"})
    fence = Fence(shut=("10.0.0.4:8000",), open=("chatgpt.com:443",))
    with pytest.raises(Refused, match="sandbox_network_open"):
        instance(tmp_path / "data", docker, fence)


def test_an_instance_on_a_container_launcher_with_no_fence_does_not_open(tmp_path):
    docker = daemon(tmp_path, {})
    with pytest.raises(Refused, match="sandbox_fence_unconfigured"):
        instance(tmp_path / "data", docker, Fence())


def test_the_operator_reads_the_fence_afresh_and_the_open_answer_never_names_it(tmp_path):
    docker = daemon(tmp_path, {"10.0.0.4:8000": "dropped", "chatgpt.com:443": "connected"})
    fence = Fence(shut=("10.0.0.4:8000",), open=("chatgpt.com:443",))
    with TestClient(instance(tmp_path / "data", docker, fence)) as client:
        # Liveness says which sandbox is deployed and nothing about this building:
        # an address on the fence names a machine on somebody's LAN.
        assert client.get("/health").json()["sandbox"] == {
            "launcher": "container",
            "image": DIGEST,
        }
        assert "10.0.0.4" not in client.get("/health").text
        health = client.get("/api/health", headers={"Authorization": "Bearer " + TOKEN}).json()
    assert health["sandbox"]["network"] == NETWORK
    assert health["sandbox"]["fence"] == {
        "shut": {"10.0.0.4:8000": "dropped"},
        "open": {"chatgpt.com:443": "connected"},
        "held": True,
    }
    # Measured again on the ask, not remembered from the start: a fence that opened
    # an hour ago is exactly what an operator is asking about.
    probes = [call for call in fake_docker.calls(docker) if "hearth.integrations.reach" in call]
    assert len(probes) == 2


def test_a_fence_that_cannot_be_measured_later_is_reported_and_not_raised(tmp_path):
    docker = daemon(tmp_path, {"10.0.0.4:8000": "dropped", "chatgpt.com:443": "connected"})
    fence = Fence(shut=("10.0.0.4:8000",), open=("chatgpt.com:443",))
    application = instance(tmp_path / "data", docker, fence)
    with TestClient(application) as client:
        fake_docker.carry(docker, PYTHON, f"#!{sys.executable}\nimport sys;sys.exit(3)\n".encode())
        response = client.get("/api/health", headers={"Authorization": "Bearer " + TOKEN})
    # A measurement that failed is not a fence that held, and it is not a 500 either:
    # the operator's own page is where a burrow's own trouble is read.
    assert response.status_code == 200
    assert response.json()["sandbox"]["fence"] == {
        "held": False,
        "error": "sandbox_fence_unmeasured",
    }


def test_the_process_launcher_says_nothing_about_a_fence_it_does_not_have(tmp_path):
    application = create_app(
        tmp_path / "data", TOKEN, supervise=False, runtime=fake_runtime(), fence=Fence()
    )
    with TestClient(application) as client:
        health = client.get("/api/health", headers={"Authorization": "Bearer " + TOKEN}).json()
    assert health["sandbox"] == {"launcher": "process"}
