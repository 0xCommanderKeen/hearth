"""The headless run end to end: launch once, settle from the stream, cancel for real.

The real CLI is never reached. A fake `claude` executable replays one of the recorded
fixtures from `fixtures/`, line by line with a pause between lines, so the cancellation
and timeout paths run against a live process group rather than a mock.
"""

import hashlib
import json
import sys
import time
from dataclasses import asdict
from pathlib import Path

import pytest
from hearth.integrations.claude.config import KIND, VERSION, budget, session_command
from hearth.integrations.claude.pricing import MODEL, PRICE_SCHEDULE
from hearth.integrations.claude.subscription import ClaudeLiveRuntime, encode, worker
from hearth.integrations.codex.usage import UsageBinding
from hearth.residents.models import Declaration, Refused

FIXTURES = Path(__file__).parent / "fixtures"
BINDING = UsageBinding(
    "real-run", hashlib.sha256(b"notes").hexdigest(), MODEL, "standard", PRICE_SCHEDULE
)


def stream(name: str) -> str:
    return (FIXTURES / f"{name}.jsonl").read_text()


def receipt(name="success", **changes) -> dict:
    return {
        "kind": KIND,
        "binding": asdict(BINDING),
        "binary": "a" * 64,
        "stdout": stream(name),
        "exit_code": 0 if name == "success" else 1,
        "cancelled": False,
        "launched": True,
    } | changes


def fake_cli(path: Path, *, fixture="success", pause=0.0, version=VERSION) -> Path:
    """A `claude` that answers the probes and then replays one recorded session."""
    path.write_text(
        f"#!{sys.executable}\n"
        "import json, sys, time\n"
        f"if '--version' in sys.argv:\n print({version!r})\n sys.exit()\n"
        "if sys.argv[1:3] == ['auth', 'status']:\n"
        " print(json.dumps({'loggedIn': True, 'authMethod': 'claude.ai'}))\n sys.exit()\n"
        # The worker delivers the prompt on stdin; a replay that never read it would
        # not exercise the same launch shape.
        "prompt = sys.stdin.read()\n"
        "assert prompt, 'the session was launched with no prompt'\n"
        f"argv = json.dumps(sys.argv[1:])\n"
        f"open({str(path.parent / 'argv.json')!r}, 'w').write(argv)\n"
        f"for line in open({str(FIXTURES / (fixture + '.jsonl'))!r}):\n"
        "    sys.stdout.write(line)\n"
        "    sys.stdout.flush()\n"
        f"    time.sleep({pause})\n"
        f"sys.exit({0 if fixture == 'success' else 1})\n"
    )
    path.chmod(0o700)
    return path


def prepared(tmp_path, *, fixture="success", pause=0.0, reserve=100_000, detach=False):
    """A store with one admitted run, started, its worker left for the test to drive.

    `start` really publishes the request and takes the folder's lock; only the
    detached `Popen` is held back, so the test can run the worker in-process and watch
    it. `detach=True` lets the real worker process go, for the observation paths.
    """
    from unittest.mock import patch

    from hearth.execution.context import read_context
    from hearth.execution.lifecycle import Execution
    from hearth.residents.memory import Memory
    from hearth.storage.artifacts import Artifacts
    from hearth.storage.database import Database
    from hearth.work.service import Hearth

    data = tmp_path / "data"
    config_dir = tmp_path / "private-claude-config"
    config_dir.mkdir()
    binary = fake_cli(tmp_path / "claude", fixture=fixture, pause=pause)
    database = Database(data / "hearth.db")
    database.initialize()
    with database.transaction(write=True) as db:
        db.execute("UPDATE system_meta SET value=? WHERE key='runtime_kind'", (KIND,))
    runtime = ClaudeLiveRuntime(data, binary=binary, config_dir=config_dir)
    hearth = Hearth(database)
    hearth.save_resident(
        "reader", Declaration("Reader", "Synthetic notes", 10_000_000), expected_revision=0
    )
    task = hearth.submit("live", "reader", "Summarize", expires_at=int(hearth.clock()) + 600)
    run = hearth.admit(task.task_id, reserve=reserve)
    execution = Execution(hearth, Artifacts(data / "artifacts"))
    execution.prepare_start(run.id, run.owner_token)
    with database.transaction() as db:
        prompt = json.dumps(
            read_context(db, run.id, Memory(hearth).files), sort_keys=True, separators=(",", ":")
        )
    if detach:
        runtime.start(run.id, prompt)
    else:
        with patch(
            "hearth.integrations.claude.subscription.subprocess.Popen", lambda *a, **k: None
        ):
            runtime.start(run.id, prompt)
    return runtime, hearth, run, prompt


def test_a_recorded_session_settles_with_the_answer_and_the_price_it_reported():
    _, _, evidence = encode(receipt(), BINDING)
    assert evidence.status == "succeeded"
    assert evidence.output == "pong"
    assert evidence.cost == 36_580


def test_the_receipt_that_commits_is_the_serialized_copy_that_was_validated():
    raw, digest, _ = encode(receipt(), BINDING)
    assert json.loads(raw) == receipt()
    assert digest == hashlib.sha256(raw.encode()).hexdigest()


def test_a_budget_stop_settles_as_failed_with_the_cost_the_cli_already_billed():
    evidence = encode(receipt("budget-stop"), BINDING)[2]
    assert evidence.status == "failed"
    assert evidence.cost == 38_051
    # And the receipt itself records that the CLI stopped on its own fence.
    from hearth.integrations.claude.subscription import transcript_of

    transcript = transcript_of(receipt("budget-stop"))
    assert transcript.budget_exhausted and transcript.subtype == "error_max_budget_usd"


def test_a_session_that_never_reached_the_api_fails_without_claiming_a_price():
    evidence = encode(receipt("not-logged-in"), BINDING)[2]
    assert evidence.status == "failed" and evidence.cost is None


def test_a_receipt_cannot_settle_another_run_or_another_price_schedule():
    for change in (
        {"input_digest": "b" * 64},
        {"schedule": "gpt-6-astra-api-equivalent-2026-09-06"},
    ):
        value = receipt()
        value["binding"] = asdict(BINDING) | change
        with pytest.raises(Refused, match="run_usage_invalid"):
            encode(value, BINDING)


@pytest.mark.parametrize(
    "change",
    [
        {"kind": "codex_subscription"},
        {"stdout": 1},
        {"exit_code": "0"},
        {"cancelled": "yes"},
        {"launched": 1},
    ],
)
def test_a_receipt_that_is_not_this_runtime_s_evidence_is_refused(change):
    with pytest.raises(Refused, match="run_usage_invalid"):
        encode(receipt(**change), BINDING)


def test_a_missing_or_extra_receipt_field_is_refused():
    short = receipt()
    del short["cancelled"]
    with pytest.raises(Refused, match="run_usage_invalid"):
        encode(short, BINDING)
    with pytest.raises(Refused, match="run_usage_invalid"):
        encode(receipt(protocol="management"), BINDING)


def test_cancel_before_launch_is_the_only_free_ending_and_cannot_hide_output():
    value = receipt(stdout="", exit_code=None, cancelled=True, launched=False)
    evidence = encode(value, BINDING)[2]
    assert evidence.status == "cancelled" and evidence.cost == 0
    # A receipt claiming no launch while carrying the CLI's own stream is refused.
    for claim in ({"stdout": stream("success")}, {"exit_code": 0}, {"cancelled": False}):
        with pytest.raises(Refused, match="run_usage_invalid"):
            encode(value | claim, BINDING)


@pytest.mark.parametrize("cancelled", [True, False])
def test_a_stream_cut_mid_session_keeps_the_observed_ending_with_unknown_usage(cancelled):
    partial = stream("success").splitlines()[0] + '\n{"type":"assist'
    value = receipt(stdout=partial, exit_code=-15, cancelled=cancelled)
    evidence = encode(value, BINDING)[2]
    assert evidence.status == ("cancelled" if cancelled else "failed")
    assert evidence.cost is None


def test_a_cost_the_cli_disagrees_with_leaves_usage_unknown_and_keeps_the_answer():
    events = [json.loads(line) for line in stream("success").splitlines()]
    events[-1]["total_cost_usd"] = 9.99
    value = receipt(stdout="\n".join(json.dumps(event) for event in events) + "\n")
    evidence = encode(value, BINDING)[2]
    assert evidence.status == "succeeded" and evidence.output == "pong"
    assert evidence.cost is None


def test_the_argv_a_run_is_launched_with_is_bounded_and_names_the_pinned_model():
    command = session_command(Path("/pinned/claude"), budget_usd="0.100000")
    assert command[0] == "/pinned/claude"
    for flag in ("--print", "--setting-sources", "--strict-mcp-config", "--no-session-persistence"):
        assert flag in command
    assert command[command.index("--model") + 1] == MODEL
    assert command[command.index("--max-budget-usd") + 1] == "0.100000"
    # No tool exists in a run until the bridge grants one (#147).
    assert command[command.index("--tools") + 1] == ""
    # `--bare` cannot run under a subscription at all; the bound is the settings flag.
    assert "--bare" not in command
    assert command[command.index("--setting-sources") + 1] == ""


def test_the_provider_fence_is_the_money_the_run_was_admitted_for():
    assert budget(100_000) == "0.100000"
    assert budget(1) == "0.000001"
    assert budget(0) == "0.000000"
    for invalid in (-1, 1.5, True):
        with pytest.raises(ValueError):
            budget(invalid)


def test_a_run_launches_once_and_a_second_start_observes_the_same_worker(tmp_path):
    runtime, _, run, prompt = prepared(tmp_path)
    worker(runtime.folder(run.id))
    evidence = runtime.inspect(run.id, expected_digest=run.input_digest)
    assert evidence.status == "succeeded" and evidence.cost == 36_580
    # Starting again neither relaunches nor disturbs the receipt already published.
    before = runtime.receipt(run.id)
    runtime.start(run.id, prompt)
    worker(runtime.folder(run.id))
    assert runtime.receipt(run.id) == before
    # A different instruction for the same run is a conflict, never a second launch.
    with pytest.raises(Refused, match="runtime_identity_conflict"):
        runtime.start(run.id, prompt + " ")


def test_the_launched_session_is_the_bounded_one_fenced_at_the_resident_s_day(tmp_path):
    """The fence is the resident's remaining day, never the admission hold.

    A reservation is a hold, not a cap: every run here reserves a cent while a real
    session costs several, so a fence built from the reservation would have stopped
    every one of them after the first billed request.
    """
    runtime, _, run, prompt = prepared(tmp_path, reserve=10_000)
    worker(runtime.folder(run.id))
    argv = json.loads((tmp_path / "argv.json").read_text())
    assert argv[argv.index("--max-budget-usd") + 1] == "10.000000"  # the daily limit
    assert argv[argv.index("--model") + 1] == MODEL
    assert "--verbose" in argv and argv[argv.index("--output-format") + 1] == "stream-json"


def test_the_fence_is_what_the_resident_may_still_spend_today(tmp_path):
    from hearth.work.service import spend_fence

    runtime, hearth, run, _ = prepared(tmp_path, reserve=250_000)
    with hearth.database.transaction(write=True) as db:
        row = db.execute("SELECT * FROM runs WHERE id=?", (run.id,)).fetchone()
        assert spend_fence(db, row) == 10_000_000
        # A day already half spent leaves half a day of fence, this run's own
        # reservation included: nothing admission allowed is stopped by it.
        db.execute(
            "INSERT INTO runs (id,task_id,resident_id,resident_revision,owner_token,status,"
            "reserved,budget_day,budget_timezone,runtime_kind,runtime_version,created_at,"
            "input_digest,actual_cost,usage_known,launch_attempted) "
            "VALUES ('earlier',?,?,?,'token','succeeded',0,?,?,?,1,?,'digest',5000000,1,1)",
            (
                row["task_id"],
                row["resident_id"],
                row["resident_revision"],
                row["budget_day"],
                row["budget_timezone"],
                row["runtime_kind"],
                row["created_at"],
            ),
        )
        assert spend_fence(db, row) == 5_000_000
        # And a day already overspent still leaves the run what it was admitted for,
        # because that promise was made before anything was launched.
        db.execute("UPDATE runs SET actual_cost=9_990_000 WHERE id='earlier'")
        assert spend_fence(db, row) == 250_000


@pytest.mark.parametrize("field", ["prompt", "sha256"])
def test_the_worker_refuses_a_changed_persisted_launch_input(tmp_path, monkeypatch, field):
    runtime, _, run, prompt = prepared(tmp_path)
    path = runtime.folder(run.id) / "request.json"
    value = json.loads(path.read_text())
    value[field] = "changed"
    path.write_text(json.dumps(value))
    launched = []
    monkeypatch.setattr(
        "hearth.integrations.claude.subscription.subprocess.Popen",
        lambda *a, **kw: launched.append(a),
    )
    worker(runtime.folder(run.id))
    assert launched == []
    assert runtime.inspect(run.id).status == "unknown"


def test_cancellation_signals_the_process_group_and_never_claims_zero_usage(tmp_path):
    import threading

    runtime, _, run, prompt = prepared(tmp_path, pause=5)
    timer = threading.Timer(0.5, runtime.stop, args=(run.id,))
    timer.start()
    try:
        worker(runtime.folder(run.id))
    finally:
        timer.join()
    evidence = runtime.inspect(run.id, expected_digest=run.input_digest)
    assert evidence.status == "cancelled"
    # The session really launched, so the cancellation is never free.
    assert evidence.cost is None
    published = runtime.receipt(run.id)
    assert published["launched"] is True and published["cancelled"] is True
    assert published["exit_code"] is not None


def test_a_worker_that_runs_out_of_time_stops_the_session_it_started(tmp_path, monkeypatch):
    monkeypatch.setattr("hearth.integrations.claude.subscription.RUN_TIMEOUT", 0.2)
    runtime, _, run, prompt = prepared(tmp_path, pause=30)
    started = time.monotonic()
    worker(runtime.folder(run.id))
    assert time.monotonic() - started < 10
    evidence = runtime.inspect(run.id, expected_digest=run.input_digest)
    assert evidence.status == "failed" and evidence.cost is None
    assert runtime.receipt(run.id)["exit_code"] is not None


def test_a_run_still_in_flight_reads_as_running_not_as_absent(tmp_path):
    """The real detached worker holds the folder's lock while its session runs."""
    runtime, _, run, _ = prepared(tmp_path, pause=5, detach=True)
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        if runtime.inspect(run.id, expected_digest=run.input_digest).status == "running":
            break
    else:
        pytest.fail("the detached worker never took the folder's lock")
    # A run whose pinned input is not this one is never observed as this one's.
    assert runtime.inspect(run.id, expected_digest="b" * 64).status == "unknown"
    runtime.stop(run.id)


def test_a_live_result_survives_backup_without_the_login_or_the_stream_leaving_sqlite(tmp_path):
    from hearth.execution.lifecycle import Execution
    from hearth.execution.usage import binding
    from hearth.storage.artifacts import Artifacts
    from hearth.storage.backup import capture, restore, verify
    from hearth.storage.database import Database
    from hearth.work.service import Hearth

    data = tmp_path / "data"
    runtime, hearth, run, _ = prepared(tmp_path)
    worker(runtime.folder(run.id))
    execution = Execution(hearth, Artifacts(data / "artifacts"))
    with hearth.database.transaction() as db:
        row = db.execute("SELECT * FROM runs WHERE id=?", (run.id,)).fetchone()
        bound = binding(db, row)
    published = runtime.receipt(run.id)
    result = execution.finish(
        run.id, run.owner_token, encode(published, bound)[2], _usage_receipt=published
    )
    assert result.actual_cost == 36_580
    metadata, content = execution.artifact(result.artifact_id)
    assert content == "pong"
    capture(data, tmp_path / "backup")
    manifest = verify(tmp_path / "backup")
    assert not any("claude-config" in name or "claude-live" in name for name in manifest["files"])
    restore(tmp_path / "backup", tmp_path / "held")
    held = Database(tmp_path / "held/hearth.db")
    assert held.restored()
    assert Hearth(held).run(run.id).actual_cost == 36_580


def settled(runtime, hearth, run, published, data):
    """Commit one Claude receipt through the ordinary settlement path."""
    from hearth.execution.lifecycle import Execution
    from hearth.execution.usage import binding
    from hearth.storage.artifacts import Artifacts

    execution = Execution(hearth, Artifacts(data / "artifacts"))
    with hearth.database.transaction() as db:
        row = db.execute("SELECT * FROM runs WHERE id=?", (run.id,)).fetchone()
        bound = binding(db, row)
    return execution.finish(
        run.id, run.owner_token, encode(published, bound)[2], _usage_receipt=published
    )


def test_a_settled_run_keeps_the_stream_as_its_receipt_and_the_price_as_its_cost(tmp_path):
    runtime, hearth, run, _ = prepared(tmp_path)
    worker(runtime.folder(run.id))
    result = settled(runtime, hearth, run, runtime.receipt(run.id), tmp_path / "data")
    assert (result.status, result.actual_cost, result.usage_known) == ("succeeded", 36_580, True)
    with hearth.database.transaction() as db:
        stored = db.execute("SELECT receipt FROM run_usage WHERE run_id=?", (run.id,)).fetchone()[0]
        pin = db.execute("SELECT schedule FROM run_pricing WHERE run_id=?", (run.id,)).fetchone()[0]
    # What commits is the CLI's own stream, not a summary of it.
    assert json.loads(stored)["stdout"] == stream("success")
    assert pin == PRICE_SCHEDULE


def test_usage_the_stream_contradicts_keeps_the_answer_and_holds_the_resident(tmp_path):
    runtime, hearth, run, _ = prepared(tmp_path)
    worker(runtime.folder(run.id))
    published = runtime.receipt(run.id)
    events = [json.loads(line) for line in published["stdout"].splitlines()]
    events[-1]["modelUsage"]["claude-opus-5"]["outputTokens"] = 4000
    published["stdout"] = "\n".join(json.dumps(event) for event in events) + "\n"
    result = settled(runtime, hearth, run, published, tmp_path / "data")
    assert result.status == "succeeded" and not result.usage_known
    assert result.actual_cost is None
    # The hold the unknown usage placed stays on the resident.
    task = hearth.submit("next", "reader", "Next", expires_at=int(hearth.clock()) + 600)
    with pytest.raises(Refused, match="resident_paused"):
        hearth.admit(task.task_id, reserve=10_000)


def test_a_stream_too_large_to_commit_still_leaves_a_receipt_to_settle_on(tmp_path, monkeypatch):
    """A run with no receipt could never settle, so the tail is dropped, not the run."""
    from hearth.integrations.claude import subscription

    runtime, _, run, _ = prepared(tmp_path)
    # Small enough that the recorded session's own stream overflows it.
    monkeypatch.setattr(subscription, "MAX_STREAM", 1024)
    worker(runtime.folder(run.id))
    published = runtime.receipt(run.id)
    assert len(json.dumps(published, sort_keys=True, separators=(",", ":"))) <= 1024
    assert published["launched"] is True and published["cancelled"] is False
    evidence = runtime.inspect(run.id, expected_digest=run.input_digest)
    # A transcript nobody can read settles nothing and claims nothing.
    assert evidence.status == "failed" and evidence.cost is None


def test_the_probe_never_lets_the_cli_speak_on_hearth_s_own_error_stream(tmp_path, capfd):
    """`auth status` names the account; none of what it says reaches Hearth."""
    data = tmp_path / "data"
    config_dir = tmp_path / "private-claude-config"
    config_dir.mkdir()
    binary = tmp_path / "claude"
    binary.write_text(
        f"#!{sys.executable}\n"
        "import json, sys\n"
        f"if '--version' in sys.argv:\n print({VERSION!r})\n sys.exit()\n"
        "sys.stderr.write('account: someone@example.invalid, plan: max\\n')\n"
        "print(json.dumps({'loggedIn': True, 'authMethod': 'claude.ai'}))\n"
    )
    binary.chmod(0o700)
    from hearth.storage.database import Database

    database = Database(data / "hearth.db")
    database.initialize()
    with database.transaction(write=True) as db:
        db.execute("UPDATE system_meta SET value=? WHERE key='runtime_kind'", (KIND,))
    capfd.readouterr()
    ClaudeLiveRuntime(data, binary=binary, config_dir=config_dir)
    captured = capfd.readouterr()
    assert "example.invalid" not in captured.err and "example.invalid" not in captured.out


def test_the_operator_sees_the_individual_requests_the_session_reported(tmp_path):
    from hearth.integrations.interface import receipt_requests

    rows = receipt_requests(json.dumps(receipt()))
    assert [row["model"] for row in rows] == [MODEL, "claude-haiku-4-5"]
    assert rows[0]["cache_write_1h_tokens"] == 3552
    # A stream whose numbers do not hold together reports nothing rather than a guess.
    assert receipt_requests(json.dumps(receipt("not-logged-in"))) == []
