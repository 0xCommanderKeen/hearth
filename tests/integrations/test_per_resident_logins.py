"""Two residents, one runtime kind, two subscriptions -- and what happens when one lapses.

The whole point of a resident's own login is that the household's money and its money
are different money, so these tests drive the real Codex adapter and its real detached
worker with a fake CLI that reports the `CODEX_HOME` it was actually given. Nothing is
a credential: the login files say `synthetic-only` and no provider is reached
(`docs/adr/0016-sandbox-per-run.md`).
"""

import json
import sys
from pathlib import Path

from hearth.execution.context import read_context
from hearth.execution.lifecycle import Execution, Executor
from hearth.integrations.codex.subscription import KIND, CodexLiveRuntime
from hearth.integrations.codex.subscription import worker as codex_worker
from hearth.integrations.logins import LOGIN_REQUIRED, Logins
from hearth.residents.memory import Memory
from hearth.residents.models import Declaration, Refused
from hearth.storage.artifacts import Artifacts
from hearth.storage.database import Database
from hearth.work.service import Hearth

from tests.fake_runtime import FakeRuntime
from tests.integrations.test_launcher_parity import CODEX_EVENTS

NOW = 1_788_640_000


def reporting_cli(path: Path) -> Path:
    """A `codex` that answers the version probe and reports the login it was handed.

    The final message is the session's own view of `CODEX_HOME`, which is the only way
    to prove from outside which subscription a run would really have spent.
    """
    path.write_text(
        f"#!{sys.executable}\n"
        "import json, os, sys\n"
        "if '--version' in sys.argv:\n print('codex-cli 0.153.4')\n sys.exit()\n"
        "assert sys.stdin.read(), 'the session was launched with no prompt'\n"
        "final = sys.argv[sys.argv.index('-o') + 1]\n"
        "answer = os.environ['CODEX_HOME']\n"
        f"for event in {json.dumps(CODEX_EVENTS)}:\n"
        "    if event.get('item', {}).get('type') == 'agent_message':\n"
        "        event['item']['text'] = answer\n"
        "    sys.stdout.write(json.dumps(event) + '\\n')\n"
        "    sys.stdout.flush()\n"
        "open(final, 'w').write(answer)\n"
    )
    path.chmod(0o700)
    return path


def household(tmp_path):
    """One store, one household login, and the real adapter over a fake CLI."""
    data = tmp_path / "data"
    shared = tmp_path / "household-login"
    shared.mkdir(parents=True)
    (shared / "auth.json").write_text("synthetic-only")
    database = Database(data / "hearth.db")
    database.initialize()
    runtime = CodexLiveRuntime(data, binary=reporting_cli(tmp_path / "codex"), auth_home=shared)
    return Hearth(database, clock=lambda: NOW), runtime, data, shared


def seed_login(data: Path, resident_id: str) -> Path:
    """What an operator does by hand: a private directory with that CLI's own login."""
    directory = data / "credentials" / resident_id / KIND
    directory.mkdir(parents=True)
    (directory / "auth.json").write_text("synthetic-only")
    return directory


def resident(hearth, resident_id: str) -> None:
    hearth.save_resident(
        resident_id,
        Declaration(resident_id.title(), "Synthetic notes", 10_000_000),
        expected_revision=0,
    )


def run_through_the_worker(hearth, runtime, resident_id: str, key: str = "one"):
    """Admit one task for that resident and let its real worker run the session."""
    from unittest.mock import patch

    task = hearth.submit(
        f"task-{resident_id}-{key}", resident_id, "Summarize", expires_at=NOW + 600
    )
    run = hearth.admit(task.task_id, reserve=100_000)
    execution = Execution(hearth, Artifacts(runtime.data / "artifacts"))
    execution.prepare_start(run.id, run.owner_token)
    with hearth.database.transaction() as db:
        prompt = json.dumps(
            read_context(db, run.id, Memory(hearth).files), sort_keys=True, separators=(",", ":")
        )
    with patch("hearth.integrations.codex.subscription.subprocess.Popen", lambda *a, **k: None):
        runtime.start(run.id, prompt)
    codex_worker(runtime.folder(run.id))
    receipt = runtime.receipt(run.id)
    # Settled through the ordinary path, so the resident is free for the next task and
    # the receipt this returns is one that really settled money.
    execution.finish(
        run.id,
        run.owner_token,
        runtime.inspect(run.id, expected_digest=run.input_digest),
        _usage_receipt=receipt,
    )
    return run, receipt


def test_two_residents_of_one_household_run_on_two_subscriptions(tmp_path):
    """The acceptance of #188: one kind, two logins, and each receipt says which."""
    hearth, runtime, data, shared = household(tmp_path)
    resident(hearth, "reader")
    resident(hearth, "karen")
    private = seed_login(data, "karen")

    shared_run, shared_receipt = run_through_the_worker(hearth, runtime, "reader")
    own_run, own_receipt = run_through_the_worker(hearth, runtime, "karen")

    # What Hearth admitted, on the run itself.
    assert hearth.run(shared_run.id).login_scope == "household"
    assert hearth.run(own_run.id).login_scope == "resident"
    # What the worker says it spent, in the receipt beside the CLI's own stream.
    assert shared_receipt["login_scope"] == "household"
    assert own_receipt["login_scope"] == "resident"
    # And what the session was really handed, said by the session itself.
    assert shared_receipt["final"] == str(shared)
    assert own_receipt["final"] == str(private)
    assert own_receipt["final"] != shared_receipt["final"]


def test_a_login_seeded_after_admission_belongs_to_the_next_run(tmp_path):
    """The pin is read once. A run already admitted keeps the login it was admitted to."""
    hearth, runtime, data, shared = household(tmp_path)
    resident(hearth, "karen")
    first, before = run_through_the_worker(hearth, runtime, "karen", key="before")
    assert before["login_scope"] == "household" and before["final"] == str(shared)
    private = seed_login(data, "karen")
    _, after = run_through_the_worker(hearth, runtime, "karen", key="after")
    # The next run is on the resident's own login, and the finished one still says the
    # household's -- which is where its money really went.
    assert after["login_scope"] == "resident" and after["final"] == str(private)
    assert hearth.run(first.id).login_scope == "household"


def test_the_admission_says_whose_login_it_pinned(tmp_path):
    hearth, _, data, _ = household(tmp_path)
    resident(hearth, "karen")
    seed_login(data, "karen")
    task = hearth.submit("task-1", "karen", "Summarize", expires_at=NOW + 600)
    hearth.admit(task.task_id, reserve=100_000)
    admitted = [fact for fact in hearth.audit() if fact["kind"] == "run.admitted"]
    assert admitted[0]["detail"]["login_scope"] == "resident"


# -- the other provider ---------------------------------------------------


def test_the_other_runtime_reads_its_login_off_the_run_in_the_same_words(tmp_path):
    """Whatever a login *is* differs between providers; which one a run spends does not."""
    from unittest.mock import patch

    from hearth.integrations.claude.config import KIND as CLAUDE_KIND
    from hearth.integrations.claude.subscription import ClaudeLiveRuntime
    from hearth.integrations.claude.subscription import worker as claude_worker
    from hearth.integrations.durable import read

    from tests.integrations.claude.test_claude_live import fake_cli, login

    data = tmp_path / "data"
    shared = login(tmp_path / "private-claude-config")
    database = Database(data / "hearth.db")
    database.initialize()
    with database.transaction(write=True) as db:
        db.execute("UPDATE system_meta SET value=? WHERE key='runtime_kind'", (CLAUDE_KIND,))
    runtime = ClaudeLiveRuntime(data, binary=fake_cli(tmp_path / "claude"), config_dir=shared)
    hearth = Hearth(database, clock=lambda: NOW)
    resident(hearth, "karen")
    (data / "credentials" / "karen").mkdir(parents=True)
    private = login(data / "credentials" / "karen" / CLAUDE_KIND)
    task = hearth.submit("task-1", "karen", "Summarize", expires_at=NOW + 600)
    run = hearth.admit(task.task_id, reserve=100_000)
    Execution(hearth, Artifacts(data / "artifacts")).prepare_start(run.id, run.owner_token)
    with database.transaction() as db:
        prompt = json.dumps(
            read_context(db, run.id, Memory(hearth).files), sort_keys=True, separators=(",", ":")
        )
    with patch("hearth.integrations.claude.subscription.subprocess.Popen", lambda *a, **k: None):
        runtime.start(run.id, prompt)
    # The configuration directory the worker will hand the session is the resident's.
    assert read(runtime.folder(run.id) / "request.json")["config_dir"] == str(private)
    claude_worker(runtime.folder(run.id))
    assert runtime.receipt(run.id)["login_scope"] == "resident"


# -- what the operator's own health answer says ---------------------------


def test_a_lapsed_resident_login_is_named_at_start_and_on_the_health_answer(tmp_path):
    from fastapi.testclient import TestClient
    from hearth.app import create_app

    from tests.integrations.claude.test_configuration import synthetic_codex

    data = tmp_path / "data"
    Database(data / "hearth.db").initialize()
    binary, auth = synthetic_codex(tmp_path)
    # An operator made the directory and the login flow never finished: seeded, and
    # empty. Nothing here is a credential.
    (data / "credentials" / "karen" / KIND).mkdir(parents=True)
    (data / "credentials" / "reader" / KIND).mkdir(parents=True)
    (data / "credentials" / "reader" / KIND / "auth.json").write_text("synthetic-only")
    app = create_app(
        data,
        "synthetic-operator-token-for-tests",
        supervise=False,
        codex_binary=binary,
        codex_auth_home=auth,
    )
    lapsed = [{"resident_id": "karen", "kind": KIND}]
    # Recorded once, where it is discovered, so the reason lives in the store and not
    # only in somebody's terminal.
    with Database(data / "hearth.db").transaction() as db:
        recorded = db.execute(
            "SELECT resource_id, detail FROM audit WHERE kind='login.resident_lapsed'"
        ).fetchall()
    assert [row[0] for row in recorded] == ["karen"]
    assert '"kind": "codex_subscription"' in recorded[0][1]
    with TestClient(app) as client:
        health = client.get(
            "/api/health",
            headers={"Authorization": "Bearer synthetic-operator-token-for-tests"},
        ).json()
        # The one that works is not named; the one that does not is.
        assert health["login"] == {"resident_lapsed": lapsed, "resident_unknown": []}
        # Nothing about a login reaches the open liveness path.
        assert "login" not in client.get("/health").json()
        # And it is asked afresh: the operator finishes the login and asks again.
        (data / "credentials" / "karen" / KIND / "auth.json").write_text("synthetic-only")
        health = client.get(
            "/api/health",
            headers={"Authorization": "Bearer synthetic-operator-token-for-tests"},
        ).json()
        assert health["login"] == {"resident_lapsed": [], "resident_unknown": []}


# -- what the operator's own command says ---------------------------------


def test_the_credentials_command_lists_the_logins_and_says_only_whether_they_work(
    tmp_path, monkeypatch
):
    from hearth.__main__ import credentials
    from hearth.integrations.claude.config import KIND as CLAUDE_KIND

    monkeypatch.delenv("HEARTH_CLAUDE_BINARY", raising=False)
    seed_login(tmp_path, "karen")
    (tmp_path / "credentials" / "reader" / KIND).mkdir(parents=True)
    (tmp_path / "credentials" / "karen" / CLAUDE_KIND).mkdir(parents=True)

    listed = credentials(tmp_path)
    assert [(row["resident_id"], row["kind"], row["logged_in"]) for row in listed] == [
        # Karen's Codex login is there, and her Claude one cannot be asked about here:
        # no server is running, so nothing can start that CLI.
        ("karen", KIND, True),
        ("karen", CLAUDE_KIND, None),
        # A directory with no credential in it is a login that does not work, which is
        # exactly what holds this resident's Codex runs.
        ("reader", KIND, False),
    ]
    # Three facts and a path. Nothing of the provider's own answer -- no account, no
    # plan, no organisation -- and nothing of the credential.
    assert all(set(row) == {"resident_id", "kind", "path", "logged_in"} for row in listed)


def test_the_credentials_command_says_nothing_about_a_household_without_own_logins(tmp_path):
    from hearth.__main__ import credentials

    assert credentials(tmp_path) == []


# -- a login that has lapsed ----------------------------------------------


def held_household(tmp_path, lapsed: set[str]):
    """A store on the fake runtime, whose probe says those residents' logins are out."""
    database = Database(tmp_path / "hearth.db")
    database.initialize()
    hearth = Hearth(database, clock=lambda: NOW)
    for resident_id in ("reader", "karen"):
        with database.transaction() as db:
            known = db.execute("SELECT 1 FROM residents WHERE id=?", (resident_id,)).fetchone()
        if not known:
            resident(hearth, resident_id)
    execution = Execution(hearth, Artifacts(tmp_path / "artifacts"))
    logins = Logins(
        tmp_path, {KIND: lambda directory: directory.parent.name not in lapsed}, refresh=0
    )
    return hearth, Executor(execution, FakeRuntime(tmp_path), logins)


def submit(hearth, resident_id: str, key: str = "one"):
    task = hearth.submit(
        f"task-{resident_id}-{key}", resident_id, "Summarize", expires_at=NOW + 600
    )
    return hearth.admit(task.task_id, reserve=5_000)


def test_a_lapsed_resident_login_holds_that_resident_s_runs_and_nothing_else(tmp_path):
    seed_login(tmp_path, "karen")
    hearth, executor = held_household(tmp_path, lapsed={"karen"})
    held = submit(hearth, "karen")
    working = submit(hearth, "reader")

    executor.step()
    # The held run was never launched -- no money, no session, no receipt -- and it is
    # still waiting to start, which is the truth about it.
    assert hearth.run(held.id).status == "starting"
    assert hearth.run(held.id).launch_attempted == 0
    assert hearth.run(held.id).finished_at is None
    # Its housemate on the household's login is worked by the very same pass.
    assert hearth.run(working.id).status == "succeeded"
    # And it is not thrown away: the moment the operator fixes the login, the very run
    # that was waiting is the one that runs.
    hearth, executor = held_household(tmp_path, lapsed=set())
    executor.step()
    assert hearth.run(held.id).status == "succeeded"


def test_a_resident_login_that_is_taken_away_holds_the_run_it_was_admitted_to(tmp_path):
    directory = seed_login(tmp_path, "karen")
    hearth, executor = held_household(tmp_path, lapsed=set())
    held = submit(hearth, "karen")
    (directory / "auth.json").unlink()
    directory.rmdir()
    executor.step()
    assert hearth.run(held.id).status == "starting"
    assert hearth.run(held.id).launch_attempted == 0


def test_a_household_run_is_never_held_by_a_resident_login_that_is_out(tmp_path):
    """A resident with no login of its own is not the resident whose login lapsed."""
    seed_login(tmp_path, "karen")
    hearth, executor = held_household(tmp_path, lapsed={"karen", "reader"})
    working = submit(hearth, "reader")
    executor.step()
    assert hearth.run(working.id).status == "succeeded"


def test_a_held_run_says_why_it_is_waiting_once_and_not_twice_a_second(tmp_path):
    """A run sitting at `starting` for a reason nobody wrote down is unreadable."""
    seed_login(tmp_path, "karen")
    hearth, executor = held_household(tmp_path, lapsed={"karen"})
    held = submit(hearth, "karen")
    executor.step()
    executor.step()
    executor.step()
    waiting = [fact for fact in hearth.audit() if fact["kind"] == "run.waiting"]
    assert len(waiting) == 1
    assert waiting[0]["resource_id"] == held.id
    assert waiting[0]["detail"]["reason"] == "login_required"
    assert waiting[0]["detail"]["resident_id"] == "karen"


def test_a_login_taken_away_between_the_gate_and_the_launch_never_ends_the_pass(tmp_path):
    """The narrow race: the login was there when the run was gated and is gone now.

    The launch intent is already recorded, so this run cannot be held any longer -- but
    one resident's race must not stop every other resident's step.
    """
    directory = seed_login(tmp_path, "karen")
    hearth, executor = held_household(tmp_path, lapsed=set())
    raced = submit(hearth, "karen")
    working = submit(hearth, "reader")
    runtime = executor.runtimes[KIND]
    started = runtime.start

    def vanishing(run_id, instruction):
        if run_id == raced.id:
            (directory / "auth.json").unlink()
            directory.rmdir()
            # Exactly what a real adapter's `start` propagates from `directory_for`,
            # asserted against the real one below.
            raise Refused(LOGIN_REQUIRED)
        return started(run_id, instruction)

    runtime.start = vanishing
    executor.step()
    # The raced run is visibly interrupted with nothing spent, and its housemate ran.
    assert hearth.run(raced.id).status == "interrupted"
    assert hearth.run(working.id).status == "succeeded"


def test_a_real_adapter_refuses_a_vanished_login_by_the_name_the_executor_reads(tmp_path):
    """The one word every provider spells the same, so the executor can read it."""
    import pytest

    hearth, runtime, data, _ = household(tmp_path)
    resident(hearth, "karen")
    directory = seed_login(data, "karen")
    task = hearth.submit("task-1", "karen", "Summarize", expires_at=NOW + 600)
    run = hearth.admit(task.task_id, reserve=100_000)
    Execution(hearth, Artifacts(data / "artifacts")).prepare_start(run.id, run.owner_token)
    with hearth.database.transaction() as db:
        prompt = json.dumps(
            read_context(db, run.id, Memory(hearth).files), sort_keys=True, separators=(",", ":")
        )
    (directory / "auth.json").unlink()
    directory.rmdir()
    with pytest.raises(Refused) as error:
        runtime.start(run.id, prompt)
    assert error.value.code == LOGIN_REQUIRED
    # And nothing was published for a session that was never started.
    assert not runtime.folder(run.id).exists()


def test_the_credentials_command_asks_the_way_the_sessions_will_be_asked(tmp_path, monkeypatch):
    """On a burrow that sandboxes its runs, a Keychain-backed login is not a login.

    The CLI on this host would answer `loggedIn: true` for that directory and every run
    on it would still be held, so the command has to ask the question the sandbox asks.
    """
    import sys

    from hearth.__main__ import credentials
    from hearth.integrations.claude.config import KIND as CLAUDE_KIND

    binary = tmp_path / "claude"
    binary.write_text(
        f"#!{sys.executable}\nimport json, sys\n"
        "print(json.dumps({'loggedIn': True, 'authMethod': 'claude.ai'}))\n"
    )
    binary.chmod(0o700)
    monkeypatch.setenv("HEARTH_CLAUDE_BINARY", str(binary))
    # A configuration directory with no credential file in it: a Keychain login.
    (tmp_path / "credentials" / "karen" / CLAUDE_KIND).mkdir(parents=True)

    monkeypatch.delenv("HEARTH_SANDBOX", raising=False)
    assert credentials(tmp_path)[0]["logged_in"] is True
    monkeypatch.setenv("HEARTH_SANDBOX", "container")
    assert credentials(tmp_path)[0]["logged_in"] is False
