"""The actual launch input agrees with scoped reads and stays pinned during races."""

import hashlib
import json

import pytest
from hearth.authority.run_access import RunAccess
from hearth.execution.lifecycle import Execution, Executor
from hearth.residents.models import Declaration
from hearth.storage.artifacts import Artifacts
from hearth.storage.database import Database
from hearth.work.service import Hearth

from tests.fake_runtime import FakeRuntime

NOW = 1_788_640_000


@pytest.fixture
def system(tmp_path):
    database = Database(tmp_path / "hearth.db")
    database.initialize()
    hearth = Hearth(database, clock=lambda: NOW)
    hearth.save_resident(
        "reader", Declaration("Reader", "Original synthetic purpose", 10000), expected_revision=0
    )
    receipt = hearth.submit(
        "first", "reader", "Summarize the synthetic notes", expires_at=NOW + 600
    )
    run = hearth.admit(receipt.task_id, reserve=3000)
    worker = Executor(Execution(hearth, Artifacts(tmp_path / "artifacts")), FakeRuntime(tmp_path))
    return hearth, worker, run, tmp_path


def changed_declaration(hearth):
    hearth.save_resident(
        "reader", Declaration("Reader", "Changed synthetic purpose", 10000), expected_revision=1
    )


def capture_launch(worker, monkeypatch):
    inputs = []
    original = worker.runtime.start

    def start(run_id, instruction):
        inputs.append((run_id, instruction))
        original(run_id, instruction)

    monkeypatch.setattr(worker.runtime, "start", start)
    return inputs


def test_launch_and_scoped_context_match_without_authority_secrets(system, monkeypatch):
    hearth, worker, run, root = system
    access = RunAccess(hearth)
    credential = access.issue(run.id, run.owner_token)
    expected = access.context(credential.token, run.id)
    inputs = capture_launch(worker, monkeypatch)
    assert worker.step()[0].status == "succeeded"
    assert len(inputs) == 1 and inputs[0][0] == run.id
    sent = inputs[0][1]
    assert json.loads(sent) == expected
    assert expected["purpose"] == "Original synthetic purpose"
    assert expected["resident_revision"] == 1 and expected["context_version"] == 9
    assert expected["instruction"] == "Summarize the synthetic notes"
    assert expected["notes"] == []
    # Reader is unchanged: it opens with no journal, cannot write its memory, and is
    # working its own task rather than answering anybody's letter.
    assert expected["journal"] == [] and expected["memory_writable"] is False
    assert expected["letter"] is None and expected["replies"] == []
    assert set(expected) == {
        "context_version",
        "skills",
        "skill_text",
        "memory",
        "memory_writable",
        "journal",
        "journal_usage",
        "run_id",
        "task_id",
        "resident_id",
        "resident_revision",
        "purpose",
        "instruction",
        "letter",
        "replies",
        "replies_usage",
        "notes",
        "inputs",
        "input_state",
        "input_usage",
    }
    assert run.owner_token not in sent and credential.token not in sent
    evidence = (root / "fake-runtime" / run.id / "request.json").read_text()
    digest = json.loads(evidence)["binding"]["input_digest"]
    assert digest == hashlib.sha256(sent.encode()).hexdigest()
    assert expected["purpose"] not in evidence and expected["instruction"] not in evidence
    assert expected["purpose"] not in json.dumps(hearth.audit())


@pytest.mark.parametrize("launch_intent", [False, True])
def test_changed_declaration_before_launch_refuses_and_can_cancel_unlaunched_work(
    system, launch_intent
):
    hearth, worker, run, _ = system
    if launch_intent:
        assert worker.execution.prepare_start(run.id, run.owner_token)
    changed_declaration(hearth)
    assert worker.step()[0].status == "interrupted"
    assert worker.runtime.inspect(run.id).status == "absent"
    assert hearth.run(run.id).finished_at is None
    worker.execution.cancel(run.id)
    after = worker.step()[0]
    # An old launch intent may represent an unobserved runtime, so keep uncertainty.
    assert after.status == ("interrupted" if launch_intent else "cancelled")
    assert after.actual_cost == (None if launch_intent else 0)


def test_configuration_change_after_launch_authorization_cannot_replace_pinned_input(
    system, monkeypatch
):
    hearth, worker, run, _ = system
    inputs = capture_launch(worker, monkeypatch)
    original = worker.execution.prepare_start

    def authorize_then_change(run_id, token):
        authorized = original(run_id, token)
        changed_declaration(hearth)
        return authorized

    monkeypatch.setattr(worker.execution, "prepare_start", authorize_then_change)
    assert worker.step()[0].status == "succeeded"
    assert hearth.resident("reader").revision == 2
    sent = json.loads(inputs[0][1])
    assert sent["resident_revision"] == 1
    assert sent["purpose"] == "Original synthetic purpose"
    assert hearth.run(run.id).resident_revision == 1


def test_restart_after_a_lost_launch_never_sends_the_input_again(system, monkeypatch):
    hearth, worker, run, root = system
    attempted = []

    def lose_call(run_id, instruction):
        attempted.append(instruction)
        raise RuntimeError("crash before runtime call")

    monkeypatch.setattr(worker.runtime, "start", lose_call)
    with pytest.raises(RuntimeError, match="crash before runtime call"):
        worker.step()
    assert hearth.run(run.id).launch_attempted
    reopened = Executor(
        Execution(Hearth(Database(root / "hearth.db")), Artifacts(root / "artifacts")),
        FakeRuntime(root),
    )
    inputs = capture_launch(reopened, monkeypatch)
    # The lost call may have reached the provider, so the restart never repeats it.
    assert reopened.step()[0].status == "interrupted"
    assert inputs == [] and attempted[0]
    assert hearth.run(run.id).finished_at is None


def test_existing_runtime_evidence_is_recovered_after_configuration_change(system, monkeypatch):
    hearth, worker, run, root = system
    original = worker.runtime.start

    def start_then_crash(run_id, instruction):
        original(run_id, instruction)
        raise RuntimeError("lost acknowledgement")

    monkeypatch.setattr(worker.runtime, "start", start_then_crash)
    with pytest.raises(RuntimeError, match="lost acknowledgement"):
        worker.step()
    changed_declaration(hearth)
    reopened = Executor(worker.execution, FakeRuntime(root))
    inputs = capture_launch(reopened, monkeypatch)
    assert reopened.step()[0].status == "succeeded"
    assert inputs == []
    assert hearth.run(run.id).resident_revision == 1


def test_next_run_uses_new_declaration_after_prior_work_settles(system, monkeypatch):
    hearth, worker, _, _ = system
    worker.step()
    changed_declaration(hearth)
    receipt = hearth.submit("next", "reader", "Next synthetic task", expires_at=NOW + 600)
    next_run = hearth.admit(receipt.task_id, reserve=3000)
    inputs = capture_launch(worker, monkeypatch)
    worker.step()
    sent = json.loads(inputs[0][1])
    assert inputs[0][0] == next_run.id
    assert sent["resident_revision"] == 2 and sent["purpose"] == "Changed synthetic purpose"
    assert sent["instruction"] == "Next synthetic task"
