"""What the operator's snapshot says about the post.

Every fact here is read from rows the letters machinery already wrote: a letter, its
answer, the chain it belongs to, and the tool evidence of a run that was refused one.
Nothing is inferred, so a village that draws from this snapshot draws only what
happened. Each check runs against a real temporary SQLite store through the interfaces
that own those rows.
"""

import json
from dataclasses import replace

import pytest
from hearth.app import create_app
from hearth.management.bootstrap import bootstrap
from hearth.management.bridge import BoundRun, Bridge
from hearth.observation.snapshot import snapshot
from hearth.residents.models import Declaration

from tests.fake_runtime import fake_runtime

TOKEN = "synthetic-letter-observation-operator"
NOW = 1_788_640_000


@pytest.fixture
def household(tmp_path):
    """Karen, granted `send_letters` by her own setup, and a reporter with a shut door."""
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


def working_run(app, resident_id: str, key: str, instruction="Answer the orchard question"):
    hearth = app.state.hearth
    task = hearth.submit(key, resident_id, instruction, expires_at=int(hearth.clock()) + 600)
    run = hearth.admit(task.task_id, reserve=100_000)
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


def send(call, to="reporter", *, operation_id="letter-1", **arguments):
    return call(
        operation_id,
        "hearth_letters_send",
        {
            "operation_id": operation_id,
            "to": to,
            "title": "One question",
            "detail": "Name one fact about the orchard.",
            **arguments,
        },
    )


def test_the_snapshot_names_both_ends_of_every_letter_and_every_answer(household):
    app, hearth, karen = household
    opens_the_door(hearth)
    writer, write = working_run(app, karen, "asks")
    ok, receipt = send(write)
    assert ok, receipt
    settle(hearth, writer)

    sent = snapshot(hearth)["letters"]
    assert [event["kind"] for event in sent] == ["letter_sent"]
    assert sent[0]["from_resident_id"] == karen
    assert sent[0]["to_resident_id"] == "reporter"
    assert sent[0]["task_id"] == receipt["task_id"]
    assert sent[0]["title"] == "One question" and sent[0]["state"] == "pending"

    reader = hearth.admit(receipt["task_id"], reserve=100_000)
    answer = native(app, reader, "answers")
    ok, written = answer(
        "reply",
        "hearth_letters_reply",
        {
            "operation_id": "answer-1",
            "letter_id": receipt["task_id"],
            "text": "The pear harvest was logged under OR-C9IX-BBTI.",
        },
    )
    assert ok, written

    events = snapshot(hearth)["letters"]
    assert [event["kind"] for event in events] == ["letter_replied", "letter_sent"]
    # The answer walks back the way the letter came: the receiver wrote it, the sender
    # reads it, and both ends are named so a village knows whose door to walk to.
    assert events[0]["from_resident_id"] == "reporter"
    assert events[0]["to_resident_id"] == karen
    assert events[0]["task_id"] == receipt["task_id"]


def test_an_operator_letter_names_no_villager_at_its_own_end(household):
    app, hearth, _ = household
    opens_the_door(hearth)
    receipt = hearth.send_operator_letter(
        "operator-post-1", "reporter", "One question", "Name one fact about the orchard."
    )
    event = snapshot(hearth)["letters"][0]
    assert event["kind"] == "letter_sent" and event["task_id"] == receipt["task_id"]
    # There is no home to start the walk from; the operator is not a resident.
    assert event["from_resident_id"] is None
    assert event["to_resident_id"] == "reporter"


def test_a_letter_carries_its_chain_root_first_and_an_ordinary_task_carries_none(household):
    app, hearth, karen = household
    opens_the_door(hearth)
    writer, write = working_run(app, karen, "asks")
    ok, receipt = send(write)
    assert ok, receipt
    settle(hearth, writer)

    tasks = {task["id"]: task for task in snapshot(hearth)["tasks"]}
    # The task Karen was working is the start of the chain and needs no breadcrumb.
    assert tasks[writer.task_id]["lineage"] == []
    chain = tasks[receipt["task_id"]]["lineage"]
    assert [hop["task_id"] for hop in chain] == [writer.task_id, receipt["task_id"]]
    assert [hop["resident_name"] for hop in chain] == ["Karen", "Reporter"]
    assert chain[0]["sender"] is None and chain[0]["state"] is None
    assert chain[0]["title"] == "Answer the orchard question"
    assert chain[1]["sender"] == karen and chain[1]["sender_name"] == "Karen"
    assert chain[1]["state"] == "pending" and chain[1]["depth"] == 1
    assert chain[1]["title"] == "One question"


def test_an_operator_letter_names_the_operator_as_its_own_root(household):
    app, hearth, _ = household
    opens_the_door(hearth)
    receipt = hearth.send_operator_letter(
        "operator-post-1", "reporter", "One question", "Name one fact about the orchard."
    )
    tasks = {task["id"]: task for task in snapshot(hearth)["tasks"]}
    chain = tasks[receipt["task_id"]]["lineage"]
    assert [hop["task_id"] for hop in chain] == [receipt["task_id"]]
    assert chain[0]["sender"] == "operator" and chain[0]["sender_name"] == "operator"
    assert chain[0]["resident_name"] == "Reporter"


def test_a_refused_send_is_this_run_s_evidence_with_the_reason_the_resident_read(household):
    app, hearth, karen = household
    # The reporter's door is shut, which is how every resident starts.
    run, write = working_run(app, karen, "asks")
    ok, refusal = send(write)
    assert not ok and refusal["error"] == "letters_not_accepted"

    state = snapshot(hearth)
    evidence = [item for item in state["runs"] if item["id"] == run.id][0]["letters_refused"]
    assert [item["reason"] for item in evidence] == ["letters_not_accepted"]
    assert evidence[0]["at"] == NOW and evidence[0]["details"] == {}
    # A refusal writes nothing, so there is no letter and no event to draw.
    assert state["letters"] == []
    assert [item for item in state["tasks"] if item["id"] != run.task_id] == []


def test_a_refused_send_carries_the_numbers_the_refusal_named(household):
    app, hearth, karen = household
    opens_the_door(hearth)
    from hearth.authority.household import Household, household_state

    with hearth.database.transaction() as db:
        policy = household_state(db, NOW)
    Household(hearth).save(
        daily_limit=policy["daily_limit"],
        timezone=policy["timezone"],
        resident_limit=policy["resident_limit"],
        concurrency_limit=policy["concurrency_limit"],
        expected_revision=policy["revision"],
        letter_daily_limit=0,
    )
    run, write = working_run(app, karen, "asks")
    ok, refusal = send(write)
    assert not ok and refusal["error"] == "letter_daily_limit_reached"

    evidence = [item for item in snapshot(hearth)["runs"] if item["id"] == run.id][0]
    assert evidence["letters_refused"] == [
        {
            "at": NOW,
            "reason": "letter_daily_limit_reached",
            "details": {"received_today": 0, "letter_daily_limit": 0},
        }
    ]


def test_a_shut_door_is_visible_on_the_resident_it_belongs_to(household):
    app, hearth, karen = household
    residents = {r["id"]: r for r in snapshot(hearth)["residents"]}
    # Every resident starts with the door shut; nothing a letter needs is opened for it.
    assert residents["reporter"]["letters_accept"] == 0
    opens_the_door(hearth)
    residents = {r["id"]: r for r in snapshot(hearth)["residents"]}
    assert residents["reporter"]["letters_accept"] == 1
    assert residents[karen]["letters_accept"] == 0
