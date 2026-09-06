"""Real SQLite/input files, deterministic Docker fault boundary; no Docker in CI."""

import json
from concurrent.futures import ThreadPoolExecutor

import pytest
from hearth.container_rehearsal import FIXTURE, IMAGE, LABEL, ContainerRehearsal
from hearth.core import Hearth
from hearth.database import Database
from hearth.memory import Memory
from hearth.models import Declaration, Refused


class Docker:
    def __init__(self, run_id):
        self.run_id = run_id
        self.calls = []
        self.container = None
        self.lose = None
        self.raw = ""

    def __call__(self, *args):
        self.calls.append(args)
        command = args[0]
        if self.lose == "before_create" and command == "create":
            raise OSError("dispatch unknown")
        if command == "create":
            name = args[args.index("--name") + 1]
            label = args[args.index("--label") + 1].split("=", 1)[1]
            self.container = {
                "Id": "synthetic-id",
                "Name": "/" + name,
                "Config": {
                    "Labels": {LABEL: label},
                    "Image": IMAGE,
                    "User": args[args.index("--user") + 1],
                    "Entrypoint": ["python3"],
                    "Cmd": ["-I", "-c", FIXTURE, args[-2], args[-1]],
                },
                "HostConfig": {
                    "NetworkMode": "none",
                    "ReadonlyRootfs": True,
                    "Privileged": False,
                    "PidMode": "",
                    "CapDrop": ["ALL"],
                    "CapAdd": None,
                    "SecurityOpt": ["no-new-privileges=true", "seccomp=builtin"],
                },
                "Mounts": [
                    {
                        "Source": args[args.index("--mount") + 1]
                        .split("source=", 1)[1]
                        .split(",target=", 1)[0],
                        "Destination": "/input",
                        "RW": False,
                    }
                ],
                "State": {"Running": False, "Status": "created", "Pid": 0, "ExitCode": 0},
            }
            result = "synthetic-id"
        elif command == "container":
            if self.container is None:
                raise OSError("absent or daemon unavailable")
            return json.dumps([self.container])
        elif command == "start":
            self.container["State"].update(Running=True, Status="running", Pid=123)
            result = "synthetic-id"
        elif command == "stop":
            self.container["State"].update(Running=False, Status="exited", Pid=0, ExitCode=137)
            result = "synthetic-id"
        elif command == "rm":
            self.container = None
            result = "synthetic-id"
        elif command == "logs":
            return self.raw
        else:
            raise AssertionError(args)
        if self.lose == command:
            raise OSError("lost acknowledgement")
        return result

    def complete(self):
        self.container["State"].update(Running=False, Status="exited", Pid=0, ExitCode=0)
        self.raw = "\n".join(
            json.dumps(event)
            for event in [
                {"type": "thread.started", "thread_id": self.run_id},
                {"type": "turn.started"},
                {
                    "type": "item.completed",
                    "item": {"id": "answer", "type": "agent_message", "text": "Synthetic summary"},
                },
                {"type": "turn.completed"},
            ]
        )


@pytest.fixture
def system(tmp_path):
    database = Database(tmp_path / "hearth.db")
    database.initialize()
    hearth = Hearth(database, clock=lambda: 1000)
    hearth.save_resident(
        "reader", Declaration("Reader", "Synthetic purpose", 10000), expected_revision=0
    )
    Memory(hearth).save("reader", "Pinned memory", expected_revision=0)
    task = hearth.submit("task", "reader", "Synthetic summary", expires_at=1500)
    run = hearth.admit(task.task_id, reserve=3000)
    docker = Docker(run.id)
    root = tmp_path / "worker"
    return hearth, run, docker, root


def test_reopen_reuses_claim_and_private_actual_stage(system):
    hearth, run, docker, root = system
    worker = ContainerRehearsal(hearth.database, root, docker=docker)
    assert worker.start(run.id).status == "running"
    path = root / "inputs" / run.id / "context.json"
    original = path.read_bytes()
    Memory(hearth).save("reader", "Future memory", expected_revision=1)
    reopened = ContainerRehearsal(hearth.database, root, docker=docker)
    assert reopened.start(run.id).status == "running"
    assert path.read_bytes() == original and path.stat().st_mode & 0o777 == 0o400
    assert path.parent.stat().st_mode & 0o777 == 0o700
    assert [c[0] for c in docker.calls].count("create") == 1
    assert [c[0] for c in docker.calls].count("start") == 1
    docker.complete()
    observation = reopened.inspect(run.id)
    assert observation.status == "exited" and observation.transcript.output == "Synthetic summary"
    assert observation.transcript.usage is None


@pytest.mark.parametrize("lost", ["before_create", "create", "start"])
def test_uncertain_dispatch_never_repeats_create_or_start(system, lost):
    hearth, run, docker, root = system
    docker.lose = lost
    worker = ContainerRehearsal(hearth.database, root, docker=docker)
    with pytest.raises(OSError):
        worker.start(run.id)
    before = [c for c in docker.calls if c[0] in {"create", "start"}]
    docker.lose = None
    result = ContainerRehearsal(hearth.database, root, docker=docker).start(run.id)
    assert result.status == ("running" if lost == "start" else "unknown")
    assert [c for c in docker.calls if c[0] in {"create", "start"}] == before


def test_cancellation_and_removed_container_never_release_claim(system):
    hearth, run, docker, root = system
    worker = ContainerRehearsal(hearth.database, root, docker=docker)
    worker.start(run.id, scenario="hold")
    assert worker.stop(run.id).status == "exited"
    worker.remove(run.id)
    assert worker.start(run.id, scenario="hold").status == "unknown"
    assert [c[0] for c in docker.calls].count("create") == 1


@pytest.mark.parametrize("wrong", ["label", "id", "name"])
def test_wrong_container_is_never_stopped_or_removed(system, wrong):
    hearth, run, docker, root = system
    worker = ContainerRehearsal(hearth.database, root, docker=docker)
    worker.start(run.id)
    if wrong == "label":
        docker.container["Config"]["Labels"][LABEL] = "foreign"
    else:
        docker.container["Id" if wrong == "id" else "Name"] = "foreign"
    assert worker.inspect(run.id).status == "unknown"
    for action in (worker.stop, worker.remove):
        with pytest.raises(Refused, match="container_ownership_mismatch"):
            action(run.id)
    assert not any(c[0] in {"stop", "rm"} for c in docker.calls)


def test_changed_scenario_and_changed_stage_refuse_without_dispatch(system):
    hearth, run, docker, root = system
    worker = ContainerRehearsal(hearth.database, root, docker=docker)
    worker.start(run.id)
    with pytest.raises(Refused, match="container_identity_conflict"):
        worker.start(run.id, scenario="hold")
    path = root / "inputs" / run.id / "context.json"
    path.chmod(0o600)
    path.write_text("altered")
    path.chmod(0o400)
    with pytest.raises(Refused, match="staged_input_conflict"):
        worker.start(run.id)
    assert [c[0] for c in docker.calls].count("create") == 1


def test_concurrent_start_dispatches_once(system):
    hearth, run, docker, root = system
    worker = ContainerRehearsal(hearth.database, root, docker=docker)
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: worker.start(run.id), range(8)))
    assert all(result.status == "running" for result in results)
    assert [c[0] for c in docker.calls].count("create") == 1


def test_cross_run_output_is_not_accepted(system):
    hearth, run, docker, root = system
    worker = ContainerRehearsal(hearth.database, root, docker=docker)
    worker.start(run.id)
    docker.run_id = "other-run"
    docker.complete()
    assert worker.inspect(run.id).status == "unknown"


def test_missing_pinned_identity_never_adopts_running_container(system):
    hearth, run, docker, root = system
    worker = ContainerRehearsal(hearth.database, root, docker=docker)
    worker.start(run.id)
    (root / run.id / "identity.json").unlink()
    assert worker.inspect(run.id).status == "unknown"
    with pytest.raises(Refused, match="container_ownership_mismatch"):
        worker.stop(run.id)
    assert not any(c[0] == "stop" for c in docker.calls)


def test_wrong_policy_refuses_before_start(system):
    hearth, run, docker, root = system

    def altered(*args):
        result = docker(*args)
        if args[0] == "create":
            docker.container["HostConfig"]["NetworkMode"] = "bridge"
        return result

    worker = ContainerRehearsal(hearth.database, root, docker=altered)
    with pytest.raises(Refused, match="container_configuration_mismatch"):
        worker.start(run.id)
    assert not any(c[0] == "start" for c in docker.calls)
    assert worker.start(run.id).status == "unknown"
    assert [c[0] for c in docker.calls].count("create") == 1


def test_foreign_claim_directory_cannot_control_other_root(system, tmp_path):
    hearth, run, docker, root = system
    ContainerRehearsal(hearth.database, root, docker=docker).start(run.id)
    foreign_root = tmp_path / "foreign"
    other = ContainerRehearsal(hearth.database, foreign_root, docker=docker)
    (foreign_root / run.id).symlink_to(root / run.id, target_is_directory=True)
    with pytest.raises(Refused, match="container_claim_invalid"):
        other.stop(run.id)
    assert not any(c[0] == "stop" for c in docker.calls)
