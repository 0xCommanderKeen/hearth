"""Letters written, read and answered from inside a run.

A resident is never shown a tool it may not use: writing needs the grant the run was
admitted with, and answering exists only in the run working the letter. Every check runs
against real temporary SQLite through the tools the run actually reaches.
"""

import json

import pytest
from hearth.app import create_app
from hearth.management.bootstrap import bootstrap
from hearth.management.bridge import BoundRun, Bridge
from hearth.management.tools import tool_specs
from hearth.observation.snapshot import snapshot
from hearth.residents.models import Declaration

from tests.fake_runtime import fake_runtime

TOKEN = "synthetic-letter-tools-operator"
NOW = 1_788_640_000
LETTER_TOOLS = {"hearth_letters_send", "hearth_letters_read", "hearth_letters_reply"}


@pytest.fixture
def household(tmp_path):
    """Karen, granted `send_letters` by her own setup, and a reporter with an open door."""
    app = create_app(tmp_path, TOKEN, supervise=False, runtime=fake_runtime())
    hearth = app.state.hearth
    hearth.clock = lambda: NOW
    karen = bootstrap(hearth)["resident_id"]
    hearth.save_resident(
        "reporter",
        Declaration("Reporter", "Answers one question about the orchard", 1_000_000),
        expected_revision=0,
    )
    return app, hearth, karen


def opens_the_door(hearth, resident_id="reporter"):
    resident = hearth.resident(resident_id)
    from dataclasses import replace

    hearth.save_resident(
        resident_id,
        replace(resident.declaration, letters_accept=True),
        expected_revision=resident.revision,
    )


def native(app, run, key):
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


def working_run(app, resident_id: str, key: str):
    """An admitted, launched run of a resident on a task the operator submitted."""
    hearth = app.state.hearth
    task = hearth.submit(key, resident_id, "Synthetic work", expires_at=int(hearth.clock()) + 600)
    run = hearth.admit(task.task_id, reserve=100_000)
    return run, native(app, run, key)


def letter_run(app, task_id: str, key: str):
    """The receiver's run of a letter it was sent."""
    run = app.state.hearth.admit(task_id, reserve=100_000)
    return run, native(app, run, key)


def settle(hearth, run):
    """End a run the way the executor would, so its resident can be admitted again."""
    with hearth.database.transaction(write=True) as db:
        db.execute(
            "UPDATE runs SET status='succeeded',finished_at=?,actual_cost=0,usage_known=1 "
            "WHERE id=?",
            (int(hearth.clock()), run.id),
        )
        db.execute("UPDATE tasks SET status='succeeded' WHERE id=?", (run.task_id,))


def send(call, to="reporter", *, operation_id="letter-1", title="One question", **arguments):
    return call(
        operation_id,
        "hearth_letters_send",
        {
            "operation_id": operation_id,
            "to": to,
            "title": title,
            "detail": "Name one fact about the orchard.",
            **arguments,
        },
    )


def offered(hearth, run, tmp_path, monkeypatch):
    """The exact tool schemas this run would be launched with, as its admission pins them."""
    from hearth.integrations.codex import app_server
    from hearth.integrations.codex.management_runtime import pin_configuration

    seen: list[list[str]] = []

    def pins(binary, tools):
        seen.append([spec["name"] for spec in tools])
        return {
            "catalog_sha256": "b" * 64,
            "tools_sha256": json.dumps(seen[-1]).encode().hex()[:64].ljust(64, "0"),
        }

    monkeypatch.setattr(app_server, "configuration_pins", pins, raising=False)
    bound = BoundRun(run.id, run.owner_token, snapshot(hearth)["epoch"], run.input_digest)
    assert pin_configuration(hearth, bound, tmp_path / "codex") is not None
    return set(seen[-1])


def test_each_run_is_offered_exactly_the_letter_tools_it_may_use(household, tmp_path, monkeypatch):
    app, hearth, karen = household
    opens_the_door(hearth)
    # The declared set is a function of the two ends, never of the resident alone.
    assert LETTER_TOOLS & {spec["name"] for spec in tool_specs()} == set()
    granted = {spec["name"] for spec in tool_specs(send_letters=True, read_post=True)}
    assert granted >= {"hearth_letters_send", "hearth_letters_read"}
    assert "hearth_letters_reply" not in granted
    assert {
        spec["name"] for spec in tool_specs(management=False, reply_letter=True, read_post=True)
    } == {"hearth_letters_reply", "hearth_letters_read"}
    # Reading one's own post needs neither a grant nor a letter in hand right now.
    assert {spec["name"] for spec in tool_specs(management=False, read_post=True)} == {
        "hearth_letters_read"
    }

    writer, call = working_run(app, karen, "writes")
    assert "hearth_letters_send" in offered(hearth, writer, tmp_path, monkeypatch)
    assert "hearth_letters_reply" not in offered(hearth, writer, tmp_path, monkeypatch)
    ok, receipt = send(call)
    assert ok, receipt
    settle(hearth, writer)

    # The receiver holds no grant at all and is still offered the answer it owes.
    reader, _ = letter_run(app, receipt["task_id"], "answers")
    assert offered(hearth, reader, tmp_path, monkeypatch) == {
        "hearth_letters_reply",
        "hearth_letters_read",
    }
    settle(hearth, reader)


def test_a_resident_without_the_capability_is_offered_no_letter_tool(
    household, tmp_path, monkeypatch
):
    app, hearth, karen = household
    opens_the_door(hearth)
    from hearth.management.authority import GrantPolicy, Management

    Management(hearth).save(
        karen,
        {
            **GrantPolicy().model_dump(),
            "expected_revision": 1,
            "enabled": True,
            "profiles": ["codex_subscription"],
            "capabilities": ["assign_work"],
        },
    )
    run, call = working_run(app, karen, "ungranted")
    assert not LETTER_TOOLS & offered(hearth, run, tmp_path, monkeypatch)
    # Calling it anyway is refused, and nothing is queued for the recipient.
    ok, refusal = send(call)
    assert not ok and refusal["error"] == "letters_not_permitted"
    with hearth.database.transaction() as db:
        assert db.execute("SELECT count(*) FROM letters").fetchone()[0] == 0


def test_a_receiver_only_resident_is_offered_its_own_post_on_an_ordinary_run(
    household, tmp_path, monkeypatch
):
    """A resident that has only ever been written to reads its post outside a letter run.

    It holds no management grant and cannot write its own memory, so the only thing that
    can carry `hearth_letters_read` to it is a pin taken for the post itself.
    """
    app, hearth, karen = household
    opens_the_door(hearth)
    writer, write = working_run(app, karen, "asks")
    receipt = send(write)[1]
    settle(hearth, writer)
    reader, answer = letter_run(app, receipt["task_id"], "answers")
    answer(
        "reply",
        "hearth_letters_reply",
        {"operation_id": "answer-1", "letter_id": receipt["task_id"], "text": "412 pear trees."},
    )
    settle(hearth, reader)

    # An ordinary task of its own, with no grant, no writable memory and no letter in hand.
    later, read = working_run(app, "reporter", "ordinary")
    with hearth.database.transaction() as db:
        from hearth.work.letters import run_letter_scope

        scope = run_letter_scope(db, later.id, NOW)
        assert scope["post"] is True and scope["send"] is False and scope["reply"] is False
        assert (
            db.execute("SELECT 1 FROM run_management WHERE run_id=?", (later.id,)).fetchone()
            is not None
        )
    assert offered(hearth, later, tmp_path, monkeypatch) == {"hearth_letters_read"}
    ok, post = read("post", "hearth_letters_read", {})
    assert ok and [item["title"] for item in post["received"]] == ["One question"]
    settle(hearth, later)

    # A copy of that store validates: a grantless pin taken for the post is a healthy pin.
    from hearth.management.authority import validate_management

    with hearth.database.transaction() as db:
        validate_management(db)


def test_the_letter_call_is_recorded_as_this_run_s_tool_evidence(household):
    app, hearth, karen = household
    opens_the_door(hearth)
    run, call = working_run(app, karen, "evidence")
    ok, receipt = send(call)
    assert ok and receipt["resident_id"] == "reporter" and receipt["status"] == "queued"
    assert receipt["sender_resident_id"] == karen and receipt["depth"] == 1
    assert receipt["originating_run_id"] == run.id
    sent = [item for item in snapshot(hearth)["runs"] if item["id"] == run.id][0]
    assert sent["management"]["calls"] == 1
    facts = {fact["kind"]: fact for fact in hearth.audit()}
    assert facts["management.tool_completed"]["resource_id"] == run.id
    assert facts["letter.sent"]["resource_id"] == receipt["task_id"]
    # The same operation replays its receipt; nothing is queued twice.
    assert call(
        "again",
        "hearth_letters_send",
        {
            "operation_id": "letter-1",
            "to": "reporter",
            "title": "One question",
            "detail": "Name one fact about the orchard.",
        },
    ) == (True, receipt)
    with hearth.database.transaction() as db:
        assert db.execute("SELECT count(*) FROM letters").fetchone()[0] == 1


def test_a_full_page_of_post_says_so_and_the_older_one_is_a_page_further_back(household):
    app, hearth, karen = household
    opens_the_door(hearth)
    writer, write = working_run(app, karen, "asks")
    first = send(write)[1]
    second = send(write, operation_id="letter-2", title="Another question")[1]
    settle(hearth, writer)

    reader, read = letter_run(app, first["task_id"], "reads")
    ok, page = read("one", "hearth_letters_read", {"limit": 1})
    assert ok and page["received_truncated"] and len(page["received"]) == 1
    older = read("two", "hearth_letters_read", {"limit": 1, "offset": 1})[1]
    assert not older["received_truncated"]
    assert {page["received"][0]["task_id"], older["received"][0]["task_id"]} == {
        first["task_id"],
        second["task_id"],
    }
    settle(hearth, reader)


def test_the_letter_is_answered_once_and_the_sender_reads_the_reply(household):
    app, hearth, karen = household
    opens_the_door(hearth)
    writer, write = working_run(app, karen, "asks")
    receipt = send(write)[1]
    settle(hearth, writer)

    reader, answer = letter_run(app, receipt["task_id"], "answers")
    # The receiver reads the letter it was handed before answering it.
    ok, post = answer("read", "hearth_letters_read", {})
    assert ok and [item["task_id"] for item in post["received"]] == [receipt["task_id"]]
    assert post["received"][0]["sender"] == karen and post["received"][0]["reply"] is None
    assert post["replies"] == []
    ok, reply = answer(
        "reply",
        "hearth_letters_reply",
        {
            "operation_id": "answer-1",
            "letter_id": receipt["task_id"],
            "text": "The orchard has 412 pear trees.",
        },
    )
    assert ok and reply["status"] == "answered" and reply["recipient_resident_id"] == karen
    # An uncertain retry recovers the answer it already wrote.
    assert answer(
        "reply-again",
        "hearth_letters_reply",
        {
            "operation_id": "answer-1",
            "letter_id": receipt["task_id"],
            "text": "The orchard has 412 pear trees.",
        },
    ) == (True, reply)
    # A second, different answer would leave the sender two and no way to choose.
    ok, refusal = answer(
        "reply-twice",
        "hearth_letters_reply",
        {
            "operation_id": "answer-2",
            "letter_id": receipt["task_id"],
            "text": "On reflection, 411.",
        },
    )
    assert not ok and refusal["error"] == "letter_already_answered"
    settle(hearth, reader)

    later, read = working_run(app, karen, "reads")
    ok, post = read("post", "hearth_letters_read", {})
    assert ok and post["received"] == []
    assert [item["text"] for item in post["replies"]] == ["The orchard has 412 pear trees."]
    assert post["replies"][0]["letter_id"] == receipt["task_id"]
    assert post["replies"][0]["resident_id"] == "reporter"
    # Nothing newer than the reply is anything at all.
    assert read("since", "hearth_letters_read", {"since": NOW + 1})[1]["replies"] == []
    settle(hearth, later)


def test_narrowing_the_grant_stops_the_next_letter_and_not_the_answer_to_the_last(
    household, tmp_path, monkeypatch
):
    """An operator who takes `send_letters` away must not hide an answer already asked for."""
    from hearth.management.authority import GrantPolicy, Management

    app, hearth, karen = household
    opens_the_door(hearth)
    writer, write = working_run(app, karen, "asks")
    receipt = send(write)[1]
    settle(hearth, writer)
    reader, answer = letter_run(app, receipt["task_id"], "answers")
    answer(
        "reply",
        "hearth_letters_reply",
        {"operation_id": "answer-1", "letter_id": receipt["task_id"], "text": "412 pear trees."},
    )
    settle(hearth, reader)

    Management(hearth).save(
        karen,
        {
            **GrantPolicy().model_dump(),
            "expected_revision": 1,
            "enabled": True,
            "profiles": ["codex_subscription"],
            "capabilities": ["assign_work"],
        },
    )
    later, read = working_run(app, karen, "reads")
    tools = offered(hearth, later, tmp_path, monkeypatch)
    assert "hearth_letters_send" not in tools and "hearth_letters_read" in tools
    ok, post = read("post", "hearth_letters_read", {})
    assert ok and [item["text"] for item in post["replies"]] == ["412 pear trees."]
    settle(hearth, later)


def test_a_grant_revoked_mid_run_takes_the_management_tools_and_not_the_answer(household):
    """A resident asked a question must still be able to answer it."""
    from hearth.management.authority import GrantPolicy, Management

    app, hearth, karen = household
    opens_the_door(hearth)
    Management(hearth).save(
        "reporter",
        {
            **GrantPolicy().model_dump(),
            "expected_revision": 0,
            "enabled": True,
            "profiles": ["codex_subscription"],
            "capabilities": ["assign_work"],
        },
    )
    writer, write = working_run(app, karen, "asks")
    receipt = send(write)[1]
    settle(hearth, writer)

    reader, answer = letter_run(app, receipt["task_id"], "answers")
    Management(hearth).save(
        "reporter", {**GrantPolicy().model_dump(), "expected_revision": 1, "enabled": False}
    )
    ok, refusal = answer("catalog", "hearth_catalog", {"query": ""})
    assert not ok and refusal["error"] == "management_grant_changed_or_revoked"
    ok, reply = answer(
        "reply",
        "hearth_letters_reply",
        {
            "operation_id": "answer-1",
            "letter_id": receipt["task_id"],
            "text": "The orchard has 412 pear trees.",
        },
    )
    assert ok and reply["status"] == "answered"
    # Writing to a colleague was the part that needed the grant, and it is gone.
    assert answer("post", "hearth_letters_read", {})[0]
    ok, refusal = send(answer, karen, operation_id="letter-back")
    assert not ok and refusal["error"] == "management_grant_changed_or_revoked"
    settle(hearth, reader)


def test_a_run_answers_the_letter_it_works_and_no_other(household):
    app, hearth, karen = household
    opens_the_door(hearth)
    writer, write = working_run(app, karen, "asks")
    first = send(write)[1]
    second = send(write, operation_id="letter-2", title="Another question")[1]
    settle(hearth, writer)

    # A run that is working no letter is not offered the tool and is refused it.
    later, read = working_run(app, karen, "not-a-letter")
    ok, refusal = read(
        "reply",
        "hearth_letters_reply",
        {"operation_id": "answer-0", "letter_id": first["task_id"], "text": "Not mine to write."},
    )
    assert not ok and refusal["error"] == "management_tool_not_permitted"
    settle(hearth, later)

    reader, answer = letter_run(app, first["task_id"], "answers")
    ok, refusal = answer(
        "wrong",
        "hearth_letters_reply",
        {"operation_id": "answer-1", "letter_id": second["task_id"], "text": "Wrong letter."},
    )
    assert not ok and refusal["error"] == "letter_not_addressed_here"
    ok, refusal = answer(
        "missing",
        "hearth_letters_reply",
        {"operation_id": "answer-2", "letter_id": "no-such-letter", "text": "Nobody's letter."},
    )
    assert not ok and refusal["error"] == "letter_not_found"
    with hearth.database.transaction() as db:
        assert db.execute("SELECT count(*) FROM letter_replies").fetchone()[0] == 0
    settle(hearth, reader)


def test_a_sender_reads_its_own_letters_and_the_same_cursor_twice_says_nothing_new(household):
    """The resident that asked can see what it asked and what came of it, without guessing."""
    app, hearth, karen = household
    opens_the_door(hearth)
    writer, write = working_run(app, karen, "asks")
    receipt = send(write)[1]
    ok, post = write("post", "hearth_letters_read", {})
    assert ok and [item["task_id"] for item in post["sent"]] == [receipt["task_id"]]
    written = post["sent"][0]
    assert written["state"] == "pending" and written["settled_at"] is None
    assert written["recipient_resident_id"] == "reporter" and written["status"] == "queued"
    assert post["received"] == [] and post["sent_truncated"] is False
    # A cursor taken from that page hands the same page back no more.
    assert write("again", "hearth_letters_read", {"since": written["created_at"]})[1]["sent"] == []
    settle(hearth, writer)
