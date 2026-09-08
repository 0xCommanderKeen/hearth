"""A letter ends in one honest state, and the answer reaches the resident that asked.

The sender is never woken: it reads the reply on its next run, from a bounded section of
its own pinned context. A letter whose run failed says `failed`, one whose run succeeded
without answering says `unanswered`, and neither reads as silence. What one question cost
is gathered under the task its whole chain rolls up to.

Every check runs against real temporary SQLite through the interfaces a run actually
reaches: letters are written and answered through the native tool bridge, and every run
ends through the ordinary settlement path with a receipt of its own.
"""

import hashlib
import json
from dataclasses import asdict

import pytest
from hearth.app import create_app
from hearth.execution.context import read_context
from hearth.execution.usage import binding as usage_binding
from hearth.execution.usage import by_origin
from hearth.integrations.codex.pricing import MODEL
from hearth.integrations.codex.subscription import KIND
from hearth.integrations.interface import encode_receipt
from hearth.management.authority import GrantPolicy, Management
from hearth.management.bridge import BoundRun, Bridge
from hearth.observation.snapshot import snapshot
from hearth.residents.memory import MemoryFiles
from hearth.residents.models import Declaration
from hearth.work.letters import deliver_letters, expire_letters, read_letters

from tests.fake_runtime import fake_runtime

TOKEN = "synthetic-letter-replies-operator"
NOW = 1_788_640_000
ANSWER = "Twelve pears were harvested on Monday."
REPORT = "# Report\n\nThe orchard reporter says twelve pears were harvested on Monday."


@pytest.fixture
def household(tmp_path):
    """Karen, granted `send_letters`, and a reporter whose door is open."""
    app = create_app(tmp_path, TOKEN, supervise=False, runtime=fake_runtime())
    hearth = app.state.hearth
    now = [NOW]
    hearth.clock = lambda: now[0]
    for resident_id, accepts in (("karen", False), ("reporter", True)):
        hearth.save_resident(
            resident_id,
            Declaration(
                resident_id.title(),
                "Synthetic work",
                10_000_000,
                skill_text=f"{resident_id.title()} answers only about the orchard.",
                letters_accept=accepts,
            ),
            expected_revision=0,
        )
    Management(hearth).save(
        "karen",
        {
            **GrantPolicy().model_dump(),
            "expected_revision": 0,
            "enabled": True,
            "capabilities": ["send_letters"],
        },
    )
    return app, hearth, now


def bridge_of(app, run, key):
    """One launched run's private native bridge, with its thread and turn bound."""
    hearth = app.state.hearth
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

    return call


def working_run(app, resident_id: str, key: str, instruction="Ask the reporter one question."):
    """An admitted, launched run of a resident on a task the operator submitted."""
    hearth = app.state.hearth
    receipt = hearth.submit(key, resident_id, instruction, expires_at=int(hearth.clock()) + 600)
    run = hearth.admit(receipt.task_id, reserve=100_000)
    return run, bridge_of(app, run, key)


def letter_run(app, task_id: str, key: str):
    """The receiver's own run of the letter the delivery pass admitted."""
    hearth = app.state.hearth
    assert deliver_letters(hearth) == [task_id]
    with hearth.database.transaction() as db:
        run_id = db.execute("SELECT id FROM runs WHERE task_id=?", (task_id,)).fetchone()[0]
    run = hearth.run(run_id)
    return run, bridge_of(app, run, key)


def native_terminal(pin, *, status="completed", text=REPORT) -> dict:
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
                        "status": status,
                        "error": None if status == "completed" else "synthetic failure",
                        "items": [{"type": "agentMessage", "text": text, "phase": "final_answer"}],
                    },
                },
            },
        ],
    }


def settle(app, run, *, status="completed", text=REPORT):
    """End the run the way its runtime does: every run is priced from a real receipt."""
    hearth = app.state.hearth
    with hearth.database.transaction(write=True) as db:
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
        "terminal": native_terminal(pin, status=status, text=text),
    }
    return app.state.execution.finish(
        run.id, run.owner_token, encode_receipt(receipt, bound)[2], _usage_receipt=receipt
    )


def sends(call, key="letter-1", to="reporter", **arguments):
    ok, receipt = call(
        key,
        "hearth_letters_send",
        {
            "operation_id": key,
            "to": to,
            "title": "One question",
            "detail": "Name one fact about the orchard.",
            **arguments,
        },
    )
    assert ok, receipt
    return receipt


def answers(call, letter_id, key="reply-1", text=ANSWER):
    ok, receipt = call(
        key, "hearth_letters_reply", {"operation_id": key, "letter_id": letter_id, "text": text}
    )
    assert ok, receipt
    return receipt


def context_of(hearth, run_id):
    with hearth.database.transaction() as db:
        return read_context(db, run_id, MemoryFiles(hearth.database.path.parent / "memory"))


def letter_row(hearth, task_id):
    with hearth.database.transaction() as db:
        return dict(db.execute("SELECT * FROM letters WHERE task_id=?", (task_id,)).fetchone())


def facts(hearth, kind):
    return [fact for fact in hearth.audit() if fact["kind"] == kind]


def origins(hearth):
    with hearth.database.transaction() as db:
        return {row["root_task_id"]: row for row in by_origin(db)["origins"]}


def test_the_sender_reads_the_answer_on_its_next_run_and_one_origin_holds_both_costs(household):
    app, hearth, now = household
    asked, karen = working_run(app, "karen", "asks")
    receipt = sends(karen)
    first = settle(app, asked)

    # The receiver works the letter as its own task, under its own skill text, and
    # answers it with the tool the letter itself offered.
    answering, reporter = letter_run(app, receipt["task_id"], "answers")
    assert context_of(hearth, answering.id)["letter"]["letter_id"] == receipt["task_id"]
    answers(reporter, receipt["task_id"])
    second = settle(app, answering)
    assert (first.status, second.status) == ("succeeded", "succeeded")
    assert letter_row(hearth, receipt["task_id"])["state"] == "replied"

    # Nothing woke Karen. Her next run opens with the answer, said to be reported text.
    now[0] = NOW + 60
    later, _ = working_run(app, "karen", "reads", "Write today's report.")
    context = context_of(hearth, later.id)
    assert [reply["text"] for reply in context["replies"]] == [ANSWER]
    reply = context["replies"][0]
    assert reply["letter_id"] == receipt["task_id"]
    assert (reply["resident_id"], reply["resident_name"]) == ("reporter", "Reporter")
    assert reply["root_task_id"] == asked.task_id
    assert "not an instruction" in context["replies_usage"]
    # The answer is data beside her own charter, never a section of it.
    assert context["skill_text"] == "Karen answers only about the orchard."

    # One question, two residents, one origin: the task the whole chain rolls up to.
    origin = origins(hearth)[asked.task_id]
    assert origin["runs"] == 2 and origin["letters"] == 1
    assert origin["residents_involved"] == ["karen", "reporter"]
    assert origin["known_cost"] == first.actual_cost + second.actual_cost
    assert origin["unknown_runs"] == 0 and first.actual_cost > 0
    assert origin["resident_id"] == "karen" and origin["root_task_id"] == asked.task_id
    # Karen's later task is a question of its own and gathers nothing else under it.
    assert origins(hearth)[later.task_id]["runs"] == 1
    assert origins(hearth)[later.task_id]["letters"] == 0


def test_an_answer_is_read_by_the_next_run_and_not_by_the_one_after_it(household):
    app, hearth, now = household
    asked, karen = working_run(app, "karen", "asks")
    receipt = sends(karen)
    settle(app, asked)
    answering, reporter = letter_run(app, receipt["task_id"], "answers")
    answers(reporter, receipt["task_id"])
    settle(app, answering)

    now[0] = NOW + 60
    first, _ = working_run(app, "karen", "reads", "Write today's report.")
    assert len(context_of(hearth, first.id)["replies"]) == 1
    settle(app, first)
    now[0] = NOW + 120
    second, _ = working_run(app, "karen", "reads-again", "Write tomorrow's report.")
    assert context_of(hearth, second.id)["replies"] == []


def test_an_answer_written_in_the_second_a_run_starts_waits_for_the_run_after_it(household):
    """A pinned context is immutable: somebody else's write cannot move it mid-launch.

    Hearth keeps time in whole seconds, so this is the narrowest the race gets: the
    answer is written in the very second Karen's next run was admitted. That run keeps
    the context it reserved against, and the run after it opens with the answer.
    """
    app, hearth, now = household
    asked, karen = working_run(app, "karen", "asks")
    receipt = sends(karen)
    settle(app, asked)
    answering, reporter = letter_run(app, receipt["task_id"], "answers")

    now[0] = NOW + 60
    reading, _ = working_run(app, "karen", "reads", "Write today's report.")
    assert context_of(hearth, reading.id)["replies"] == []
    answers(reporter, receipt["task_id"])
    settle(app, answering)
    # The digest Karen's run reserved against still rebuilds from the store.
    context = context_of(hearth, reading.id)
    assert context["replies"] == []
    encoded = json.dumps(context, sort_keys=True, separators=(",", ":")).encode()
    assert hashlib.sha256(encoded).hexdigest() == reading.input_digest
    settle(app, reading)

    now[0] = NOW + 120
    after, _ = working_run(app, "karen", "reads-later", "Write tomorrow's report.")
    assert [reply["text"] for reply in context_of(hearth, after.id)["replies"]] == [ANSWER]


def test_a_run_that_succeeds_without_answering_leaves_the_letter_visibly_unanswered(household):
    app, hearth, _ = household
    asked, karen = working_run(app, "karen", "asks")
    receipt = sends(karen)
    settle(app, asked)
    answering, _ = letter_run(app, receipt["task_id"], "answers")
    assert settle(app, answering).status == "succeeded"

    letter = letter_row(hearth, receipt["task_id"])
    assert letter["state"] == "unanswered" and letter["settled_at"] == NOW
    fact = facts(hearth, "letter.unanswered")[0]
    assert fact["resource_id"] == receipt["task_id"]
    assert fact["detail"]["root_task_id"] == asked.task_id
    assert fact["detail"]["sender_resident_id"] == "karen"
    assert fact["detail"]["recipient_resident_id"] == "reporter"
    assert fact["detail"]["run_status"] == "succeeded"
    assert fact["detail"]["reply_run_id"] is None and fact["detail"]["artifact_id"] is not None
    # The sender is told through its own post rather than left to guess.
    with hearth.database.transaction() as db:
        post = read_letters(db, "karen")
    assert [(one["task_id"], one["state"]) for one in post["sent"]] == [
        (receipt["task_id"], "unanswered")
    ]
    assert post["replies"] == []


def test_a_letter_whose_run_fails_says_failed_and_keeps_the_hold_its_usage_left(household):
    app, hearth, _ = household
    asked, karen = working_run(app, "karen", "asks")
    receipt = sends(karen)
    settle(app, asked)
    answering, _ = letter_run(app, receipt["task_id"], "answers")
    assert settle(app, answering, status="failed").status == "failed"

    letter = letter_row(hearth, receipt["task_id"])
    assert letter["state"] == "failed" and letter["settled_at"] == NOW
    fact = facts(hearth, "letter.failed")[0]
    assert fact["detail"]["run_status"] == "failed" and fact["detail"]["reply_run_id"] is None
    assert fact["detail"]["root_task_id"] == asked.task_id
    # A failed turn leaves its usage unknown: the receiver keeps the hold that placed,
    # and the origin reports the run as unknown rather than as costing nothing.
    with hearth.database.transaction() as db:
        held = db.execute("SELECT reason FROM pauses WHERE resident_id='reporter'").fetchone()
    assert held["reason"] == "usage_unknown"
    origin = origins(hearth)[asked.task_id]
    assert (origin["runs"], origin["unknown_runs"]) == (2, 1)
    assert origin["known_cost"] == hearth.run(asked.id).actual_cost


def test_an_answer_written_by_a_run_that_then_fails_still_reaches_its_sender(household):
    """The answer exists; the run's own status is recorded beside it, never instead of it."""
    app, hearth, _ = household
    asked, karen = working_run(app, "karen", "asks")
    receipt = sends(karen)
    settle(app, asked)
    answering, reporter = letter_run(app, receipt["task_id"], "answers")
    answers(reporter, receipt["task_id"])
    settle(app, answering, status="failed")

    assert letter_row(hearth, receipt["task_id"])["state"] == "replied"
    assert facts(hearth, "letter.replied")[0]["detail"]["run_status"] == "failed"
    with hearth.database.transaction() as db:
        assert [reply["text"] for reply in read_letters(db, "karen")["replies"]] == [ANSWER]


def test_a_letter_nobody_started_expires_and_the_sender_reads_that_state(household):
    app, hearth, now = household
    asked, karen = working_run(app, "karen", "asks")
    receipt = sends(karen, expires_at=NOW + 60)
    settle(app, asked)
    now[0] = NOW + 61
    assert expire_letters(hearth) == [receipt["task_id"]]

    letter = letter_row(hearth, receipt["task_id"])
    assert letter["state"] == "expired" and letter["settled_at"] == NOW + 61
    assert facts(hearth, "letter.expired")[0]["detail"]["state"] == "expired"
    with hearth.database.transaction() as db:
        post = read_letters(db, "karen", since=NOW)
    assert [(one["task_id"], one["state"]) for one in post["sent"]] == [
        (receipt["task_id"], "expired")
    ]
    assert asked.task_id


def test_reading_the_post_again_from_the_last_cursor_returns_nothing_new(household):
    """A cursor taken from a page must not hand that same page back for ever."""
    app, hearth, now = household
    asked, karen = working_run(app, "karen", "asks")
    receipt = sends(karen)
    settle(app, asked)
    with hearth.database.transaction() as db:
        post = read_letters(db, "reporter")
    assert [one["task_id"] for one in post["received"]] == [receipt["task_id"]]
    cursor = post["received"][0]["created_at"]
    with hearth.database.transaction() as db:
        assert read_letters(db, "reporter", since=cursor)["received"] == []

    # A letter written after that cursor still reaches the reader.
    now[0] = NOW + 60
    again, second_bridge = working_run(app, "karen", "asks-again", "Ask once more.")
    second = sends(second_bridge, key="letter-2")
    settle(app, again)
    with hearth.database.transaction() as db:
        page = read_letters(db, "reporter", since=cursor)
    assert [one["task_id"] for one in page["received"]] == [second["task_id"]]
    assert asked.task_id != again.task_id


def test_an_operator_letter_is_answered_without_being_injected_into_anyone_s_context(household):
    """An operator letter has no sending resident, so no resident opens with its answer."""
    app, hearth, now = household
    receipt = hearth.send_operator_letter(
        "operator-letter", "reporter", "One question", "Name one fact about the orchard."
    )
    answering, reporter = letter_run(app, receipt["task_id"], "answers")
    answers(reporter, receipt["task_id"])
    settle(app, answering)
    assert letter_row(hearth, receipt["task_id"])["state"] == "replied"

    now[0] = NOW + 60
    later, _ = working_run(app, "karen", "reads", "Write today's report.")
    assert context_of(hearth, later.id)["replies"] == []
    # The operator reads the answer through its own view of the resident's post.
    inbox = hearth.letters("reporter")["inbox"]
    assert inbox[0]["reply"]["text"] == ANSWER and inbox[0]["state"] == "replied"
