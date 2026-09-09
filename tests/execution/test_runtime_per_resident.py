"""One store, two brains: each run is worked by the runtime its declaration named.

The store's `runtime_kind` is the default a resident falls back to, not the only
answer it may give (`docs/adr/0015-runtime-per-resident.md`). Here one resident keeps
the default while another declares the second live kind, both are admitted from the
same household, and the executor hands each run to its own provider's adapter.
"""

import pytest
from hearth.execution.lifecycle import Execution, Executor
from hearth.integrations.claude.config import KIND as CLAUDE_KIND
from hearth.integrations.codex.subscription import KIND as CODEX_KIND
from hearth.residents.models import Declaration, Refused
from hearth.storage.artifacts import Artifacts
from hearth.storage.database import Database
from hearth.work.service import Hearth

from tests.fake_runtime import CLAUDE_ANSWER_COST, FakeClaudeRuntime, FakeRuntime


def household(tmp_path, *, claude="success"):
    """Karen on the store's default, a second resident on the other live runtime.

    Both runtimes are configured here first, because a store records which providers
    it has been configured for and refuses to declare a resident onto one it never had.
    """
    data = tmp_path / "data"
    database = Database(data / "hearth.db")
    database.initialize()
    adapters = {"codex": FakeRuntime(data), "claude": FakeClaudeRuntime(data, scenario=claude)}
    hearth = Hearth(database)
    hearth.save_resident(
        "karen", Declaration("Karen", "Runs the household", 10_000_000), expected_revision=0
    )
    hearth.save_resident(
        "scribe",
        Declaration("Scribe", "Writes on the second brain", 10_000_000, runtime=CLAUDE_KIND),
        expected_revision=0,
    )
    execution = Execution(hearth, Artifacts(data / "artifacts"))
    return hearth, execution, adapters


def admit(hearth, resident_id, key):
    task = hearth.submit(key, resident_id, "Summarize", expires_at=int(hearth.clock()) + 600)
    return hearth.admit(task.task_id, reserve=100_000)


def test_two_residents_on_one_store_each_reach_their_own_runtime(tmp_path):
    hearth, execution, adapters = household(tmp_path)
    karen = admit(hearth, "karen", "karen-task")
    scribe = admit(hearth, "scribe", "scribe-task")
    # The pin is written at admission, from the declaration and not from the store.
    assert (karen.runtime_kind, scribe.runtime_kind) == (CODEX_KIND, CLAUDE_KIND)

    settled = {run.resident_id: run for run in Executor(execution, adapters.values()).step()}
    assert settled["karen"].status == "succeeded" and settled["scribe"].status == "succeeded"
    # Each answer is its own provider's, priced by its own schedule.
    assert settled["karen"].actual_cost == 2000
    assert settled["scribe"].actual_cost == CLAUDE_ANSWER_COST
    # And each run's work really happened in its own runtime's folder.
    assert (tmp_path / "data/fake-runtime" / karen.id).is_dir()
    assert (tmp_path / "data/fake-claude-runtime" / scribe.id).is_dir()
    assert not (tmp_path / "data/fake-runtime" / scribe.id).exists()
    assert not (tmp_path / "data/fake-claude-runtime" / karen.id).exists()


def test_an_unlaunched_run_whose_runtime_is_gone_ends_at_zero_without_launching(tmp_path):
    hearth, execution, adapters = household(tmp_path)
    scribe = admit(hearth, "scribe", "scribe-task")
    # The instance is reopened without the second provider, as a host that lost its
    # configuration would come back.
    executor = Executor(execution, [adapters["codex"]])
    held = executor.step()[0]

    assert held.id == scribe.id
    # Nothing here can start it, so it is asked to stop rather than launched anywhere.
    assert held.status == "stopping" and not held.launch_attempted
    assert not (tmp_path / "data/fake-claude-runtime" / scribe.id).exists()
    assert not (tmp_path / "data/fake-runtime" / scribe.id).exists()
    with hearth.database.transaction() as db:
        details = [
            row[0]
            for row in db.execute(
                "SELECT detail FROM audit WHERE kind='run.cancel_requested' AND resource_id=?",
                (scribe.id,),
            )
        ]
    assert len(details) == 1 and '"reason": "runtime_unavailable"' in details[0]

    # The next pass settles it at zero, from a receipt proving it never launched.
    settled = executor.step()[0]
    assert settled.status == "cancelled"
    assert settled.actual_cost == 0 and settled.usage_known
    assert not (tmp_path / "data/fake-claude-runtime" / scribe.id).exists()
    # The resident is free to work again; nothing was spent and nothing is paused.
    with hearth.database.transaction() as db:
        assert db.execute("SELECT COUNT(*) FROM pauses").fetchone()[0] == 0


def test_a_launched_run_whose_runtime_is_gone_stays_unknown_and_is_never_retried(tmp_path):
    hearth, execution, adapters = household(tmp_path, claude="hold")
    scribe = admit(hearth, "scribe", "scribe-task")
    assert Executor(execution, adapters.values()).step()[0].status == "running"

    executor = Executor(execution, [adapters["codex"]])
    held = executor.step()[0]
    assert held.status == "interrupted" and held.finished_at is None
    # Waiting is not retrying, and a second pass says nothing new.
    assert executor.step()[0].status == "interrupted"
    with hearth.database.transaction() as db:
        details = [
            row[0]
            for row in db.execute(
                "SELECT detail FROM audit WHERE kind='run.interrupted' AND resource_id=?",
                (scribe.id,),
            )
        ]
    assert len(details) == 1 and '"reason": "runtime_unavailable"' in details[0]

    # Configure the runtime again and the same run is observed where it really is.
    assert Executor(execution, adapters.values()).step()[0].status == "running"


def test_the_store_default_must_be_a_runtime_this_instance_is_configured_for(tmp_path):
    hearth, execution, adapters = household(tmp_path)
    with pytest.raises(Refused, match="runtime_store_mismatch"):
        Executor(execution, [adapters["claude"]]).step()


def test_a_declaration_may_only_name_a_runtime_this_release_ships(tmp_path):
    for kind in ("inline_mock", "codex_mock", "shell", ""):
        with pytest.raises(Refused, match="runtime_not_configured"):
            Declaration("Scribe", "Writes", 1000, runtime=kind).validate()
    # Both live kinds are declarable, and no runtime at all is the store's default.
    for kind in (CODEX_KIND, CLAUDE_KIND, None):
        Declaration("Scribe", "Writes", 1000, runtime=kind).validate()


def test_a_runtime_this_store_was_never_configured_for_is_refused_at_the_declaration(tmp_path):
    data = tmp_path / "data"
    database = Database(data / "hearth.db")
    database.initialize()
    FakeRuntime(data)
    hearth = Hearth(database)
    with pytest.raises(Refused, match="runtime_not_configured"):
        hearth.save_resident(
            "scribe",
            Declaration("Scribe", "Writes", 10_000_000, runtime=CLAUDE_KIND),
            expected_revision=0,
        )
    with database.transaction() as db:
        assert db.execute("SELECT COUNT(*) FROM residents").fetchone()[0] == 0
    # Configuring that provider is what makes the declaration possible.
    FakeClaudeRuntime(data)
    hearth.save_resident(
        "scribe",
        Declaration("Scribe", "Writes", 10_000_000, runtime=CLAUDE_KIND),
        expected_revision=0,
    )
    assert hearth.resident("scribe").declaration.runtime == CLAUDE_KIND


def test_a_declared_runtime_survives_every_save_that_does_not_mention_it(tmp_path):
    hearth, _, _ = household(tmp_path)
    with hearth.database.transaction() as db:
        assert hearth.declared_runtime(db, "scribe") == CLAUDE_KIND
    # Rewriting what the resident is keeps the brain it runs on.
    hearth.save_resident(
        "scribe",
        Declaration(
            "Scribe",
            "Writes rather more",
            10_000_000,
            runtime=hearth.resident("scribe").declaration.runtime,
        ),
        expected_revision=1,
    )
    with hearth.database.transaction() as db:
        assert hearth.declared_runtime(db, "scribe") == CLAUDE_KIND
    # A run admitted afterwards is still pinned to it.
    assert admit(hearth, "scribe", "later").runtime_kind == CLAUDE_KIND
