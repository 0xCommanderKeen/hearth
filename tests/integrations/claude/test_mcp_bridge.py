"""Hearth's own tools inside a Claude session, over the real shim and the real socket.

Every test here launches a fake `claude` that reads the `--mcp-config` Hearth wrote,
starts the actual shim with the actual environment declared for it, and speaks MCP to
it over stdio -- so the socket path, the peer check, the frame handling and the
trusted worker's transaction are all exercised. Nothing reaches Anthropic; the
session's own numbers come from the recorded fixture #146 committed.
"""

import contextlib
import json
import selectors
import sys
import threading
import time
from pathlib import Path

import pytest
from hearth.integrations.claude.config import KIND
from hearth.integrations.claude.mcp_bridge import TOOL_PREFIX
from hearth.integrations.claude.subscription import ClaudeLiveRuntime, worker
from hearth.residents.models import Declaration, Refused

FIXTURES = Path(__file__).parent / "fixtures"
MEMORY_TOOLS = ["hearth_memory_read", "hearth_memory_save", "hearth_journal_write"]


def fake_cli(path: Path, session: Path) -> Path:
    """A `claude` that really speaks MCP to the shim, scripted by `session`."""
    path.write_text(
        f"#!{sys.executable}\n"
        "import sys\n"
        f"sys.path.insert(0, {str(Path(__file__).parent)!r})\n"
        "import bridge_cli\n"
        f"sys.exit(bridge_cli.main({str(session)!r}, sys.argv[1:]))\n"
    )
    path.chmod(0o700)
    return path


class Store:
    """One Claude store with one resident, ready to run sessions in-process."""

    def __init__(self, tmp_path: Path, *, memory_writable=True, grant=None):
        from hearth.execution.lifecycle import Execution
        from hearth.storage.artifacts import Artifacts
        from hearth.storage.database import Database
        from hearth.work.service import Hearth

        self.tmp_path = tmp_path
        self.data = tmp_path / "data"
        self.session_path = tmp_path / "session.json"
        self.record_path = tmp_path / "record.json"
        config_dir = tmp_path / "private-claude-config"
        config_dir.mkdir()
        self.binary = fake_cli(tmp_path / "claude", self.session_path)
        self.session_path.write_text("{}")
        database = Database(self.data / "hearth.db")
        database.initialize()
        with database.transaction(write=True) as db:
            db.execute("UPDATE system_meta SET value=? WHERE key='runtime_kind'", (KIND,))
        self.runtime = ClaudeLiveRuntime(self.data, binary=self.binary, config_dir=config_dir)
        self.hearth = Hearth(database)
        self.hearth.save_resident(
            "writer",
            Declaration("Writer", "Synthetic notes", 10_000_000, memory_writable=memory_writable),
            expected_revision=0,
        )
        if grant is not None:
            from hearth.management.authority import Management

            Management(self.hearth).save("writer", {"expected_revision": 0, **grant})
        self.execution = Execution(self.hearth, Artifacts(self.data / "artifacts"))

    def script(self, steps, **options) -> None:
        self.session_path.write_text(
            json.dumps(
                {
                    "fixture": str(FIXTURES / "success.jsonl"),
                    "record": str(self.record_path),
                    "steps": steps,
                    **options,
                }
            )
        )

    def run(self, key="note", *, reserve=200_000):
        """Admit one run and start it with the detached worker held back."""
        from unittest.mock import patch

        from hearth.execution.context import read_context
        from hearth.residents.memory import Memory

        task = self.hearth.submit(
            key, "writer", "Write the note", expires_at=int(self.hearth.clock()) + 600
        )
        run = self.hearth.admit(task.task_id, reserve=reserve)
        self.execution.prepare_start(run.id, run.owner_token)
        with self.hearth.database.transaction() as db:
            prompt = json.dumps(
                read_context(db, run.id, Memory(self.hearth).files),
                sort_keys=True,
                separators=(",", ":"),
            )
        with patch(
            "hearth.integrations.claude.subscription.subprocess.Popen", lambda *a, **k: None
        ):
            self.runtime.start(run.id, prompt)
        return run

    def work(self, run):
        """Run the worker in this process, then observe what it published."""
        worker(self.runtime.folder(run.id))
        return self.runtime.receipt(run.id)

    def record(self) -> dict:
        return json.loads(self.record_path.read_text())

    def settle(self, run):
        from hearth.execution.usage import binding
        from hearth.integrations.claude.subscription import encode

        published = self.runtime.receipt(run.id)
        with self.hearth.database.transaction() as db:
            row = db.execute("SELECT * FROM runs WHERE id=?", (run.id,)).fetchone()
            bound = binding(db, row)
        return self.execution.finish(
            run.id, run.owner_token, encode(published, bound)[2], _usage_receipt=published
        )

    def rows(self, sql, *parameters):
        with self.hearth.database.transaction() as db:
            return [dict(row) for row in db.execute(sql, parameters)]


@contextlib.contextmanager
def pumping(server):
    """Drive one bridge server's socket from a thread, as the worker's loop does."""
    stop = threading.Event()

    def loop():
        with selectors.DefaultSelector() as selector:
            server.attach(selector)
            while not stop.is_set():
                for key, _ in selector.select(0.05):
                    server.ready(key)

    thread = threading.Thread(target=loop)
    thread.start()
    try:
        yield
    finally:
        stop.set()
        thread.join()


def answer(reply) -> dict:
    """What one tool call answered, as the model would read it."""
    result = (reply or {}).get("result") or {}
    return json.loads(result["content"][0]["text"]) | {"isError": result.get("isError")}


def test_the_recorded_real_session_is_the_session_this_bridge_checks_for(tmp_path):
    """`fixtures/management.jsonl` is a real session, recorded against the pinned CLI.

    It was run on 2026-09-09 with `--mcp-config` naming this shim, a throwaway socket
    server standing in for the worker, and one tool offered. What it proves, and what
    the checks below are written from: the CLI connects to the shim, reports it as
    `{"name": "hearth", "status": "connected"}` with exactly the offered
    `mcp__hearth__*` names, and really calls the tool through it.
    """
    from hearth.integrations.claude.mcp_bridge import check_session

    events = [json.loads(line) for line in (FIXTURES / "management.jsonl").read_text().splitlines()]
    init = events[0]
    assert check_session(init, ["mcp__hearth__hearth_journal_write"]) == (
        init["session_id"],
        init["uuid"],
    )
    # One name more or fewer, or a server that did not connect, is not this session.
    for offered in ([], ["mcp__hearth__hearth_memory_read"], init["tools"] + ["x"]):
        with pytest.raises(Refused, match="claude_tools_changed"):
            check_session(init, offered)
    with pytest.raises(Refused, match="claude_tools_changed"):
        check_session(
            init | {"mcp_servers": [{"name": "hearth", "status": "failed"}]}, init["tools"]
        )
    # The model really called the tool, and the CLI really carried the answer back.
    used = [
        block
        for event in events
        if event.get("type") == "assistant"
        for block in event["message"]["content"]
        if block.get("type") == "tool_use"
    ]
    assert [block["name"] for block in used] == ["mcp__hearth__hearth_journal_write"]
    assert events[-1]["permission_denials"] == []


def test_a_real_session_that_called_a_tool_settles_at_the_price_the_cli_reported():
    """Two requests, one of them a tool call, and the money still adds up.

    This is the session that corrected #146's reading of `usage.iterations`: it holds
    one row for a session that billed two requests, so the per-model total is what is
    priced. Hearth's own arithmetic still has to match the CLI's, per model and in
    total, or nothing settles.
    """
    from dataclasses import asdict

    from hearth.integrations.claude.pricing import MODEL, PRICE_SCHEDULE
    from hearth.integrations.claude.subscription import encode
    from hearth.integrations.codex.usage import UsageBinding

    binding = UsageBinding("real-run", "a" * 64, MODEL, "standard", PRICE_SCHEDULE)
    receipt = {
        "kind": KIND,
        "binding": asdict(binding),
        "binary": "b" * 64,
        "stdout": (FIXTURES / "management.jsonl").read_text(),
        "exit_code": 0,
        "cancelled": False,
        "launched": True,
        "management": {"catalog_sha256": "c" * 64, "tools_sha256": "d" * 64, "error": None},
    }
    evidence = encode(receipt, binding)[2]
    assert evidence.status == "succeeded" and evidence.output == "done"
    # 44,519 for the pinned model and 995 for the CLI's own housekeeping model, which
    # is the $0.0455135 the CLI reported for the session, to the microdollar.
    assert evidence.cost == 45_514


def test_one_grant_hashes_to_one_tools_pin_on_either_runtime():
    """The Claude pin is the same digest of the same list Codex pins, or it means less."""
    from hearth.integrations.claude.mcp_bridge import configuration_pins
    from hearth.integrations.codex import app_server_config
    from hearth.management.tools import tool_specs

    # `app_server_config.digest` is what Codex's own `configuration_pins` writes as
    # `tools_sha256`; the Claude pin has to be the same number for the same list.
    for tools in (
        tool_specs(memory=True, management=False),
        tool_specs(memory=True, management=True, send_letters=True),
    ):
        assert configuration_pins(tools)["tools_sha256"] == app_server_config.digest(tools)


def test_a_run_is_offered_exactly_the_tools_its_authority_allows(tmp_path):
    """A resident that may write its own memory reaches those three tools and no more."""
    store = Store(tmp_path)
    store.script([])
    run = store.run()
    store.work(run)
    record = store.record()
    listed = [tool["name"] for tool in record["listed"]["result"]["tools"]]
    assert listed == MEMORY_TOOLS
    # Existence and permission are two different flags, and both name the same set.
    expected = [TOOL_PREFIX + name for name in MEMORY_TOOLS]
    argv = record["argv"]
    assert argv[argv.index("--tools") + 1] == ",".join(expected)
    assert argv[argv.index("--allowedTools") + 1] == ",".join(expected)
    assert argv[argv.index("--mcp-config") + 1].endswith("mcp.json")
    assert "--strict-mcp-config" in argv
    # Every listed tool carries its own schema, so the model is not guessing.
    assert all(
        tool["inputSchema"]["type"] == "object" for tool in record["listed"]["result"]["tools"]
    )


def test_the_shim_carries_no_credential_no_store_and_no_token(tmp_path):
    store = Store(tmp_path)
    store.script([{"tool": "hearth_journal_write", "arguments": {"text": "Wrote the note."}}])
    run = store.run()
    store.work(run)
    record = store.record()
    server = record["configuration"]["mcpServers"]["hearth"]
    # The whole environment the shim is given, and the whole of its argv.
    assert record["shim_environment"] is not None and set(server["env"]) == {"PATH"}
    written = json.dumps(record["configuration"]) + json.dumps(record["argv"])
    with store.hearth.database.transaction() as db:
        owner = db.execute("SELECT owner_token FROM runs WHERE id=?", (run.id,)).fetchone()[0]
    for secret in (owner, "hearth.db", str(tmp_path / "private-claude-config")):
        assert secret not in written
    assert server["args"][:3] == ["-I", "-m", "hearth.integrations.claude.mcp_bridge"]
    assert server["args"][3].endswith("bridge.sock")
    # The socket is the run's own, and only its owner can reach it.
    assert (store.runtime.folder(run.id) / "mcp.json").stat().st_mode & 0o777 == 0o600


def test_a_call_over_the_bridge_writes_and_is_audited_in_one_transaction(tmp_path):
    store = Store(tmp_path)
    store.script([{"tool": "hearth_journal_write", "arguments": {"text": "Wrote the note."}}])
    run = store.run()
    store.work(run)
    call = store.record()["calls"][0]
    assert answer(call["reply"])["isError"] is False
    entries = store.rows("SELECT * FROM journal_entries WHERE resident_id='writer'")
    assert [entry["text"] for entry in entries] == ["Wrote the note."]
    assert entries[0]["run_id"] == run.id
    recorded = store.rows("SELECT * FROM management_calls WHERE run_id=?", run.id)
    assert len(recorded) == 1
    facts = store.rows("SELECT * FROM audit WHERE resource_id=?", run.id)
    completed = [fact for fact in facts if fact["kind"] == "management.tool_completed"]
    assert len(completed) == 1
    assert json.loads(completed[0]["detail"])["tool"] == "hearth_journal_write"
    # The session still settled from its own stream, at the recorded price.
    result = store.settle(run)
    assert (result.status, result.actual_cost) == ("succeeded", 36_580)


def test_a_call_identity_used_twice_answers_once_and_refuses_a_different_payload(tmp_path):
    store = Store(tmp_path)
    store.script(
        [
            {"id": 7, "tool": "hearth_journal_write", "arguments": {"text": "First."}},
            {"id": 7, "tool": "hearth_journal_write", "arguments": {"text": "First."}},
            {"id": 7, "tool": "hearth_journal_write", "arguments": {"text": "Second."}},
        ]
    )
    run = store.run()
    store.work(run)
    calls = store.record()["calls"]
    assert calls[0]["reply"]["result"] == calls[1]["reply"]["result"]
    assert answer(calls[2]["reply"]) == {"error": "management_call_conflict", "isError": True}
    # One identity, one entry, one recorded call.
    assert [row["text"] for row in store.rows("SELECT * FROM journal_entries")] == ["First."]
    assert len(store.rows("SELECT * FROM management_calls WHERE run_id=?", run.id)) == 1


def test_a_tool_the_run_was_never_offered_is_refused_and_recorded(tmp_path):
    """The offered set is the run's authority; nothing outside it reaches a writer."""
    store = Store(tmp_path)
    store.script([{"tool": "hearth_catalog", "arguments": {"query": "everything"}}])
    run = store.run()
    store.work(run)
    assert answer(store.record()["calls"][0]["reply"]) == {
        "error": "management_tool_not_offered",
        "isError": True,
    }
    facts = store.rows("SELECT * FROM audit WHERE kind='management.tool_refused'")
    assert len(facts) == 1 and facts[0]["resource_id"] == run.id
    assert json.loads(facts[0]["detail"])["tool"] == "hearth_catalog"
    # A refused call is not a call: it spends nothing of the run's allowance.
    assert store.rows("SELECT * FROM management_calls WHERE run_id=?", run.id) == []


def test_a_granted_resident_is_offered_the_whole_management_surface(tmp_path):
    """The Karen shape: a grant carries the management tools into a Claude session."""
    store = Store(
        tmp_path,
        grant={"enabled": True, "capabilities": ["assign_work"], "profiles": [KIND]},
    )
    store.script([{"tool": "hearth_catalog", "arguments": {"query": ""}}])
    run = store.run()
    store.work(run)
    record = store.record()
    listed = [tool["name"] for tool in record["listed"]["result"]["tools"]]
    # Every memory tool, and the management tools the grant carries beside them.
    assert set(MEMORY_TOOLS) < set(listed)
    assert {"hearth_catalog", "hearth_residents_read", "hearth_work_assign"} <= set(listed)
    argv = record["argv"]
    assert argv[argv.index("--tools") + 1].split(",") == [TOOL_PREFIX + name for name in listed]
    # And the call really ran: the catalog it answered with is this store's own.
    catalog = answer(record["calls"][0]["reply"])
    assert catalog["isError"] is False
    assert [resident["id"] for resident in catalog["residents"]] == ["writer"]
    assert store.rows("SELECT * FROM management_calls WHERE run_id=?", run.id) != []
    # What the operator is shown names the transport those calls really travelled on,
    # which is this provider's own and not the one the other adapter uses.
    from hearth.observation.snapshot import snapshot

    shown = {row["id"]: row for row in snapshot(store.hearth)["runs"]}[run.id]["management"]
    assert shown["protocol"] == "claude_mcp_bridge" and shown["calls"] == 1


def test_a_grant_revoked_mid_session_closes_management_but_not_memory(tmp_path):
    """ADR 0012 over the bridge: remembering is not managing, and outlives the grant."""
    store = Store(
        tmp_path,
        grant={"enabled": True, "capabilities": ["assign_work"], "profiles": [KIND]},
    )
    ready, go = tmp_path / "ready", tmp_path / "go"
    store.script(
        [
            {"signal": str(ready)},
            {"await": str(go)},
            {"tool": "hearth_catalog", "arguments": {"query": "everything"}},
            {"tool": "hearth_journal_write", "arguments": {"text": "Answered anyway."}},
        ]
    )
    run = store.run()

    def revoke():
        from hearth.management.authority import Management

        deadline = time.monotonic() + 20
        while time.monotonic() < deadline and not ready.exists():
            time.sleep(0.02)
        Management(store.hearth).save("writer", {"expected_revision": 1, "enabled": False})
        go.write_text("go")

    thread = threading.Thread(target=revoke)
    thread.start()
    try:
        store.work(run)
    finally:
        thread.join()
    calls = store.record()["calls"]
    assert answer(calls[0]["reply"]) == {
        "error": "management_grant_changed_or_revoked",
        "isError": True,
    }
    assert answer(calls[1]["reply"])["isError"] is False
    assert [row["text"] for row in store.rows("SELECT * FROM journal_entries")] == [
        "Answered anyway."
    ]


def test_the_shim_answers_a_malformed_frame_and_keeps_serving(tmp_path):
    store = Store(tmp_path)
    store.script(
        [
            {"raw": "{ this is not JSON"},
            {"raw": json.dumps({"jsonrpc": "2.0", "id": 41, "method": "hearth/invent"})},
            {"tool": "hearth_journal_write", "arguments": {"text": "Still here."}},
        ]
    )
    run = store.run()
    store.work(run)
    calls = store.record()["calls"]
    assert calls[0]["reply"]["error"]["code"] == -32700
    assert calls[1]["reply"]["error"]["code"] == -32601
    # The socket was not held open by either, and the next call was answered.
    assert answer(calls[2]["reply"])["isError"] is False
    assert store.runtime.receipt(run.id)["management"]["error"] is None


def test_a_frame_the_socket_never_expects_is_refused_without_ending_the_run(tmp_path):
    """Straight at the socket, past the shim: an unknown frame answers and disconnects."""
    from hearth.integrations.claude.mcp_bridge import connect, open_bridge, socket_path

    store = Store(tmp_path)
    store.script([])
    run = store.run()
    folder = store.runtime.folder(run.id)
    request = json.loads((folder / "request.json").read_text())
    server = open_bridge(folder, request, store.hearth)
    try:
        assert server.answer(b"{not json") == {"error": "mcp_bridge_frame_invalid"}
        assert server.answer(b'{"op":"nonsense"}') == {"error": "mcp_bridge_frame_invalid"}
        assert [
            tool["name"] for tool in server.answer(b'{"op":"tools/list"}')["result"]["tools"]
        ] == MEMORY_TOOLS
        # A call frame missing its arguments is refused as a call, not as a frame.
        assert answer(server.answer(b'{"op":"tools/call","call_id":"1","tool":"hearth_x"}')) == {
            "error": "management_call_invalid",
            "isError": True,
        }
        # A well-formed call before the session is trusted still mutates nothing.
        early = b'{"op":"tools/call","call_id":"1","tool":"hearth_memory_read","arguments":{}}'
        assert answer(server.answer(early)) == {
            "error": "management_session_untrusted",
            "isError": True,
        }
        # The socket is the run's own, and carries the same permission as its folder.
        assert socket_path(folder).stat().st_mode & 0o777 == 0o600
        with pumping(server), connect(socket_path(folder), timeout=5) as client:
            client.sendall(b'{"op":"tools/list"}\n')
            assert b"hearth_memory_read" in client.recv(65536)
    finally:
        server.close()
    assert not socket_path(folder).exists()


class Pairing:
    """A selector that hands the worker one batch holding both readinesses at once.

    Readiness for the session's stdout and for the bridge's socket normally arrive in
    separate batches, seconds apart, and a batch's own order is not defined. This holds
    a stdout-only readiness back -- the pipe is level-triggered, so it stays ready --
    until a socket readiness joins it, and then releases both with the socket first:
    the exact order the loop must not depend on. It gives up and passes everything
    through after a while, so a session that never calls a tool still finishes.
    """

    def __init__(self, inner):
        self.inner = inner
        # The worker registers its child's stdout first and the bridge's listener
        # second; everything after that is one connection carrying one call.
        self.registered = []
        self.withheld = 0
        self.paired = False

    @property
    def stdout(self):
        return self.registered[0] if self.registered else None

    @property
    def listener(self):
        return self.registered[1] if len(self.registered) > 1 else None

    def __getattr__(self, name):
        return getattr(self.inner, name)

    def __enter__(self):
        self.inner.__enter__()
        return self

    def __exit__(self, *details):
        return self.inner.__exit__(*details)

    def register(self, fileobj, events, data=None):
        if len(self.registered) < 2:
            self.registered.append(fileobj)
        return self.inner.register(fileobj, events, data)

    def select(self, timeout=None):
        ready = self.inner.select(timeout)
        if self.paired or not ready:
            return ready
        held = [item for item in ready if item[0].fileobj is self.stdout]
        others = [item for item in ready if item[0].fileobj is not self.stdout]
        if others and any(item[0].fileobj is not self.listener for item in others) and held:
            # A tool call and the chunk carrying `init`, in one batch, the call first.
            self.paired = True
            return others + held
        if others:
            # The listener still has to be answered, or no call is ever accepted.
            return others
        self.withheld += 1
        return [] if self.withheld <= 40 else ready


def test_a_call_arriving_with_the_init_it_depends_on_is_answered_not_refused(tmp_path):
    """The session's own output is read and checked before any socket key beside it."""
    import types

    from hearth.integrations.claude import subscription

    store = Store(tmp_path)
    # No retry: the first answer this call gets is the one the test is about.
    store.script(
        [{"tool": "hearth_journal_write", "arguments": {"text": "Written first time."}}],
        no_retry=True,
    )
    run = store.run()
    made = []

    def selector(*arguments, **options):
        wrapper = Pairing(selectors.DefaultSelector(*arguments, **options))
        made.append(wrapper)
        return wrapper

    original = subscription.selectors
    subscription.selectors = types.SimpleNamespace(
        DefaultSelector=selector,
        EVENT_READ=selectors.EVENT_READ,
        EVENT_WRITE=selectors.EVENT_WRITE,
    )
    try:
        store.work(run)
    finally:
        subscription.selectors = original
    assert made and made[0].paired, "the two readinesses were never delivered together"
    assert answer(store.record()["calls"][0]["reply"])["isError"] is False
    assert [row["text"] for row in store.rows("SELECT * FROM journal_entries")] == [
        "Written first time."
    ]


def test_a_call_before_the_session_reports_its_tools_changes_nothing(tmp_path):
    """The bridge is shut until Hearth has seen the session's own `init` event."""
    store = Store(tmp_path)
    store.script(
        [{"tool": "hearth_journal_write", "arguments": {"text": "Too early."}}],
        steps_before_init=True,
    )
    run = store.run()
    store.work(run)
    assert answer(store.record()["calls"][0]["reply"]) == {
        "error": "management_session_untrusted",
        "isError": True,
    }
    assert store.rows("SELECT * FROM journal_entries") == []


@pytest.mark.parametrize(
    "changed",
    [
        {"reported_tools": []},
        {"reported_tools": ["mcp__hearth__hearth_journal_write"]},
        {"reported_servers": []},
        {"reported_servers": [{"name": "hearth", "status": "failed"}]},
    ],
)
def test_a_session_whose_tool_surface_is_not_the_pinned_one_is_stopped(tmp_path, changed):
    store = Store(tmp_path)
    store.script(
        [{"tool": "hearth_journal_write", "arguments": {"text": "Never written."}}], **changed
    )
    run = store.run()
    published = store.work(run)
    assert published["management"]["error"] == "claude_tools_changed"
    assert store.rows("SELECT * FROM journal_entries") == []
    # Stopped, not cancelled: the session was launched, so its cost is not zero by
    # assumption, and it settles as failed.
    assert published["cancelled"] is False and published["launched"] is True
    assert store.runtime.inspect(run.id).status == "failed"


def test_a_session_that_is_not_the_pinned_build_or_model_is_stopped(tmp_path):
    store = Store(tmp_path)
    store.script([], reported_version="2.1.999")
    run = store.run()
    assert store.work(run)["management"]["error"] == "claude_session_unpinned"


def test_a_run_that_reaches_no_tool_is_launched_without_a_bridge(tmp_path):
    """Nothing about the bridge touches a run that was never pinned to Hearth's tools."""
    store = Store(tmp_path, memory_writable=False)
    store.script([])
    run = store.run()
    published = store.work(run)
    record = store.record()
    argv = record["argv"]
    assert argv[argv.index("--tools") + 1] == ""
    assert "--mcp-config" not in argv and "--allowedTools" not in argv
    assert "management" not in published
    assert "configuration" not in record
    assert store.settle(run).actual_cost == 36_580


def test_a_grant_changed_between_admission_and_launch_is_refused_before_a_launch(tmp_path):
    """The pins are checked again in the worker, where nothing has been spent yet."""
    store = Store(tmp_path)
    store.script([])
    run = store.run()
    folder = store.runtime.folder(run.id)
    with store.hearth.database.transaction(write=True) as db:
        db.execute(
            "UPDATE run_management SET tools_sha256='c' WHERE run_id=?",
            (run.id,),
        )
    published = store.work(run)
    assert published["launched"] is False and published["cancelled"] is True
    assert published["management"]["error"] == "management_configuration_changed"
    assert not (folder / "mcp.json").exists()
    # Nothing ran, so the run settles at nothing.
    evidence = store.runtime.inspect(run.id)
    assert (evidence.status, evidence.cost) == ("cancelled", 0)


def test_settlement_refuses_a_receipt_configured_differently_from_the_pin(tmp_path):
    from hearth.integrations.claude.receipts import validate_receipt_pins

    store = Store(tmp_path)
    store.script([])
    run = store.run()
    published = store.work(run)
    pins = {"claude_live_binary": published["binary"], "management": None}
    with pytest.raises(Refused, match="management_not_granted_at_admission"):
        validate_receipt_pins(KIND, published, pins, cancelled=False)
    pin = store.rows("SELECT * FROM run_management WHERE run_id=?", run.id)[0]
    assert validate_receipt_pins(KIND, published, pins | {"management": pin}, cancelled=False) is (
        False
    )
    for change in ({"tools_sha256": "z" * 64}, {"catalog_sha256": None}):
        with pytest.raises(Refused, match="management_configuration_changed"):
            validate_receipt_pins(
                KIND, published, pins | {"management": pin | change}, cancelled=False
            )
    # And a management run whose receipt carries no envelope at all cannot settle.
    with pytest.raises(Refused, match="management_native_receipt_required"):
        validate_receipt_pins(
            KIND,
            {key: value for key, value in published.items() if key != "management"},
            pins | {"management": pin},
            cancelled=False,
        )


def test_a_writer_that_fails_ends_the_session_and_says_so_in_the_receipt(tmp_path, monkeypatch):
    """Hearth's own failure is never answered as a refusal and never left silent."""
    import sqlite3

    from hearth.management.bridge import Bridge

    store = Store(tmp_path)
    store.script([{"tool": "hearth_journal_write", "arguments": {"text": "Never written."}}])
    run = store.run()

    def broken(self, params):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(Bridge, "call", broken)
    published = store.work(run)
    assert published["management"]["error"] == "mcp_bridge_failed"
    assert store.rows("SELECT * FROM journal_entries") == []
    # Launched, so not free, and never unknown-with-a-relaunch.
    assert published["launched"] is True
    assert store.runtime.inspect(run.id).status == "failed"


def test_a_bridge_that_cannot_be_opened_leaves_a_receipt_rather_than_a_run(tmp_path):
    """Hearth's own failure still settles: a run with no receipt could never close."""
    store = Store(tmp_path)
    store.script([])
    run = store.run()
    # Something is already where the per-run configuration has to be written.
    (store.runtime.folder(run.id) / "mcp.json").write_text("{}")
    published = store.work(run)
    assert published["launched"] is False and published["cancelled"] is True
    assert published["management"]["error"] == "mcp_bridge_failed"
    assert not store.record_path.exists()
    assert store.runtime.inspect(run.id).status == "cancelled"


def test_a_session_asking_for_tools_it_has_not_got_cannot_fill_the_audit(tmp_path):
    """A refusal is recorded, but only as many times as the run may call at all."""
    store = Store(
        tmp_path,
        grant={
            "enabled": True,
            "capabilities": ["assign_work"],
            "profiles": [KIND],
            "max_calls": 2,
        },
    )
    store.script([{"tool": "hearth_nonsense", "arguments": {}} for _ in range(4)])
    run = store.run()
    store.work(run)
    replies = [answer(call["reply"]) for call in store.record()["calls"]]
    assert all(reply["error"] == "management_tool_not_offered" for reply in replies)
    assert len(store.rows("SELECT * FROM audit WHERE kind='management.tool_refused'")) == 2


@pytest.mark.parametrize("usage", ["known", "invalid", "unknown"])
def test_a_final_bridge_failure_survives_normal_cli_exit_and_settles_once(
    tmp_path, monkeypatch, usage
):
    from hearth.execution.usage import by_origin
    from hearth.integrations.claude.mcp_bridge import BridgeServer

    store = Store(tmp_path)
    store.script([])
    run = store.run()
    original_close = BridgeServer.close

    def late_failure(server):
        # Model the final-call failure visible only as the bridge closes, after
        # stdout reached EOF. This exercises the worker's server.failure fallback.
        server.failure = "mcp_bridge_failed"
        original_close(server)

    monkeypatch.setattr(BridgeServer, "close", late_failure)
    published = store.work(run)
    assert published["exit_code"] == 0 and published["cancelled"] is False
    assert published["management"]["error"] == "mcp_bridge_failed"
    if usage != "known":
        events = [json.loads(line) for line in published["stdout"].splitlines()]
        if usage == "invalid":
            events[-1]["total_cost_usd"] = 9.99
        else:
            del events[-1]["total_cost_usd"]
        published["stdout"] = "\n".join(json.dumps(event) for event in events) + "\n"
        (store.runtime.folder(run.id) / "receipt.json").write_text(json.dumps(published))
    evidence = store.runtime.inspect(run.id)
    assert evidence.status == "failed" and evidence.output == "pong"
    result = store.settle(run)
    assert result.status == "failed"
    assert result.actual_cost == (36_580 if usage == "known" else None)
    assert bool(result.usage_known) is (usage == "known")
    assert store.execution.artifact(result.artifact_id)[1] == "pong"
    with pytest.raises(Refused, match="run_already_finished"):
        store.settle(run)
    stored = store.rows("SELECT receipt FROM run_usage WHERE run_id=?", run.id)
    assert len(stored) == 1
    assert json.loads(stored[0]["receipt"])["management"]["error"] == "mcp_bridge_failed"
    with store.hearth.database.transaction() as db:
        origin = by_origin(db)["origins"][0]
    assert origin["known_cost"] == (36_580 if usage == "known" else 0)
    assert origin["unknown_runs"] == (0 if usage == "known" else 1)
    facts = store.rows("SELECT * FROM audit WHERE kind='run.failed' AND resource_id=?", run.id)
    assert len(facts) == 1
    from hearth.storage.backup import capture, verify

    capture(store.data, tmp_path / "backup")
    verify(tmp_path / "backup")
    # Neither another start nor another worker can launch this run again.
    folder = store.runtime.folder(run.id)
    request = json.loads((folder / "request.json").read_text())
    monkeypatch.setattr(
        "hearth.integrations.claude.subscription.subprocess.Popen",
        lambda *a, **kw: pytest.fail("settled bridge failure relaunched"),
    )
    store.runtime.start(run.id, request["prompt"])
    worker(folder)
    assert store.runtime.receipt(run.id) == published
    if usage != "known":
        task = store.hearth.submit(
            "next", "writer", "Next", expires_at=int(store.hearth.clock()) + 600
        )
        with pytest.raises(Refused, match="resident_paused"):
            store.hearth.admit(task.task_id, reserve=10_000)
