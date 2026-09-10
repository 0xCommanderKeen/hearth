"""The reporter's second daily run opens with what its first run wrote.

The scripted CLI has no database access. It repeats only the notes it was handed and
the exact text of the entry Hearth pinned to the run, so the second report can mention
the first day only if the journal actually travelled into its context.
"""

import time

from hearth.app import create_app
from hearth.execution.context import read_context
from hearth.inputs.catalog import Inputs
from hearth.management.bootstrap import bootstrap
from hearth.observation.snapshot import snapshot
from hearth.residents.journal import Journal
from hearth.residents.memory import Memory
from hearth.residents.provisioning import Provisioning
from hearth.skills.assignments import read_assignments
from hearth.skills.bootstrap import KEEP_A_JOURNAL
from hearth.storage.backup import capture, restore, verify

from tests.fake_runtime import fake_runtime
from tests.integrations.codex.test_karen_journey import NOTES, TOKEN, installed

REPORT = "Write today's fictional orchard report."


def reporter(tmp_path):
    """One writable orchard reporter, provisioned the ordinary way by the operator."""
    app = installed(tmp_path)
    hearth = app.state.hearth
    orchard = Inputs(hearth).save(
        "orchard", name="Fictional orchard", notes=NOTES, actor="operator"
    )
    bootstrap(hearth)
    receipt = Provisioning(hearth).create(
        "journal-reporter",
        dict(
            name="Simulated orchard reporter",
            purpose="Summarize supplied fictional orchard facts concisely.",
            instructions="Report only the supplied notes. Close the day with one entry.",
            initial_memory="The orchard is fictional.",
            memory_writable=True,
            execution_profile="codex_subscription",
            daily_limit=500_000,
            creation_reason="Record the journal acceptance journey.",
            input_sets=[{"input_set_id": orchard["input_set_id"]}],
        ),
        actor="operator",
    )
    assert receipt["status"] == "ready", receipt
    return app, receipt["resident_id"]


def daily_run(app, resident_id: str, key: str):
    """Submit one day's report and let the ordinary supervisor carry it to settlement."""
    hearth = app.state.hearth
    task = hearth.submit(key, resident_id, REPORT, expires_at=int(hearth.clock()) + 120)
    hearth.admit(task.task_id, reserve=100_000)
    supervisor = app.state.supervisor
    supervisor.start()
    try:
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            with hearth.database.transaction() as db:
                row = db.execute(
                    "SELECT id,status,artifact_id,usage_known FROM runs WHERE task_id=?",
                    (task.task_id,),
                ).fetchone()
            if row is not None and row["status"] in {"succeeded", "failed", "cancelled"}:
                break
            time.sleep(0.1)
    finally:
        supervisor.stop()
    assert row is not None and row["status"] == "succeeded", dict(row) if row else None
    return dict(row)


def test_the_second_daily_run_refers_to_what_the_first_run_wrote(tmp_path):
    app, resident_id = reporter(tmp_path)
    hearth = app.state.hearth
    # The etiquette arrives with the resident because it may write; the wording is library text.
    with hearth.database.transaction() as db:
        assigned = read_assignments(db, resident_id)
    assert [item["name"] for item in assigned["skills"]] == ["Keep a journal"]
    assert assigned["skills"][0]["instructions"] == KEEP_A_JOURNAL

    first = daily_run(app, resident_id, "day-one")
    with hearth.database.transaction() as db:
        opening = read_context(db, first["id"], Memory(hearth).files)
    assert opening["journal"] == [] and opening["memory"]["revision"] == 1
    entries = Journal(hearth).read(resident_id)["entries"]
    assert len(entries) == 1 and entries[0]["run_id"] == first["id"]
    assert entries[0]["text"].startswith("Day 1: reported 12 pears Monday")
    assert Memory(hearth).read(resident_id)["revision"] == 2

    second = daily_run(app, resident_id, "day-two")
    with hearth.database.transaction() as db:
        reopened = read_context(db, second["id"], Memory(hearth).files)
    assert [item["sequence"] for item in reopened["journal"]] == [1]
    assert reopened["journal"][0]["text"] == entries[0]["text"]
    # The pinned note carries yesterday's durable fact, and only once.
    assert reopened["memory"]["revision"] == 2
    assert reopened["memory"]["text"].count("report only what they contain") == 1

    report = app.state.execution.artifact(second["artifact_id"])[1]
    assert "My last entry said: " + entries[0]["text"] in report
    assert "Today: Harvested 12 pears Monday. Planted 3 trees Tuesday." in report
    assert Memory(hearth).read(resident_id)["revision"] == 2

    history = Memory(hearth).history(resident_id)
    assert [
        (item["revision"], item["author"], item["run_id"]) for item in history["revisions"]
    ] == [
        (2, "run", first["id"]),
        (1, "operator", None),
    ]
    runs = {row["id"]: row for row in snapshot(hearth)["runs"]}
    assert runs[first["id"]]["memory_written"] == [2]
    assert runs[first["id"]]["journal_opened"] == []
    assert runs[first["id"]]["journal_written"] == 1
    assert runs[second["id"]]["memory_written"] == []
    assert runs[second["id"]]["journal_opened"] == [1]
    assert runs[second["id"]]["journal_written"] == 2
    # Remembering is not managing: the reporter holds no management authority at all.
    assert runs[first["id"]]["management"] is None

    capture(tmp_path / "data", tmp_path / "backup")
    verify(tmp_path / "backup")
    restore(tmp_path / "backup", tmp_path / "held")
    held = create_app(
        tmp_path / "held", TOKEN, supervise=False, runtime=fake_runtime()
    ).state.hearth
    assert held.database.restored()
    assert (
        Journal(held).read(resident_id)["entries"] == Journal(hearth).read(resident_id)["entries"]
    )
    assert Memory(held).history(resident_id)["revisions"] == history["revisions"]
