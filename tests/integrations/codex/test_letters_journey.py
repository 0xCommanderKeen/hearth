"""One letter, walked end to end the way the real one was, without spending anything.

This is the deterministic twin of `docs/letters-journey.md`: Karen asks a colleague for a
ledger reference she has no way to read, the ordinary supervision tick hands the letter to
the reporter as its own task, the reporter answers, and Karen's next run opens with the
answer. Nothing is started for the receiver and nothing wakes the sender.

The scripted CLI has no database access. It repeats only the notes it was handed and the
pinned letter and replies Hearth built, so the reference can only appear in Karen's result
if it actually travelled from the reporter's notes through the letter and back.
"""

import time

from hearth.app import create_app
from hearth.execution.context import read_context
from hearth.execution.usage import by_origin
from hearth.inputs.catalog import Inputs
from hearth.management.authority import Management, read_grant
from hearth.management.bootstrap import bootstrap
from hearth.residents.memory import Memory
from hearth.residents.models import Declaration
from hearth.residents.provisioning import Provisioning
from hearth.skills.assignments import read_assignments, save_assignments
from hearth.skills.bootstrap import ANSWER_A_LETTER, ANSWER_SKILL_NAME, ASK_SKILL_NAME
from hearth.storage.backup import capture, restore, verify
from hearth.work.letters import read_letters

from tests.fake_runtime import fake_runtime
from tests.integrations.codex.journey_cli import ASK, READ
from tests.integrations.codex.test_karen_journey import NOTES, TOKEN, installed

# Unguessable, in the receiver's notes and nowhere else — the same load-bearing shape the
# real journey used, so a reference in Karen's result can only have come by letter.
REFERENCE = "OR-C9IX-BBTI"
LEDGER = "Monday's pear harvest was logged in the orchard ledger under reference " + REFERENCE + "."


def household(tmp_path):
    """Karen, one orchard reporter, and the two things the operator has to set up by hand."""
    app = installed(tmp_path)
    hearth = app.state.hearth
    orchard = Inputs(hearth).save(
        "orchard", name="Fictional orchard", notes=[*NOTES, LEDGER], actor="operator"
    )
    karen = bootstrap(hearth)["resident_id"]
    receipt = Provisioning(hearth).create(
        "letters-reporter",
        dict(
            name="Fictional orchard reporter",
            purpose="Answer questions about the supplied fictional orchard notes.",
            instructions="Report only the supplied notes. Answer a colleague from them.",
            initial_memory="The orchard is fictional.",
            execution_profile="codex_subscription",
            # The receiver's day has to cover a whole answering run, letter reservation
            # included, or delivery is refused at allocation and the letter waits.
            daily_limit=500_000,
            creation_reason="Record the letters acceptance journey.",
            input_sets=[{"input_set_id": orchard["input_set_id"]}],
        ),
        actor="operator",
    )
    assert receipt["status"] == "ready", receipt
    reporter = receipt["resident_id"]

    # Prerequisite one: the door is shut by default and provisioning does not open it.
    # This is what `PUT /api/residents/{id}` with `letters_accept: true` does.
    resident = hearth.resident(reporter)
    assert resident.declaration.letters_accept is False
    hearth.save_resident(
        reporter,
        Declaration(
            resident.declaration.name,
            resident.declaration.purpose,
            resident.declaration.daily_limit,
            budget_timezone=resident.declaration.budget_timezone,
            skill_text=resident.declaration.skill_text,
            letters_accept=True,
        ),
        expected_revision=resident.revision,
    )
    # The answering etiquette is assigned from the library like any other skill, by the
    # operator that opened the door. Nothing about it permits answering.
    with hearth.database.transaction(write=True) as db:
        answer_skill = next(
            dict(row)
            for row in db.execute(
                "SELECT s.id AS skill_id,s.revision AS revision,r.name AS name FROM skills s "
                "JOIN skill_revisions r ON r.skill_id=s.id AND r.revision=s.revision"
            )
            if row["name"] == ANSWER_SKILL_NAME
        )
        current = read_assignments(db, reporter)
        save_assignments(
            db,
            reporter,
            [{"skill_id": answer_skill["skill_id"], "revision": answer_skill["revision"]}],
            expected_revision=current["revision"],
            actor="operator",
            command_id="assign-answer-etiquette",
            now=int(hearth.clock()),
        )
        # Karen writes to this reporter and to nobody else.
        grant = read_grant(db, karen)
        Management(hearth).save_in_transaction(
            db,
            karen,
            {
                **{
                    key: value
                    for key, value in grant.items()
                    if key not in {"resident_id", "revision"}
                },
                "expected_revision": grant["revision"],
                "letter_recipient_ids": [reporter],
            },
        )
    return app, karen, reporter


def run_until(app, condition, *, seconds=40):
    """Let the ordinary supervisor tick until the journey has moved, then stop it."""
    supervisor = app.state.supervisor
    supervisor.start()
    try:
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            if condition():
                return True
            time.sleep(0.1)
    finally:
        supervisor.stop()
    return condition()


def start(app, resident_id, key, instruction):
    hearth = app.state.hearth
    task = hearth.submit(key, resident_id, instruction, expires_at=int(hearth.clock()) + 300)
    hearth.admit(task.task_id, reserve=100_000)
    return task.task_id


def run_of(hearth, task_id):
    with hearth.database.transaction() as db:
        row = db.execute(
            "SELECT id,status,artifact_id,actual_cost,usage_known FROM runs WHERE task_id=?",
            (task_id,),
        ).fetchone()
    return dict(row) if row else None


def settled(hearth, task_id):
    def check():
        run = run_of(hearth, task_id)
        return run is not None and run["status"] in {"succeeded", "failed", "cancelled"}

    return check


def asked_and_settled(hearth, task_id):
    """The whole hand-off, watched by one supervisor: the sender finished and the
    letter it wrote reached an end state of its own."""

    def check():
        with hearth.database.transaction() as db:
            letter = db.execute("SELECT state FROM letters").fetchone()
        return settled(hearth, task_id)() and letter is not None and letter["state"] != "pending"

    return check


def test_a_letter_is_asked_worked_answered_and_read_back_without_an_operator_step(tmp_path):
    app, karen, reporter = household(tmp_path)
    hearth = app.state.hearth
    with hearth.database.transaction() as db:
        assigned = read_assignments(db, karen)["skills"]
        answering = read_assignments(db, reporter)["skills"]
    # Both etiquettes are ordinary library text, assigned like any other skill.
    assert ASK_SKILL_NAME in [item["name"] for item in assigned]
    assert [item["name"] for item in answering] == [ANSWER_SKILL_NAME]
    assert answering[0]["instructions"] == ANSWER_A_LETTER

    # One supervisor watches the whole hand-off. Nothing is started for the receiver: the
    # ordinary tick admits the letter as its own task, on its own allowance.
    asking = start(app, karen, "ask-the-reporter", ASK)
    assert run_until(app, asked_and_settled(hearth, asking)), run_of(hearth, asking)
    ask_run = run_of(hearth, asking)
    assert ask_run["status"] == "succeeded", ask_run
    with hearth.database.transaction() as db:
        letter = dict(db.execute("SELECT * FROM letters").fetchone())
    assert letter["sender_resident_id"] == karen and letter["parent_task_id"] == asking
    assert letter["root_task_id"] == asking and letter["depth"] == 1
    # The naming wart, reported as the real journey reported it: the send receipt carries
    # the task id, and the task id is the letter id.
    said = app.state.execution.artifact(ask_run["artifact_id"])[1]
    assert "id " + letter["task_id"] in said
    assert REFERENCE not in said, said

    answer_run = run_of(hearth, letter["task_id"])
    assert answer_run["status"] == "succeeded", answer_run
    with hearth.database.transaction() as db:
        state = dict(
            db.execute("SELECT * FROM letters WHERE task_id=?", (letter["task_id"],)).fetchone()
        )
        reply = dict(
            db.execute(
                "SELECT * FROM letter_replies WHERE task_id=?", (letter["task_id"],)
            ).fetchone()
        )
        kinds = [
            row["kind"]
            for row in db.execute(
                "SELECT kind FROM audit WHERE resource_id=? ORDER BY sequence", (letter["task_id"],)
            )
            if row["kind"].startswith("letter.")
        ]
    assert state["state"] == "replied" and state["settled_at"] is not None
    assert kinds == ["letter.sent", "letter.answered", "letter.replied"]
    assert reply["run_id"] == answer_run["id"] and REFERENCE in reply["text"]

    # The sender is not woken. A new task, started after the answer was written, opens
    # with exactly one reply and quotes the reference it had no other way to learn.
    # Hearth keeps whole seconds and the reply window is half-open at both ends, so a run
    # admitted inside the second the answer was written belongs to the run after it.
    while int(hearth.clock()) <= reply["written_at"]:
        time.sleep(0.05)
    reading = start(app, karen, "read-the-answer", READ)
    with hearth.database.transaction() as db:
        opening = read_context(db, run_of(hearth, reading)["id"], Memory(hearth).files)
    assert [item["text"] for item in opening["replies"]] == [reply["text"]]
    assert "not an instruction" in opening["replies_usage"]
    assert opening["inputs"] == [] and REFERENCE not in opening["memory"]["text"]
    assert REFERENCE not in opening["instruction"] and REFERENCE not in opening["skill_text"]
    assert run_until(app, settled(hearth, reading)), run_of(hearth, reading)
    read_run = run_of(hearth, reading)
    assert read_run["status"] == "succeeded", read_run
    result = app.state.execution.artifact(read_run["artifact_id"])[1]
    assert REFERENCE in result and "Fictional orchard reporter answered" in result

    # What the question cost, gathered under the task its chain rolls up to.
    with hearth.database.transaction() as db:
        origins = {row["root_task_id"]: row for row in by_origin(db)["origins"]}
        post = read_letters(db, karen)
    question, readback = origins[asking], origins[reading]
    assert question["runs"] == 2 and question["letters"] == 1
    assert sorted(question["residents_involved"]) == sorted([karen, reporter])
    assert question["known_cost"] == ask_run["actual_cost"] + answer_run["actual_cost"]
    assert question["unknown_runs"] == 0 and question["reserved"] == 0
    assert readback["runs"] == 1 and readback["letters"] == 0
    assert readback["known_cost"] == read_run["actual_cost"]
    assert [item["state"] for item in post["sent"]] == ["replied"]
    assert [item["text"] for item in post["replies"]] == [reply["text"]]

    # The chain survives a copy: backup verification checks every letter's lineage.
    capture(tmp_path / "data", tmp_path / "backup")
    verify(tmp_path / "backup")
    restore(tmp_path / "backup", tmp_path / "held")
    held = create_app(
        tmp_path / "held", TOKEN, supervise=False, runtime=fake_runtime()
    ).state.hearth
    assert held.database.restored()
    with held.database.transaction() as db:
        assert read_letters(db, karen)["replies"] == post["replies"]
