"""In-run memory and journal tools, and the journal the next run opens with."""

import json
from dataclasses import asdict, replace

import pytest
from hearth.app import create_app
from hearth.authority.household import Household
from hearth.execution.context import read_context
from hearth.execution.usage import binding as usage_binding
from hearth.integrations.codex.pricing import MODEL
from hearth.integrations.codex.subscription import KIND
from hearth.integrations.interface import encode_receipt
from hearth.management.authority import Management
from hearth.management.bootstrap import bootstrap
from hearth.management.bridge import BoundRun, Bridge
from hearth.management.tools import tool_specs
from hearth.observation.snapshot import snapshot
from hearth.residents.journal import Journal
from hearth.residents.memory import Memory
from hearth.residents.models import Declaration, Refused

from tests.fake_runtime import fake_runtime

TOKEN = "synthetic-memory-tools-operator"
MEMORY_TOOL_NAMES = {"hearth_memory_read", "hearth_memory_save", "hearth_journal_write"}


def manager(tmp_path):
    app = create_app(tmp_path, TOKEN, supervise=False, runtime=fake_runtime())
    hearth = app.state.hearth
    hearth.clock = lambda: 1788640000
    return app, hearth, bootstrap(hearth)["resident_id"]


def working_run(app, resident_id: str, key: str):
    """One admitted, launched run of a resident, with its private native bridge."""
    hearth = app.state.hearth
    task = hearth.submit(key, resident_id, "Synthetic work", expires_at=int(hearth.clock()) + 600)
    run = hearth.admit(task.task_id, reserve=100000)
    app.state.execution.prepare_start(run.id, run.owner_token)
    bridge = Bridge(
        hearth, BoundRun(run.id, run.owner_token, snapshot(hearth)["epoch"], run.input_digest)
    )
    thread, turn = "thread-" + key, "turn-" + key
    bridge.bind_thread(thread)
    bridge.bind_turn(thread, turn)

    def call(call_id: str, tool: str, arguments: dict):
        response = bridge.call(
            dict(
                threadId=thread,
                turnId=turn,
                callId=key + "-" + call_id,
                tool=tool,
                arguments=arguments,
            )
        )
        return response["success"], json.loads(response["contentItems"][0]["text"])

    return run, call


def native_terminal(pin) -> dict:
    """The app-server evidence the management runtime seals this run's one turn with."""
    thread, turn = pin["thread_id"], pin["turn_id"]
    return {
        "protocol": "codex-app-server-0.153.4",
        "launched": True,
        "cancelled": False,
        "error": None,
        "exit_code": -15,
        "catalog_sha256": pin["catalog_sha256"],
        "tools_sha256": pin["tools_sha256"],
        "events": [
            {"method": "thread/started", "params": {"thread": {"id": thread, "model": MODEL}}},
            {"method": "turn/started", "params": {"threadId": thread, "turn": {"id": turn}}},
            {
                "method": "thread/tokenUsage/updated",
                "params": {
                    "threadId": thread,
                    "turnId": turn,
                    "tokenUsage": {
                        "total": {
                            "totalTokens": 30,
                            "inputTokens": 20,
                            "outputTokens": 10,
                            "cachedInputTokens": 0,
                            "reasoningOutputTokens": 0,
                        }
                    },
                },
            },
            {
                "method": "turn/completed",
                "params": {
                    "threadId": thread,
                    "turn": {
                        "id": turn,
                        "status": "completed",
                        "error": None,
                        "items": [
                            {
                                "type": "agentMessage",
                                "text": "# Daily summary",
                                "phase": "final_answer",
                            }
                        ],
                    },
                },
            },
        ],
    }


def settle(app, run) -> None:
    """End the run the way its runtime does: every run is priced from a real receipt."""
    hearth = app.state.hearth
    with hearth.database.transaction(write=True) as db:
        # A run that never reached pin_configuration still needs its launch pins.
        db.execute(
            "UPDATE run_management SET catalog_sha256=COALESCE(catalog_sha256,?), "
            "tools_sha256=COALESCE(tools_sha256,?) WHERE run_id=?",
            ("b" * 64, "c" * 64, run.id),
        )
        pin = db.execute("SELECT * FROM run_management WHERE run_id=?", (run.id,)).fetchone()
        bound = usage_binding(db, db.execute("SELECT * FROM runs WHERE id=?", (run.id,)).fetchone())
        binary = db.execute(
            "SELECT value FROM system_meta WHERE key='codex_live_binary'"
        ).fetchone()[0]
    receipt = {
        "kind": KIND,
        "protocol": "management",
        "binding": asdict(bound),
        "binary": binary,
        "terminal": native_terminal(pin),
    }
    app.state.execution.finish(
        run.id, run.owner_token, encode_receipt(receipt, bound)[2], _usage_receipt=receipt
    )


def pinned_context(hearth, run_id: str) -> dict:
    with hearth.database.transaction() as db:
        return read_context(db, run_id, Memory(hearth).files)


def test_a_run_saves_memory_and_a_journal_entry_and_the_next_run_opens_with_both(tmp_path):
    app, hearth, karen = manager(tmp_path)
    first, call = working_run(app, karen, "first")
    assert pinned_context(hearth, first.id)["journal"] == []

    ok, pinned = call("read", "hearth_memory_read", {})
    assert ok and pinned["revision"] == 1 and pinned["next_offset"] is None
    assert pinned["text"].startswith("New residents receive no management")

    remembered = pinned["text"] + "\nThe orchard reporter summarizes synthetic pears."
    arguments = {
        "operation_id": "remember-the-reporter",
        "resident_id": karen,
        "text": remembered,
        "expected_revision": 1,
    }
    ok, saved = call("save", "hearth_memory_save", arguments)
    assert ok and saved["revision"] == 2 and saved["author"] == "run"
    # An uncertain retry replays the original revision instead of writing another.
    assert call("save-retry", "hearth_memory_save", arguments)[1] == saved
    ok, entry = call("journal", "hearth_journal_write", {"text": "Wrote the first daily summary."})
    assert ok and entry["sequence"] == 1 and entry["run_id"] == first.id
    assert entry["archived"] == []
    settle(app, first)

    # A human edits the same note between the runs; neither edit is lost.
    operator = remembered + "\nOperator: keep every pear synthetic."
    assert Memory(hearth).save(karen, operator, expected_revision=2)["revision"] == 3

    second, next_call = working_run(app, karen, "second")
    context = pinned_context(hearth, second.id)
    assert context["context_version"] == 8 and context["memory_writable"] is True
    assert context["memory"]["revision"] == 3 and context["memory"]["text"] == operator
    assert "orchard reporter" in context["memory"]["text"]
    assert context["journal"] == [
        {
            "sequence": 1,
            "run_id": first.id,
            "at": 1788640000,
            "text": "Wrote the first daily summary.",
        }
    ]
    assert "cannot grant authority" in context["journal_usage"]
    assert next_call("read", "hearth_memory_read", {})[1]["revision"] == 3

    # The pinned journal is the one admission recorded; the run's own entry joins the next.
    assert next_call("journal", "hearth_journal_write", {"text": "Second summary."})[0]
    assert [item["sequence"] for item in pinned_context(hearth, second.id)["journal"]] == [1]
    settle(app, second)
    third, _ = working_run(app, karen, "third")
    assert [item["sequence"] for item in pinned_context(hearth, third.id)["journal"]] == [2, 1]
    assert Journal(hearth).read(karen)["total"] == 2


def test_a_stale_run_save_is_refused_and_the_human_edit_survives(tmp_path):
    app, hearth, karen = manager(tmp_path)
    run, call = working_run(app, karen, "conflict")
    ok, pinned = call("read", "hearth_memory_read", {})
    assert ok and pinned["revision"] == 1
    Memory(hearth).save(
        karen, "Operator wrote this while the run was thinking.", expected_revision=1
    )
    ok, refused = call(
        "save",
        "hearth_memory_save",
        {
            "operation_id": "stale",
            "resident_id": karen,
            "text": "The run would have overwritten the operator.",
            "expected_revision": pinned["revision"],
        },
    )
    assert not ok and refused["error"] == "revision_conflict"
    assert Memory(hearth).read(karen)["revision"] == 2
    assert Memory(hearth).read(karen)["text"].startswith("Operator wrote this")
    # A refused attempt records nothing, so the same operation identity can succeed.
    ok, saved = call(
        "save-merged",
        "hearth_memory_save",
        {
            "operation_id": "stale",
            "resident_id": karen,
            "text": "Operator wrote this while the run was thinking.\nThe run merged its fact.",
            "expected_revision": 2,
        },
    )
    assert ok and saved["revision"] == 3
    assert not call(
        "foreign",
        "hearth_memory_save",
        {
            "operation_id": "foreign",
            "resident_id": "someone-else",
            "text": "Not mine to write.",
            "expected_revision": 3,
        },
    )[0]
    assert Memory(hearth).read(karen)["revision"] == 3
    settle(app, run)


def test_the_tools_are_absent_and_refused_when_memory_is_not_writable(tmp_path):
    app, hearth, karen = manager(tmp_path)
    assert MEMORY_TOOL_NAMES <= {spec["name"] for spec in tool_specs(memory=True)}
    assert not MEMORY_TOOL_NAMES & {spec["name"] for spec in tool_specs()}
    assert tool_specs() != tool_specs(memory=True)

    declaration = hearth.resident(karen).declaration
    assert declaration.memory_writable is True
    hearth.save_resident(karen, replace(declaration, memory_writable=False), expected_revision=1)
    run, call = working_run(app, karen, "unwritable")
    assert pinned_context(hearth, run.id)["memory_writable"] is False
    for tool, arguments in (
        ("hearth_memory_read", {}),
        (
            "hearth_memory_save",
            {
                "operation_id": "denied",
                "resident_id": karen,
                "text": "Never stored.",
                "expected_revision": 1,
            },
        ),
        ("hearth_journal_write", {"text": "Never written."}),
    ):
        ok, refused = call(tool.removeprefix("hearth_"), tool, arguments)
        assert not ok and refused["error"] == "memory_not_writable"
    assert Memory(hearth).read(karen)["revision"] == 1
    assert Journal(hearth).read(karen)["entries"] == []
    # The management tools this run does hold are unaffected.
    assert call("catalog", "hearth_catalog", {"query": ""})[0]


def test_the_pinned_tool_schemas_follow_the_declared_capability(tmp_path, monkeypatch):
    from hearth.integrations.codex import app_server
    from hearth.integrations.codex.management_runtime import pin_configuration

    app, hearth, karen = manager(tmp_path)
    offered = []

    def pins(binary, tools):
        offered.append([spec["name"] for spec in tools])
        return {
            "catalog_sha256": "b" * 64,
            "tools_sha256": json.dumps(offered[-1]).encode().hex()[:64].ljust(64, "0"),
        }

    monkeypatch.setattr(app_server, "configuration_pins", pins, raising=False)
    writable, _ = working_run(app, karen, "writable")
    bound = BoundRun(
        writable.id, writable.owner_token, snapshot(hearth)["epoch"], writable.input_digest
    )
    assert pin_configuration(hearth, bound, tmp_path / "codex") is not None
    assert MEMORY_TOOL_NAMES <= set(offered[0])
    with hearth.database.transaction() as db:
        first = db.execute(
            "SELECT tools_sha256 FROM run_management WHERE run_id=?", (writable.id,)
        ).fetchone()[0]
    settle(app, writable)

    declaration = hearth.resident(karen).declaration
    hearth.save_resident(karen, replace(declaration, memory_writable=False), expected_revision=1)
    plain, _ = working_run(app, karen, "plain")
    bound = BoundRun(plain.id, plain.owner_token, snapshot(hearth)["epoch"], plain.input_digest)
    assert pin_configuration(hearth, bound, tmp_path / "codex") is not None
    assert not MEMORY_TOOL_NAMES & set(offered[1])
    with hearth.database.transaction() as db:
        second = db.execute(
            "SELECT tools_sha256 FROM run_management WHERE run_id=?", (plain.id,)
        ).fetchone()[0]
    assert first != second


def test_a_manager_needs_the_grant_to_provision_a_resident_that_writes_memory(tmp_path):
    app, hearth, karen = manager(tmp_path)
    run, call = working_run(app, karen, "provision")
    request = {
        "operation_id": "reporter",
        "resident": {
            "name": "Reporter",
            "purpose": "Summarize synthetic pears",
            "creation_reason": "Requested through Karen",
            "execution_profile": "codex_subscription",
            "daily_limit": 100000,
            "memory_writable": True,
        },
        "reserve": 10000,
    }
    ok, receipt = call("child", "hearth_residents_provision", request)
    assert ok and receipt["status"] == "ready"
    child = receipt["resident_id"]
    assert hearth.resident(child).declaration.memory_writable is True

    grant = Management(hearth).read(karen)
    policy = {key: value for key, value in grant.items() if key not in {"resident_id", "revision"}}
    Management(hearth).save(
        karen,
        {
            **policy,
            "capabilities": [name for name in policy["capabilities"] if name != "writable_memory"],
            "expected_revision": grant["revision"],
        },
    )
    settle(app, run)
    second, next_call = working_run(app, karen, "provision-again")
    ok, refused = next_call(
        "child",
        "hearth_residents_provision",
        {**request, "operation_id": "second-reporter"},
    )
    assert not ok and refused["error"] == "management_memory_not_permitted"
    assert len(snapshot(hearth)["residents"]) == 2
    # Her own memory follows her own declaration, not the grant she hands to others.
    assert next_call("read", "hearth_memory_read", {})[0]
    settle(app, second)


def enable_grant(hearth, resident_id: str, capabilities: list[str]) -> None:
    """The plain native surface: enabled, this runtime's profile, nothing else."""
    Management(hearth).save(
        resident_id,
        {
            "enabled": True,
            "profiles": ["codex_subscription"],
            "input_set_ids": [],
            "capabilities": capabilities,
            "expected_revision": Management(hearth).read(resident_id)["revision"],
        },
    )


def test_the_tools_reach_a_granted_run_that_manages_nothing(tmp_path):
    app, hearth, _ = manager(tmp_path)
    hearth.save_resident(
        "diarist",
        Declaration("Diarist", "Keep synthetic notes", 1000000, memory_writable=True),
        expected_revision=0,
    )
    enable_grant(hearth, "diarist", [])
    run, call = working_run(app, "diarist", "diary")
    assert pinned_context(hearth, run.id)["memory_writable"] is True
    ok, saved = call(
        "save",
        "hearth_memory_save",
        {
            "operation_id": "first-fact",
            "resident_id": "diarist",
            "text": "The synthetic orchard has pears.",
            "expected_revision": 0,
        },
    )
    assert ok and saved["revision"] == 1 and saved["author"] == "run"
    assert call("journal", "hearth_journal_write", {"text": "Read the orchard notes."})[0]
    assert call("read", "hearth_memory_read", {})[1]["revision"] == 0
    # It manages nothing: every management action is refused, memory and journal are not.
    ok, refused = call(
        "assign",
        "hearth_work_assign",
        {"operation_id": "nope", "resident_id": "diarist", "instruction": "Again", "start": False},
    )
    assert not ok and refused["error"] == "management_capability_not_permitted"
    settle(app, run)
    second, _ = working_run(app, "diarist", "diary-again")
    context = pinned_context(hearth, second.id)
    assert context["memory"]["text"] == "The synthetic orchard has pears."
    assert [item["text"] for item in context["journal"]] == ["Read the orchard notes."]


def test_preserving_a_declared_capability_is_not_an_escalation(tmp_path):
    app, hearth, karen = manager(tmp_path)
    run, call = working_run(app, karen, "provision")
    ok, receipt = call(
        "child",
        "hearth_residents_provision",
        {
            "operation_id": "reporter",
            "resident": {
                "name": "Reporter",
                "purpose": "Summarize synthetic pears",
                "creation_reason": "Requested through Karen",
                "execution_profile": "codex_subscription",
                "daily_limit": 100000,
                "memory_writable": True,
            },
        },
    )
    assert ok
    child = receipt["resident_id"]
    grant = Management(hearth).read(karen)
    policy = {key: value for key, value in grant.items() if key not in {"resident_id", "revision"}}
    Management(hearth).save(
        karen,
        {
            **policy,
            "capabilities": [name for name in policy["capabilities"] if name != "writable_memory"],
            "expected_revision": grant["revision"],
        },
    )
    settle(app, run)
    second, next_call = working_run(app, karen, "configure")
    ok, configuration = next_call("read", "hearth_residents_configuration", {"resident_id": child})
    assert ok
    declaration = json.loads(configuration["text"])["declaration"]
    assert declaration["memory_writable"] is True
    # Echoing back what the read tool returned is the documented preserve-and-edit flow.
    ok, saved = next_call(
        "edit",
        "hearth_residents_configure",
        {
            "resident_id": child,
            "operation_id": "narrow-the-budget",
            "changes": {
                "expected_lifecycle_revision": 1,
                "declaration": {**declaration, "daily_limit": 50000},
            },
        },
    )
    assert ok and saved["revisions"]["declaration"] == 2
    assert hearth.resident(child).declaration.memory_writable is True
    # Raising it from false to true is the escalation the grant still governs.
    hearth.save_resident(
        child,
        replace(hearth.resident(child).declaration, memory_writable=False),
        expected_revision=2,
    )
    ok, refused = next_call(
        "raise",
        "hearth_residents_configure",
        {
            "resident_id": child,
            "operation_id": "raise-the-capability",
            "changes": {
                "expected_lifecycle_revision": 1,
                "declaration": {**declaration, "memory_writable": True},
            },
        },
    )
    assert not ok and refused["error"] == "management_memory_not_permitted"
    assert hearth.resident(child).declaration.memory_writable is False
    settle(app, second)


def test_an_unreadable_pinned_journal_interrupts_only_its_own_run(tmp_path):
    app, hearth, karen = manager(tmp_path)
    hearth.save_resident(
        "other", Declaration("Other", "Summarize synthetic notes", 1000000), expected_revision=0
    )
    first, call = working_run(app, karen, "first")
    assert call("journal", "hearth_journal_write", {"text": "The entry that goes missing."})[0]
    settle(app, first)
    second, _ = working_run(app, karen, "second")
    assert len(pinned_context(hearth, second.id)["journal"]) == 1
    settle(app, second)
    third = app.state.hearth.submit("third", karen, "Work again", expires_at=1788640600)
    third = hearth.admit(third.task_id, reserve=100000)
    task = hearth.submit("unrelated", "other", "Summarize", expires_at=1788640600)
    unrelated = hearth.admit(task.task_id, reserve=1000)
    with hearth.database.transaction(write=True) as db:
        db.execute("DELETE FROM journal_entries WHERE resident_id=?", (karen,))
    results = {run.id: run.status for run in app.state.executor.step()}
    assert results[third.id] == "interrupted"
    assert results[unrelated.id] == "succeeded"


def test_a_pinned_entry_that_changed_under_the_run_is_refused(tmp_path):
    app, hearth, karen = manager(tmp_path)
    first, call = working_run(app, karen, "first")
    assert call("journal", "hearth_journal_write", {"text": "What the next run will read."})[0]
    settle(app, first)
    second, _ = working_run(app, karen, "second")
    assert len(pinned_context(hearth, second.id)["journal"]) == 1
    # The entry keeps its checksum and size but no longer says what the run read.
    with hearth.database.transaction(write=True) as db:
        db.execute("UPDATE journal_entries SET at=? WHERE resident_id=?", (1788650000, karen))
    with pytest.raises(Refused, match="journal_entry_changed"):
        pinned_context(hearth, second.id)
    settle(app, second)


def test_a_pinned_entry_that_retention_archives_is_still_read_back(tmp_path):
    app, hearth, karen = manager(tmp_path)
    policy = Household(hearth).read()
    Household(hearth).save(
        daily_limit=policy["daily_limit"],
        timezone=policy["timezone"],
        resident_limit=policy["resident_limit"],
        concurrency_limit=policy["concurrency_limit"],
        expected_revision=policy["revision"],
        journal_limit=1,
    )
    first, call = working_run(app, karen, "first")
    assert call("journal", "hearth_journal_write", {"text": "The entry that will roll."})[0]
    settle(app, first)
    second, next_call = working_run(app, karen, "second")
    assert pinned_context(hearth, second.id)["journal"][0]["text"] == "The entry that will roll."
    ok, entry = next_call("journal", "hearth_journal_write", {"text": "The entry that stays."})
    assert ok and len(entry["archived"]) == 1
    assert Journal(hearth).read(karen)["total"] == 1
    # The pinned entry now lives only in its immutable file, and reads the same.
    assert pinned_context(hearth, second.id)["journal"] == [
        {
            "sequence": 1,
            "run_id": first.id,
            "at": 1788640000,
            "text": "The entry that will roll.",
        }
    ]
    settle(app, second)


def test_a_reader_without_the_capability_keeps_an_empty_journal_and_no_writable_memory(tmp_path):
    app = create_app(tmp_path, TOKEN, supervise=False, runtime=fake_runtime())
    hearth = app.state.hearth
    hearth.save_resident(
        "reader", Declaration("Reader", "Summarize synthetic notes", 100000), expected_revision=0
    )
    task = hearth.submit("reader-task", "reader", "Summarize", expires_at=int(hearth.clock()) + 600)
    run = hearth.admit(task.task_id, reserve=1000)
    context = pinned_context(hearth, run.id)
    assert context["memory_writable"] is False and context["journal"] == []
    assert app.state.executor.step()[0].status == "succeeded"


def test_a_resident_that_only_remembers_reaches_its_tools_without_management(tmp_path):
    """Writable memory admits a run to the native tools; it grants no management at all."""
    app = create_app(tmp_path, TOKEN, supervise=False, runtime=fake_runtime())
    hearth = app.state.hearth
    hearth.clock = lambda: 1788640000
    hearth.save_resident(
        "reader",
        Declaration("Reader", "Summarize synthetic notes", 100000, memory_writable=True),
        expected_revision=0,
    )
    Memory(hearth).save("reader", "Reader remembers.", expected_revision=0)
    run, call = working_run(app, "reader", "remembers")
    with hearth.database.transaction() as db:
        pin = db.execute("SELECT * FROM run_management WHERE run_id=?", (run.id,)).fetchone()
    assert pin["grant_revision"] is None and pin["grant_sha256"] is None
    # The declared tool set is the memory tools alone; management is not offered.
    assert {spec["name"] for spec in tool_specs(memory=True, management=False)} == MEMORY_TOOL_NAMES
    assert tool_specs(management=False) == []
    ok, pinned = call("read", "hearth_memory_read", {})
    assert ok and pinned["text"] == "Reader remembers."
    assert call("journal", "hearth_journal_write", {"text": "Reader wrote its own entry."})[0]
    ok, refused = call("catalog", "hearth_catalog", {"query": ""})
    assert not ok and refused["error"] == "management_tool_not_permitted"
    ok, refused = call(
        "provision",
        "hearth_residents_provision",
        {"operation_id": "no", "resident": {"name": "N", "purpose": "P", "creation_reason": "C"}},
    )
    assert not ok and refused["error"] == "management_tool_not_permitted"
    # Remembering is not managing, and the run view never reports it as authority.
    assert snapshot(hearth)["runs"][0]["management"] is None
    assert Journal(hearth).read("reader")["total"] == 1
    settle(app, run)


def test_a_resident_that_neither_manages_nor_remembers_is_pinned_no_tools(tmp_path):
    app = create_app(tmp_path, TOKEN, supervise=False, runtime=fake_runtime())
    hearth = app.state.hearth
    hearth.save_resident(
        "reader", Declaration("Reader", "Summarize synthetic notes", 100000), expected_revision=0
    )
    task = hearth.submit("plain", "reader", "Summarize", expires_at=int(hearth.clock()) + 600)
    run = hearth.admit(task.task_id, reserve=1000)
    with hearth.database.transaction() as db:
        assert (
            db.execute("SELECT 1 FROM run_management WHERE run_id=?", (run.id,)).fetchone() is None
        )


def test_revoking_a_grant_mid_run_leaves_memory_and_the_closing_entry_intact(tmp_path):
    """Revoking management takes management away, not the entry that says how work ended."""
    from hearth.management.bridge import authorize

    app, hearth, karen = manager(tmp_path)
    run, call = working_run(app, karen, "revoked")
    assert call("catalog", "hearth_catalog", {"query": ""})[0]

    grant = Management(hearth).read(karen)
    policy = {key: value for key, value in grant.items() if key not in {"resident_id", "revision"}}
    Management(hearth).save(
        karen, {**policy, "enabled": False, "expected_revision": grant["revision"]}
    )
    for tool, arguments in (
        ("hearth_catalog", {"query": ""}),
        (
            "hearth_residents_provision",
            {
                "operation_id": "no",
                "resident": {"name": "N", "purpose": "P", "creation_reason": "C"},
            },
        ),
    ):
        ok, refused = call(tool.removeprefix("hearth_"), tool, arguments)
        assert not ok and refused["error"] == "management_grant_changed_or_revoked"

    ok, pinned = call("read", "hearth_memory_read", {})
    assert ok and pinned["revision"] == 1
    ok, saved = call(
        "save",
        "hearth_memory_save",
        {
            "operation_id": "after-revocation",
            "resident_id": karen,
            "text": "The operator withdrew management while this run worked.",
            "expected_revision": 1,
        },
    )
    assert ok and saved["author"] == "run"
    ok, entry = call("journal", "hearth_journal_write", {"text": "Ended unclear: grant revoked."})
    assert ok and entry["sequence"] == 1
    # The run is not torn down: the worker's liveness check still authorizes it.
    with hearth.database.transaction() as db:
        authority = authorize(db, run_bound(app, hearth, run), int(hearth.clock()))
    assert authority["management_revoked"] and authority["memory_writable"] is True
    assert authority["grant"]["capabilities"] == []
    settle(app, run)


def run_bound(app, hearth, run) -> BoundRun:
    return BoundRun(run.id, run.owner_token, snapshot(hearth)["epoch"], run.input_digest)
