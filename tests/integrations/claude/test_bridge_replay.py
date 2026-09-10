"""Retries cross the real Claude socket and the owning SQLite authority each time."""

import contextlib
import json
from dataclasses import replace

import pytest
from hearth.integrations.claude.config import KIND
from hearth.integrations.claude.mcp_bridge import BridgeServer, respond, session_tools, socket_path
from hearth.management.authority import Management
from hearth.management.bridge import BoundRun
from hearth.observation.snapshot import snapshot

from tests.integrations.claude.test_mcp_bridge import Store, answer, pumping

GRANT = {"enabled": True, "capabilities": ["assign_work"], "profiles": [KIND]}


@contextlib.contextmanager
def transport(store, run):
    bound = BoundRun(run.id, run.owner_token, snapshot(store.hearth)["epoch"], run.input_digest)
    with store.hearth.database.transaction() as db:
        tools, max_calls = session_tools(db, store.hearth, bound)
    folder = store.tmp_path / "transport"
    folder.mkdir(exist_ok=True)
    server = BridgeServer(
        folder, hearth=store.hearth, bound=bound, tools=tools, max_calls=max_calls
    )
    server.trust("synthetic-session", "synthetic-turn")
    server.open()

    def call(ident, tool, arguments):
        frame = json.dumps(
            {"id": ident, "method": "tools/call", "params": {"name": tool, "arguments": arguments}}
        ).encode()
        return answer(respond(socket_path(folder), frame))

    try:
        with pumping(server):
            yield call
    finally:
        server.close()


@pytest.mark.parametrize("memory_writable", [False, True])
@pytest.mark.parametrize("change", ["revoked", "revised", "cancelled", "expired", "inactive"])
def test_replay_obeys_current_authority(tmp_path, change, memory_writable):
    store = Store(tmp_path, grant=GRANT, memory_writable=memory_writable)
    run = store.run()
    with transport(store, run) as call:
        assert call("catalog", "hearth_catalog", {"query": ""})["isError"] is False
        if change in {"revoked", "revised"}:
            Management(store.hearth).save(
                "writer", {**GRANT, "expected_revision": 1, "enabled": change == "revised"}
            )
            error = "management_grant_changed_or_revoked"
        elif change == "expired":
            expiry = store.rows("SELECT expires_at FROM run_management WHERE run_id=?", run.id)[0]
            store.hearth.clock = lambda: expiry["expires_at"]
            error = "management_access_expired"
        else:
            if change == "cancelled":
                store.execution.cancel(run.id)
            else:
                store.execution.observe(run.id, run.owner_token, "interrupted")
            error = "management_run_inactive"
        refused = {"error": error, "isError": True}
        assert call("fresh", "hearth_catalog", {"query": ""}) == refused
        assert call("catalog", "hearth_catalog", {"query": ""}) == refused
    assert len(store.rows("SELECT * FROM management_calls")) == 1


def test_retries_use_durable_receipts_and_do_not_repeat_mutations(tmp_path):
    store = Store(tmp_path, grant={**GRANT, "max_calls": 1})
    run = store.run()
    with transport(store, run) as call:
        original = call("journal", "hearth_journal_write", {"text": "One fact."})
        assert original["isError"] is False
        assert call("journal", "hearth_journal_write", {"text": "One fact."}) == original
        assert call("journal", "hearth_journal_write", {"text": "Changed."}) == {
            "error": "management_call_conflict",
            "isError": True,
        }
        assert call("second", "hearth_catalog", {"query": ""}) == {
            "error": "management_call_limit",
            "isError": True,
        }
    # A fresh transport holds no local reply; the same run still owns the receipt.
    with transport(store, run) as call:
        assert call("journal", "hearth_journal_write", {"text": "One fact."}) == original
    assert len(store.rows("SELECT * FROM management_calls")) == 1
    assert len(store.rows("SELECT * FROM audit WHERE kind='management.tool_completed'")) == 1
    assert [row["text"] for row in store.rows("SELECT * FROM journal_entries")] == ["One fact."]


def test_memory_replays_and_fresh_writes_outlive_management_revocation(tmp_path):
    store = Store(tmp_path, grant=GRANT)
    run = store.run()
    with transport(store, run) as call:
        tools = {
            "hearth_memory_read": {},
            "hearth_memory_save": {
                "resident_id": "writer",
                "operation_id": "save",
                "expected_revision": 0,
                "text": "A standing fact.",
            },
            "hearth_journal_write": {"text": "One observation."},
        }
        replies = {tool: call(tool, tool, arguments) for tool, arguments in tools.items()}
        assert all(reply["isError"] is False for reply in replies.values())
        Management(store.hearth).save("writer", {"expected_revision": 1, "enabled": False})
        for tool, arguments in tools.items():
            assert call(tool, tool, arguments) == replies[tool]
        assert call("closing", "hearth_journal_write", {"text": "Closed."})["isError"] is False
    assert len(store.rows("SELECT * FROM journal_entries")) == 1


@pytest.mark.parametrize("reply_before_revocation", [False, True])
def test_letter_read_and_reply_replays_outlive_management_revocation(
    tmp_path, reply_before_revocation
):
    store = Store(tmp_path, grant=GRANT, memory_writable=False)
    resident = store.hearth.resident("writer")
    store.hearth.save_resident(
        "writer",
        replace(resident.declaration, letters_accept=True),
        expected_revision=resident.revision,
    )
    letter = store.hearth.send_operator_letter("question", "writer", "Orchard", "How many trees?")
    run = store.hearth.admit(letter["task_id"], reserve=100_000)
    store.execution.prepare_start(run.id, run.owner_token)
    with transport(store, run) as call:
        post = call("post", "hearth_letters_read", {})
        arguments = {"operation_id": "reply", "letter_id": letter["task_id"], "text": "412 trees."}
        reply = (
            call("reply", "hearth_letters_reply", arguments) if reply_before_revocation else None
        )
        Management(store.hearth).save("writer", {"expected_revision": 1, "enabled": False})
        if reply is None:
            reply = call("reply", "hearth_letters_reply", arguments)
        assert post["isError"] is False and reply["isError"] is False
        assert call("post", "hearth_letters_read", {}) == post
        assert call("reply", "hearth_letters_reply", arguments) == reply
        assert call("fresh-post", "hearth_letters_read", {})["isError"] is False
        assert call("catalog", "hearth_catalog", {"query": ""}) == {
            "error": "management_grant_changed_or_revoked",
            "isError": True,
        }
    assert len(store.rows("SELECT * FROM letter_replies")) == 1
