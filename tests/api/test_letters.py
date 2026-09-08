"""The operator writes a letter with its own hand, and reads any resident's post.

No grant bounds the operator, because there is no resident whose authority it could
escalate; the receiver's door, its archive state and the household's own reach hold
exactly as they do for a letter one resident writes to another.
"""

from dataclasses import replace

import pytest
from fastapi.testclient import TestClient
from hearth.app import create_app
from hearth.authority.household import Household
from hearth.management.bootstrap import bootstrap
from hearth.residents.models import Declaration

from tests.fake_runtime import fake_runtime

TOKEN = "synthetic-operator-letters-token"
AUTH = {"Authorization": "Bearer " + TOKEN}
NOW = 1_788_640_000
DAY = 86_400


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(tmp_path, TOKEN, supervise=False, runtime=fake_runtime())) as client:
        client.app.state.hearth.clock = lambda: NOW
        yield client


def hearth(client):
    return client.app.state.hearth


def reporter(client, *, accepts=True):
    hearth(client).save_resident(
        "reporter",
        Declaration(
            "Reporter", "Answers one question about the orchard", 1_000_000, letters_accept=accepts
        ),
        expected_revision=0,
    )
    return "reporter"


def write(client, to="reporter", *, key="post-1", **body):
    return client.post(
        f"/api/residents/{to}/letters",
        headers={**AUTH, "Idempotency-Key": key},
        json={"title": "One question", "detail": "Name one fact about the orchard.", **body},
    )


def written(client):
    with hearth(client).database.transaction() as db:
        return tuple(
            db.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
            for table in ("tasks", "letters", "commands", "audit")
        )


def test_the_operator_writes_a_letter_that_is_an_ordinary_task_for_the_receiver(client):
    reporter(client)
    response = write(client)
    assert response.status_code == 201
    receipt = response.json()
    assert receipt["sender"] == "operator" and receipt["resident_id"] == "reporter"
    assert receipt["parent_task_id"] is None
    # It starts a chain of its own: no run wrote it and no hop came before it.
    assert receipt["root_task_id"] == receipt["task_id"] and receipt["depth"] == 1
    assert receipt["expires_at"] == NOW + DAY and receipt["status"] == "queued"
    task = hearth(client).task(receipt["task_id"])
    assert task.resident_id == "reporter" and task.status == "queued"
    assert "Name one fact about the orchard." in task.instruction
    sent = [fact for fact in hearth(client).audit() if fact["kind"] == "letter.sent"]
    assert len(sent) == 1 and sent[0]["detail"]["sender"] == "operator"
    assert sent[0]["detail"]["sender_resident_id"] is None
    assert sent[0]["detail"]["recipient_resident_id"] == "reporter"
    # The same key replays the one letter; a changed payload is a different letter.
    assert write(client).json() == receipt
    conflict = write(client, title="A different question")
    assert conflict.status_code == 409 and conflict.json() == {"error": "command_conflict"}
    assert len(hearth(client).letters("reporter")["inbox"]) == 1


def test_the_operator_cannot_waive_the_door_the_archive_or_the_household_s_reach(client):
    reporter(client, accepts=False)
    before = written(client)
    closed = write(client)
    assert closed.status_code == 409 and closed.json() == {"error": "letters_not_accepted"}
    assert written(client) == before

    missing = write(client, "nobody", key="post-2")
    assert missing.status_code == 404 and missing.json() == {"error": "resident_not_found"}

    from hearth.residents.maintenance import LifecycleChange, Maintenance

    hearth(client).save_resident(
        "gone",
        Declaration("Gone", "Left the household", 1_000_000, letters_accept=True),
        expected_revision=0,
    )
    Maintenance(hearth(client)).change_lifecycle(
        "archive-gone", "gone", LifecycleChange(expected_revision=0, state="archived")
    )
    archived = write(client, "gone", key="post-4")
    assert archived.status_code == 409 and archived.json() == {"error": "recipient_archived"}

    # A household that closed the post closed it to the operator too.
    resident = hearth(client).resident("reporter")
    hearth(client).save_resident(
        "reporter",
        replace(resident.declaration, letters_accept=True),
        expected_revision=resident.revision,
    )
    policy = Household(hearth(client)).read()
    Household(hearth(client)).save(
        daily_limit=policy["daily_limit"],
        timezone=policy["timezone"],
        resident_limit=policy["resident_limit"],
        concurrency_limit=policy["concurrency_limit"],
        expected_revision=policy["revision"],
        max_letter_depth=0,
    )
    before = written(client)
    closed = write(client, key="post-3")
    assert closed.status_code == 409
    assert closed.json() == {"error": "max_letter_depth_exceeded"}
    assert written(client) == before


def test_a_shorter_shelf_life_is_the_operator_s_to_ask_for_and_a_longer_one_is_not(client):
    reporter(client)
    early = write(client, expires_at=NOW + 60)
    assert early.status_code == 201 and early.json()["expires_at"] == NOW + 60
    late = write(client, key="post-2", expires_at=NOW + 2 * DAY)
    assert late.status_code == 409 and late.json() == {"error": "invalid_letter_deadline"}


def test_the_inbox_and_the_sent_letters_are_both_the_operator_s_to_read(client):
    karen = bootstrap(hearth(client))["resident_id"]
    reporter(client)
    operator_letter = write(client).json()
    # One letter from a resident, alongside the operator's own.
    task = hearth(client).submit(
        "karen-work", karen, "Answer the orchard question", expires_at=NOW + 600
    )
    run = hearth(client).admit(task.task_id, reserve=100_000)
    with hearth(client).database.transaction(write=True) as db:
        sent = hearth(client).send_letter_in_transaction(
            db, run.id, "reporter", "Karen's question", "How many pear trees?", "letter-1"
        )

    inbox = client.get("/api/residents/reporter/letters", headers=AUTH)
    assert inbox.status_code == 200
    body = inbox.json()
    assert {(item["task_id"], item["sender"]) for item in body["inbox"]} == {
        (sent["task_id"], karen),
        (operator_letter["task_id"], "operator"),
    }
    assert body["sent"] == []
    letter = next(item for item in body["inbox"] if item["task_id"] == sent["task_id"])
    assert letter["reply"] is None and letter["depth"] == 1
    assert "How many pear trees?" in letter["instruction"]

    # What Karen wrote is hers, and the answer to it reaches her end of the exchange.
    with hearth(client).database.transaction(write=True) as db:
        db.execute(
            "UPDATE runs SET status='succeeded',finished_at=?,actual_cost=0,usage_known=1 "
            "WHERE id=?",
            (NOW, run.id),
        )
        db.execute("UPDATE tasks SET status='succeeded' WHERE id=?", (run.task_id,))
    answering = hearth(client).admit(sent["task_id"], reserve=100_000)
    with hearth(client).database.transaction(write=True) as db:
        hearth(client).reply_to_letter_in_transaction(
            db, answering.id, sent["task_id"], "412 pear trees.", "answer-1"
        )
    mine = client.get(f"/api/residents/{karen}/letters", headers=AUTH).json()
    assert mine["inbox"] == []
    assert [item["task_id"] for item in mine["sent"]] == [sent["task_id"]]
    assert mine["sent"][0]["reply"]["text"] == "412 pear trees."
    assert mine["sent"][0]["reply"]["resident_id"] == "reporter"
    assert mine["sent"][0]["reply"]["run_id"] == answering.id

    assert client.get("/api/residents/nobody/letters", headers=AUTH).status_code == 404
    assert client.get("/api/residents/reporter/letters?limit=0", headers=AUTH).status_code == 409
