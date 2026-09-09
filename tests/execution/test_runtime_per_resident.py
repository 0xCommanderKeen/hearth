"""One store, two brains: each run is worked by the runtime its declaration named.

The store's `runtime_kind` is the default a resident falls back to, not the only
answer it may give (`docs/adr/0015-runtime-per-resident.md`). Here one resident keeps
the default while another declares the second live kind, both are admitted from the
same household, and the executor hands each run to its own provider's adapter.
"""

from dataclasses import replace

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


def test_the_operator_snapshot_names_both_brains_and_prices_each_run_by_its_own(tmp_path):
    """Townhall reads provider names from here, so nothing in it is written by hand."""
    from hearth.observation.snapshot import snapshot

    hearth, execution, adapters = household(tmp_path)
    karen = admit(hearth, "karen", "karen-task")
    scribe = admit(hearth, "scribe", "scribe-task")
    Executor(execution, adapters.values()).step()

    state = snapshot(hearth)
    assert state["runtimes"]["default"] == CODEX_KIND
    assert set(state["runtimes"]["configured"]) == {CODEX_KIND, CLAUDE_KIND}
    kinds = state["runtimes"]["kinds"]
    assert kinds[CODEX_KIND] == {"label": "Codex subscription", "live": True}
    assert kinds[CLAUDE_KIND] == {"label": "Claude subscription", "live": True}
    # A run may carry a kind this release no longer ships, and the operator still
    # gets a name for it -- one that says it was never a provider.
    assert kinds["codex_mock"] == {"label": "retired Codex mock", "live": False}

    runs = {row["resident_id"]: row for row in state["runs"]}
    assert runs["karen"]["runtime_kind"] == CODEX_KIND
    assert (runs["karen"]["model"], runs["karen"]["price_schedule"]) == (
        "gpt-6-astra",
        "gpt-6-astra-api-equivalent-2026-09-06",
    )
    assert runs["scribe"]["runtime_kind"] == CLAUDE_KIND
    assert (runs["scribe"]["model"], runs["scribe"]["price_schedule"]) == (
        "claude-opus-5",
        "claude-opus-5-api-equivalent-2026-09-07",
    )
    assert {runs["karen"]["id"], runs["scribe"]["id"]} == {karen.id, scribe.id}


def test_an_unlaunched_run_whose_runtime_is_gone_waits_rather_than_being_thrown_away(tmp_path):
    hearth, execution, adapters = household(tmp_path)
    scribe = admit(hearth, "scribe", "scribe-task")
    # The instance is reopened without the second provider, as a host that lost its
    # configuration would come back.
    executor = Executor(execution, [adapters["codex"]])
    held = executor.step()[0]

    assert held.id == scribe.id
    # Nothing here can start it, and a missing configuration is not a reason to throw
    # the resident's work away: it is still waiting to start, which is the truth about
    # it, and it was launched nowhere else.
    assert held.status == "starting" and not held.launch_attempted
    assert held.finished_at is None
    assert not (tmp_path / "data/fake-claude-runtime" / scribe.id).exists()
    assert not (tmp_path / "data/fake-runtime" / scribe.id).exists()
    # Waiting is not retrying, and a second pass says nothing new about it.
    assert executor.step()[0].status == "starting"
    with hearth.database.transaction() as db:
        assert db.execute("SELECT COUNT(*) FROM pauses").fetchone()[0] == 0
        assert (
            db.execute("SELECT COUNT(*) FROM audit WHERE resource_id=?", (scribe.id,)).fetchone()[0]
            == 1
        )  # its admission, and nothing repeated per pass

    # Configure the runtime again and the same run is worked, on its own pin.
    assert Executor(execution, adapters.values()).step()[0].status == "succeeded"
    assert hearth.run(scribe.id).actual_cost == CLAUDE_ANSWER_COST


def test_the_operator_can_end_a_waiting_run_that_was_never_launched(tmp_path):
    """Nothing ran, and the registry can prove it from the store's own pins."""
    hearth, execution, adapters = household(tmp_path)
    scribe = admit(hearth, "scribe", "scribe-task")
    executor = Executor(execution, [adapters["codex"]])
    assert executor.step()[0].status == "starting"

    execution.cancel(scribe.id)
    settled = executor.step()[0]
    assert settled.status == "cancelled"
    assert settled.actual_cost == 0 and settled.usage_known
    assert not (tmp_path / "data/fake-claude-runtime" / scribe.id).exists()
    # Nothing was spent, so nothing is paused and the resident may work again.
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
    # A cancellation asked for while it waits is remembered rather than flattened, and
    # it still settles nothing: this run may really have spent money.
    execution.cancel(scribe.id)
    assert executor.step()[0].status == "stopping"
    assert hearth.run(scribe.id).finished_at is None

    # Configure the runtime again and the run ends where it really is.
    settled = Executor(execution, adapters.values()).step()[0]
    # A killed session proves no turn, so it settles cancelled with usage unknown.
    assert settled.status == "cancelled" and not settled.usage_known


def test_the_store_default_must_be_a_runtime_this_instance_is_configured_for(tmp_path):
    hearth, execution, adapters = household(tmp_path)
    with pytest.raises(Refused, match="runtime_store_mismatch"):
        Executor(execution, [adapters["claude"]]).step()


def test_a_declaration_may_only_move_to_a_runtime_this_release_ships(tmp_path):
    hearth, _, _ = household(tmp_path)
    for kind in ("inline_mock", "codex_mock", "shell", "", "x" * 101):
        with pytest.raises(Refused, match="runtime_not_configured"):
            hearth.save_resident(
                "karen",
                Declaration("Karen", "Runs the household", 10_000_000, runtime=kind),
                expected_revision=1,
            )
    # Both live kinds are declarable, and no runtime at all is the store's default.
    for revision, kind in enumerate((CODEX_KIND, CLAUDE_KIND, None), start=1):
        hearth.save_resident(
            "karen",
            Declaration("Karen", "Runs the household", 10_000_000, runtime=kind),
            expected_revision=revision,
        )


def test_a_resident_keeps_a_runtime_this_release_has_since_retired(tmp_path):
    """Losing the runtime must not lose the resident: what stands may always be kept."""
    hearth, _, _ = household(tmp_path)
    with hearth.database.transaction(write=True) as db:
        db.execute("UPDATE declarations SET runtime='inline_mock' WHERE resident_id='scribe'")
    standing = hearth.resident("scribe").declaration
    assert standing.runtime == "inline_mock"
    # Renaming, repurposing or pausing it carries the retired kind forward untouched.
    saved = hearth.save_resident(
        "scribe", replace(standing, purpose="Writes rather more"), expected_revision=1
    )
    assert saved.declaration.runtime == "inline_mock"
    # Only a move is refused, and moving to a live one is the way out.
    with pytest.raises(Refused, match="runtime_not_configured"):
        hearth.save_resident("scribe", replace(standing, runtime="codex_mock"), expected_revision=2)
    assert (
        hearth.save_resident(
            "scribe", replace(standing, runtime=CLAUDE_KIND), expected_revision=2
        ).declaration.runtime
        == CLAUDE_KIND
    )


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


def test_the_operator_moves_one_resident_between_brains_and_only_when_it_says_so(tmp_path):
    """The declaration route never moves a resident it was not asked to move."""
    from fastapi.testclient import TestClient
    from hearth.app import create_app

    from tests.fake_runtime import fake_runtimes

    token = "synthetic-operator-token-for-tests"
    headers = {"Authorization": "Bearer " + token}
    app = create_app(tmp_path, token, supervise=False, runtime=fake_runtimes())
    hearth = app.state.hearth
    hearth.save_resident("scribe", Declaration("Scribe", "Writes", 10_000_000), expected_revision=0)
    with TestClient(app) as client:
        saved = client.get("/api/residents/scribe", headers=headers).json()
        assert saved["declaration"]["runtime"] is None
        body = saved["declaration"] | {"expected_revision": saved["revision"]}

        # Moved because the body says so.
        moved = client.put(
            "/api/residents/scribe", headers=headers, json=body | {"runtime": CLAUDE_KIND}
        ).json()
        assert moved["declaration"]["runtime"] == CLAUDE_KIND

        # A save that never mentions the runtime keeps it.
        whole = {key: value for key, value in body.items() if key != "runtime"}
        kept = client.put(
            "/api/residents/scribe",
            headers=headers,
            json=whole | {"purpose": "Writes more", "expected_revision": moved["revision"]},
        ).json()
        assert kept["declaration"]["runtime"] == CLAUDE_KIND
        door = client.put(
            "/api/residents/scribe",
            headers=headers,
            json={"letters_accept": True, "expected_revision": kept["revision"]},
        ).json()
        assert door["declaration"]["runtime"] == CLAUDE_KIND
        assert door["declaration"]["letters_accept"] is True

        # And an explicit null is the way back to the store's default.
        back = client.put(
            "/api/residents/scribe",
            headers=headers,
            json={"runtime": None, "expected_revision": door["revision"]},
        ).json()
        assert back["declaration"]["runtime"] is None
        # A runtime nobody ships is refused rather than stored.
        refused = client.put(
            "/api/residents/scribe",
            headers=headers,
            json={"runtime": "inline_mock", "expected_revision": back["revision"]},
        )
        assert refused.status_code == 409 and refused.json()["error"] == "runtime_not_configured"


def test_a_second_provider_that_will_not_open_does_not_take_the_household_down(tmp_path):
    """A lapsed login on one brain must not stop every resident on the other."""
    from fastapi.testclient import TestClient
    from hearth.app import create_app
    from hearth.integrations.claude.config import VERSION

    from tests.integrations.claude.test_configuration import synthetic_cli, synthetic_codex

    data = tmp_path / "data"
    database = Database(data / "hearth.db")
    database.initialize()
    codex_binary, auth = synthetic_codex(tmp_path)
    config_dir = tmp_path / "claude-config"
    config_dir.mkdir()
    # The store has a resident on Claude, and this host's Claude CLI is logged out.
    FakeClaudeRuntime(data)
    Hearth(database).save_resident(
        "scribe",
        Declaration("Scribe", "Writes on the second brain", 10_000_000, runtime=CLAUDE_KIND),
        expected_revision=0,
    )
    app = create_app(
        data,
        "synthetic-operator-token-for-tests",
        supervise=False,
        codex_binary=codex_binary,
        codex_auth_home=auth,
        claude_binary=synthetic_cli(tmp_path / "logged-out-claude", logged_in=False),
        claude_config_dir=config_dir,
    )
    # The household opens on the runtime it can, and says why the other is missing.
    assert set(app.state.executor.runtimes) == {CODEX_KIND}
    with database.transaction() as db:
        recorded = db.execute(
            "SELECT resource_id, detail FROM audit WHERE kind='runtime.unavailable'"
        ).fetchall()
    assert [row[0] for row in recorded] == [CLAUDE_KIND]
    assert '"reason": "claude_subscription_login_required"' in recorded[0][1]
    opened = [{"kind": CODEX_KIND, "label": "Codex subscription", "default": True}]
    with TestClient(app) as client:
        # Liveness says which brains work is handed to, and nothing about the machine
        # this instance was started on: no path, and no reason a provider refused.
        assert client.get("/health").json() == {"service": "hearth", "runtimes": opened}
        # An operator whose scribe is waiting reads why behind their own token: the
        # runtime opened nowhere, and the provider's refusal says what to fix.
        health = client.get(
            "/api/health",
            headers={"Authorization": "Bearer synthetic-operator-token-for-tests"},
        ).json()
        assert health["runtimes"] == opened
        assert health["unavailable"] == [
            {"kind": CLAUDE_KIND, "reason": "claude_subscription_login_required"}
        ]
    assert VERSION  # the pinned version is what the synthetic CLI answered with


def test_a_runtime_nothing_here_is_configured_for_is_recorded_at_start(tmp_path):
    """No refusal to report, because nothing was even pointed at it."""
    from hearth.app import create_app

    from tests.integrations.claude.test_configuration import synthetic_codex

    data = tmp_path / "data"
    database = Database(data / "hearth.db")
    database.initialize()
    FakeClaudeRuntime(data)
    Hearth(database).save_resident(
        "scribe",
        Declaration("Scribe", "Writes on the second brain", 10_000_000, runtime=CLAUDE_KIND),
        expected_revision=0,
    )
    codex_binary, auth = synthetic_codex(tmp_path)
    app = create_app(
        data,
        "synthetic-operator-token-for-tests",
        supervise=False,
        codex_binary=codex_binary,
        codex_auth_home=auth,
    )
    assert set(app.state.executor.runtimes) == {CODEX_KIND}
    with database.transaction() as db:
        detail = db.execute(
            "SELECT detail FROM audit WHERE kind='runtime.unavailable' AND resource_id=?",
            (CLAUDE_KIND,),
        ).fetchone()[0]
    assert '"reason": "runtime_not_configured"' in detail
