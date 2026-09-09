"""A granted folder, from the grant to the container's argv to the run's own receipt.

Epic #183 slice D (#187). The grant is `tests/management/test_filesystem_grants.py`;
what is here is the half below the worker: the mount a container is started with, the
folder a run on the process launcher reaches instead, and how Hearth comes to say that
a writable one was used. Nothing here needs a daemon -- `tests/fake_docker.py` runs the
command it is given, so the session sees the host's own paths either way, which is why
the fake CLI writes into `host_path` rather than into `/mounts/<name>`.
"""

import json

import pytest
from hearth.execution.lifecycle import Execution
from hearth.integrations.codex.subscription import encode as codex_encode
from hearth.integrations.codex.subscription import worker as codex_worker
from hearth.integrations.codex.usage import UsageBinding
from hearth.integrations.launcher import Sandbox, survey, written_mounts
from hearth.residents.models import Refused
from hearth.storage.artifacts import Artifacts
from hearth.work.service import Hearth

from tests import fake_docker
from tests.integrations.test_launcher_parity import IMAGE, NETWORK, daemon, prepared_codex


def granted(tmp_path, mounts, *, container=True, writes=True):
    """One admitted run whose resident holds `mounts`, ready for its worker to be run."""
    docker = daemon(tmp_path) if container else None
    sandbox = (
        Sandbox("container", image=IMAGE, network=NETWORK, docker=str(docker))
        if container
        else Sandbox()
    )
    runtime, run = prepared_codex(tmp_path / "store", sandbox, docker, mounts=mounts, writes=writes)
    return runtime, run, docker


def folders(tmp_path):
    shared, drafts = tmp_path / "shared", tmp_path / "drafts"
    shared.mkdir()
    drafts.mkdir()
    (shared / "note.md").write_text("A synthetic note.")
    return shared, drafts


def started(docker) -> list[str]:
    """The argv of the one container this run started."""
    calls = [
        call for call in fake_docker.calls(docker) if call[:1] == ["run"] and "--mount" in call
    ]
    assert len(calls) == 1
    return calls[0]


def mounts_of(argv) -> list[str]:
    return [argv[index + 1] for index, part in enumerate(argv) if part == "--mount"]


def test_a_granted_folder_is_one_bind_mount_at_the_name_the_grant_gave_it(tmp_path):
    shared, drafts = folders(tmp_path)
    runtime, run, docker = granted(
        tmp_path,
        [
            {"name": "notes", "host_path": str(shared)},
            {"name": "drafts", "host_path": str(drafts), "mode": "rw"},
        ],
    )
    codex_worker(runtime.folder(run.id))
    arguments = mounts_of(started(docker))
    # Read-only unless the grant said otherwise, at `/mounts/<name>` and nowhere else.
    assert f"type=bind,source={shared},target=/mounts/notes,readonly" in arguments
    assert f"type=bind,source={drafts},target=/mounts/drafts" in arguments
    # Nothing else of the host is there: Hearth's own three, and the grant's two.
    assert len(arguments) == 5
    assert not any("/mounts/" in argument and "readonly" in argument for argument in arguments[:1])


def test_a_run_that_is_not_sandboxed_still_says_what_it_was_granted(tmp_path):
    shared, _ = folders(tmp_path)
    runtime, run, _ = granted(
        tmp_path, [{"name": "notes", "host_path": str(shared)}], container=False
    )
    codex_worker(runtime.folder(run.id))
    receipt = runtime.receipt(run.id)
    # A child of its worker reaches the whole host and is confined by nothing, so the
    # honest receipt names the folder it was granted and no container.
    assert receipt["sandbox"]["launcher"] == "process"
    assert receipt["sandbox"]["container_id"] is None
    assert [entry["name"] for entry in receipt["sandbox"]["mounts"]] == ["notes"]


def test_the_receipt_says_which_writable_folder_the_run_actually_wrote_into(tmp_path):
    shared, drafts = folders(tmp_path)
    runtime, run, _ = granted(
        tmp_path,
        [
            {"name": "notes", "host_path": str(shared)},
            {"name": "drafts", "host_path": str(drafts), "mode": "rw"},
        ],
    )
    codex_worker(runtime.folder(run.id))
    receipt = runtime.receipt(run.id)
    assert (drafts / "written-by-the-session.md").is_file()
    assert receipt["sandbox"]["mounts"] == [
        # A read-only mount is not surveyed at all: nothing may write it, and saying
        # "not written" of a folder nobody looked at would be a claim Hearth cannot make.
        {"name": "notes", "mode": "ro", "written": None},
        {"name": "drafts", "mode": "rw", "written": True},
    ]
    assert written_mounts(receipt) == ["drafts"]
    # The receipt still settles, and the sandbox block is still Hearth's own writing.
    bound = UsageBinding(**receipt["binding"])
    assert codex_encode(receipt, bound)[2].status == "succeeded"


def test_a_writable_folder_the_run_left_alone_is_not_reported_as_used(tmp_path):
    _, drafts = folders(tmp_path)
    runtime, run, _ = granted(
        tmp_path, [{"name": "drafts", "host_path": str(drafts), "mode": "rw"}], writes=False
    )
    codex_worker(runtime.folder(run.id))
    receipt = runtime.receipt(run.id)
    assert receipt["sandbox"]["mounts"] == [{"name": "drafts", "mode": "rw", "written": False}]
    assert written_mounts(receipt) == []


def test_settling_a_run_that_used_a_writable_folder_records_the_fact(tmp_path):
    shared, drafts = folders(tmp_path)
    runtime, run, _ = granted(
        tmp_path,
        [
            {"name": "notes", "host_path": str(shared)},
            {"name": "drafts", "host_path": str(drafts), "mode": "rw"},
        ],
    )
    codex_worker(runtime.folder(run.id))
    receipt = runtime.receipt(run.id)
    hearth = Hearth(runtime.database)
    execution = Execution(hearth, Artifacts(runtime.data / "artifacts"))
    bound = UsageBinding(**receipt["binding"])
    execution.finish(
        run.id, run.owner_token, codex_encode(receipt, bound)[2], _usage_receipt=receipt
    )
    used = [fact for fact in hearth.audit() if fact["kind"] == "run.mount_rw_used"]
    assert [fact["detail"]["name"] for fact in used] == ["drafts"]
    assert used[0]["resource_id"] == run.id
    # The host path is read from what the run was admitted with, never from the
    # receipt: a receipt naming a folder this run was not granted names nothing.
    assert used[0]["detail"]["host_path"] == str(drafts)


def test_a_receipt_claiming_a_folder_the_run_never_held_is_refused(tmp_path):
    shared, _ = folders(tmp_path)
    runtime, run, _ = granted(tmp_path, [{"name": "notes", "host_path": str(shared)}], writes=False)
    codex_worker(runtime.folder(run.id))
    receipt = runtime.receipt(run.id)
    bound = UsageBinding(**receipt["binding"])
    for broken in (
        [{"name": "notes", "mode": "ro"}],
        [{"name": "notes", "mode": "sideways", "written": None}],
        [{"name": "notes", "mode": "ro", "written": "yes"}],
        {"notes": "ro"},
        [{"name": 1, "mode": "ro", "written": None}],
    ):
        sandbox = receipt["sandbox"] | {"mounts": broken}
        with pytest.raises(Refused, match="run_usage_invalid"):
            codex_encode(receipt | {"sandbox": sandbox}, bound)


def test_a_folder_too_large_to_look_at_twice_is_unknown_rather_than_unused(tmp_path, monkeypatch):
    from hearth.integrations import launcher

    monkeypatch.setattr(launcher, "SURVEY_LIMIT", 1)
    big = tmp_path / "big"
    big.mkdir()
    (big / "one").write_text("1")
    (big / "two").write_text("2")
    assert survey(str(big)) is None
    # And a folder that is not there at all cannot be surveyed either.
    assert survey(str(tmp_path / "gone")) is None


def test_a_run_folder_survey_notices_a_new_file_and_a_changed_one(tmp_path):
    folder = tmp_path / "folder"
    folder.mkdir()
    (folder / "note.md").write_text("first")
    before = survey(str(folder))
    assert before == survey(str(folder))
    (folder / "note.md").write_text("longer than the first")
    changed = survey(str(folder))
    assert changed != before
    (folder / "second.md").write_text("new")
    assert survey(str(folder)) != changed


def test_the_process_launcher_is_never_handed_a_mount_list(tmp_path):
    from hearth.integrations.launcher import Mount, ProcessLauncher

    with pytest.raises(Refused, match="sandbox_mounts_unsupported"):
        ProcessLauncher().start(
            ["true"],
            env={},
            cwd=tmp_path,
            stdin=None,
            mounts=(Mount(str(tmp_path), "/mounts/notes"),),
        )


def test_a_request_naming_a_mount_hearth_could_not_have_written_launches_nothing(tmp_path):
    shared, _ = folders(tmp_path)
    runtime, run, _ = granted(tmp_path, [{"name": "notes", "host_path": str(shared)}])
    folder = runtime.folder(run.id)
    request = json.loads((folder / "request.json").read_text())
    request["mounts"] = [{"name": "../escape", "host_path": str(shared), "mode": "ro"}]
    (folder / "request.json").write_text(json.dumps(request))
    codex_worker(folder)
    # The worker refuses before anything is launched and the run stays unlaunched,
    # exactly as it does for a sandbox document it cannot read.
    assert not (folder / "receipt.json").exists()
