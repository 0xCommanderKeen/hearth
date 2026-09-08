"""Letters are arbitrated by Hearth: both permissions, the chain, and the shelf life.

Every check runs against a real temporary SQLite store through the owning interface, so
a refusal that claims to write nothing is observed to write nothing.
"""

import uuid

import pytest
from hearth.authority.household import Household
from hearth.management.authority import GrantPolicy, Management
from hearth.residents.models import Declaration, Refused
from hearth.storage.database import Database
from hearth.work.letters import expire_letters
from hearth.work.service import Hearth

NOW = 1_800_000_000
DAY = 86_400


@pytest.fixture
def household(tmp_path):
    now = [NOW]
    database = Database(tmp_path / "hearth.db")
    database.initialize()
    hearth = Hearth(database, clock=lambda: now[0])
    Household(hearth).save(
        daily_limit=100_000_000,
        timezone="UTC",
        resident_limit=20,
        concurrency_limit=10,
        expected_revision=0,
    )
    return hearth, now


def resident(hearth, resident_id, *, accepts=False, sends=False, recipients=()):
    hearth.save_resident(
        resident_id,
        Declaration(resident_id.title(), "Synthetic work", 10_000_000, letters_accept=accepts),
        expected_revision=0,
    )
    if sends or recipients:
        Management(hearth).save(
            resident_id,
            {
                **GrantPolicy().model_dump(),
                "expected_revision": 0,
                "enabled": True,
                "capabilities": ["send_letters"] if sends else [],
                "letter_recipient_ids": list(recipients),
            },
        )
    return resident_id


def running(hearth, resident_id, instruction="Synthetic work"):
    receipt = hearth.submit(
        "cmd-" + uuid.uuid4().hex, resident_id, instruction, expires_at=int(hearth.clock()) + 600
    )
    return hearth.admit(receipt.task_id, reserve=100_000)


def settle(hearth, run):
    """End a run the way the executor would, so its resident is free to be admitted again."""
    with hearth.database.transaction(write=True) as db:
        db.execute(
            "UPDATE runs SET status='succeeded',finished_at=?,actual_cost=0,usage_known=1 "
            "WHERE id=?",
            (int(hearth.clock()), run.id),
        )
        db.execute("UPDATE tasks SET status='succeeded' WHERE id=?", (run.task_id,))


def send(hearth, run, to, *, operation_id="letter-1", **kwargs):
    with hearth.database.transaction(write=True) as db:
        return hearth.send_letter_in_transaction(
            db,
            run.id,
            to,
            kwargs.pop("title", "One question"),
            kwargs.pop("detail", "Name one fact about the orchard."),
            operation_id,
            **kwargs,
        )


def written(hearth):
    with hearth.database.transaction() as db:
        return tuple(
            db.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
            for table in ("tasks", "letters", "management_operations", "audit")
        )


def refused(hearth, run, to, **kwargs):
    """Send, expect a refusal, and prove the store is exactly as it was."""
    before = written(hearth)
    with pytest.raises(Refused) as error:
        send(hearth, run, to, **kwargs)
    assert written(hearth) == before
    return error.value


def test_a_letter_is_a_task_with_an_address_and_an_audited_pair_of_ends(household):
    hearth, _ = household
    karen = resident(hearth, "karen", sends=True)
    reporter = resident(hearth, "reporter", accepts=True)
    run = running(hearth, karen, "Answer the orchard question")
    receipt = send(hearth, run, reporter)
    assert receipt["resident_id"] == reporter
    assert receipt["sender_resident_id"] == karen
    assert receipt["parent_task_id"] == run.task_id
    assert receipt["root_task_id"] == run.task_id
    assert receipt["depth"] == 1
    assert receipt["expires_at"] == NOW + DAY
    assert receipt["status"] == "queued"
    assert receipt["operation_id"] == "letter-1"
    assert receipt["originating_run_id"] == run.id
    task = hearth.task(receipt["task_id"])
    assert task.resident_id == reporter and task.status == "queued"
    assert "Name one fact about the orchard." in task.instruction
    with hearth.database.transaction() as db:
        letter = dict(
            db.execute("SELECT * FROM letters WHERE task_id=?", (receipt["task_id"],)).fetchone()
        )
    assert letter["sender_resident_id"] == karen and letter["sender_run_id"] == run.id
    assert letter["title"] == "One question" and letter["created_at"] == NOW
    sent = [fact for fact in hearth.audit() if fact["kind"] == "letter.sent"]
    assert len(sent) == 1 and sent[0]["resource_id"] == receipt["task_id"]
    assert sent[0]["detail"]["sender_resident_id"] == karen
    assert sent[0]["detail"]["recipient_resident_id"] == reporter
    assert sent[0]["detail"]["depth"] == 1


def test_the_same_operation_replays_its_receipt_and_a_changed_payload_conflicts(household):
    hearth, _ = household
    karen = resident(hearth, "karen", sends=True)
    reporter = resident(hearth, "reporter", accepts=True)
    run = running(hearth, karen)
    first = send(hearth, run, reporter)
    assert send(hearth, run, reporter) == first
    with hearth.database.transaction() as db:
        assert db.execute("SELECT count(*) FROM letters").fetchone()[0] == 1
    with pytest.raises(Refused, match="management_operation_conflict"):
        send(hearth, run, reporter, detail="A different question entirely.")
    with hearth.database.transaction() as db:
        assert db.execute("SELECT count(*) FROM letters").fetchone()[0] == 1


def test_a_replay_recovers_the_letter_a_closed_door_would_now_refuse(household):
    hearth, _ = household
    karen = resident(hearth, "karen", sends=True)
    reporter = resident(hearth, "reporter", accepts=True)
    run = running(hearth, karen)
    first = send(hearth, run, reporter)
    declaration = hearth.resident(reporter).declaration
    hearth.save_resident(
        reporter,
        Declaration(
            declaration.name, declaration.purpose, declaration.daily_limit, letters_accept=False
        ),
        expected_revision=1,
    )
    # The letter was already queued; a retry must recover it, not refuse it twice.
    assert send(hearth, run, reporter) == first


def test_both_permissions_must_meet_and_neither_side_can_waive_the_other(household):
    hearth, _ = household
    ungranted = resident(hearth, "ungranted")
    reporter = resident(hearth, "reporter", accepts=True)
    assert refused(hearth, running(hearth, ungranted), reporter).code == "letters_not_permitted"
    karen = resident(hearth, "karen", sends=True)
    shut = resident(hearth, "shut")
    assert refused(hearth, running(hearth, karen), shut).code == "letters_not_accepted"


def test_authority_is_the_grant_the_run_was_admitted_with(household):
    """A grant edited mid-run neither arrives late nor survives being taken away."""
    hearth, _ = household
    karen = resident(hearth, "karen")
    reporter = resident(hearth, "reporter", accepts=True)
    ungranted = running(hearth, karen)
    policy = {**GrantPolicy().model_dump(), "enabled": True, "capabilities": ["send_letters"]}
    Management(hearth).save(karen, {**policy, "expected_revision": 0})
    # This run was admitted with nothing pinned; a later grant is not its authority.
    assert refused(hearth, ungranted, reporter).code == "letters_not_permitted"
    settle(hearth, ungranted)
    granted = running(hearth, karen)
    Management(hearth).save(karen, {**policy, "capabilities": [], "expected_revision": 1})
    assert refused(hearth, granted, reporter).code == "letters_not_permitted"


def test_an_allowlisted_grant_writes_only_to_the_names_it_lists(household):
    hearth, _ = household
    reporter = resident(hearth, "reporter", accepts=True)
    other = resident(hearth, "other", accepts=True)
    karen = resident(hearth, "karen", sends=True, recipients=[reporter])
    run = running(hearth, karen)
    assert refused(hearth, run, other).code == "recipient_not_allowed"
    assert send(hearth, run, reporter)["resident_id"] == reporter


def test_a_missing_archived_or_self_addressed_recipient_is_refused(household):
    hearth, _ = household
    karen = resident(hearth, "karen", sends=True, accepts=True)
    gone = resident(hearth, "gone", accepts=True)
    run = running(hearth, karen)
    assert refused(hearth, run, "nobody").code == "resident_not_found"
    assert refused(hearth, run, karen).code == "self_letter"
    from hearth.residents.maintenance import LifecycleChange, Maintenance

    Maintenance(hearth).change_lifecycle(
        "archive-gone", gone, LifecycleChange(expected_revision=0, state="archived")
    )
    assert refused(hearth, run, gone).code == "recipient_archived"


def test_depth_and_lineage_come_from_the_admitted_run_not_from_the_caller(household):
    hearth, _ = household
    karen = resident(hearth, "karen", sends=True)
    reporter = resident(hearth, "reporter", accepts=True, sends=True)
    archivist = resident(hearth, "archivist", accepts=True, sends=True)
    origin = running(hearth, karen, "Answer the orchard question")
    first = send(hearth, origin, reporter)
    # The reporter answers the letter, and from that run writes on to a third resident.
    hop = hearth.admit(first["task_id"], reserve=100_000)
    second = send(hearth, hop, archivist, operation_id="letter-2")
    assert second["depth"] == 2
    assert second["parent_task_id"] == first["task_id"]
    # Cost attributes to the question that started the chain, not to the last hop.
    assert second["root_task_id"] == origin.task_id


def test_the_household_depth_cap_bounds_the_chain_and_zero_closes_the_post(household):
    hearth, _ = household
    karen = resident(hearth, "karen", sends=True)
    reporter = resident(hearth, "reporter", accepts=True, sends=True)
    archivist = resident(hearth, "archivist", accepts=True, sends=True)
    gardener = resident(hearth, "gardener", accepts=True)
    origin = running(hearth, karen)
    first = send(hearth, origin, reporter)
    hop = hearth.admit(first["task_id"], reserve=100_000)
    second = send(hearth, hop, archivist, operation_id="letter-2")
    third = hearth.admit(second["task_id"], reserve=100_000)
    error = refused(hearth, third, gardener, operation_id="letter-3")
    assert error.code == "max_letter_depth_exceeded"
    assert error.details == {"depth": 3, "max_letter_depth": 2}
    household_revision = Household(hearth).read()["revision"]
    Household(hearth).save(
        daily_limit=100_000_000,
        timezone="UTC",
        resident_limit=20,
        concurrency_limit=10,
        expected_revision=household_revision,
        max_letter_depth=0,
    )
    fresh = resident(hearth, "fresh", sends=True)
    assert (
        refused(hearth, running(hearth, fresh), reporter, operation_id="letter-4").code
        == "max_letter_depth_exceeded"
    )


def test_a_chain_never_revisits_a_resident_and_the_refusal_names_the_walk(household):
    hearth, _ = household
    karen = resident(hearth, "karen", sends=True, accepts=True)
    reporter = resident(hearth, "reporter", accepts=True, sends=True)
    origin = running(hearth, karen)
    first = send(hearth, origin, reporter)
    hop = hearth.admit(first["task_id"], reserve=100_000)
    error = refused(hearth, hop, karen, operation_id="letter-2")
    assert error.code == "letter_cycle"
    assert error.details == {"chain": [karen, reporter, karen]}


def test_a_letter_may_be_shortened_never_lengthened_and_never_outlives_its_shelf(household):
    hearth, _ = household
    karen = resident(hearth, "karen", sends=True)
    reporter = resident(hearth, "reporter", accepts=True)
    run = running(hearth, karen)
    assert refused(hearth, run, reporter, expires_at=NOW + DAY + 1).code == (
        "invalid_letter_deadline"
    )
    assert refused(hearth, run, reporter, expires_at=NOW).code == "invalid_letter_deadline"
    assert send(hearth, run, reporter, expires_at=NOW + 60)["expires_at"] == NOW + 60


def test_an_expired_letter_is_never_admitted_and_closes_as_a_failed_task(household):
    hearth, now = household
    karen = resident(hearth, "karen", sends=True)
    reporter = resident(hearth, "reporter", accepts=True)
    run = running(hearth, karen)
    receipt = send(hearth, run, reporter, expires_at=NOW + 60)
    assert expire_letters(hearth) == []
    now[0] = NOW + 61
    with pytest.raises(Refused, match="letter_expired"):
        hearth.admit(receipt["task_id"], reserve=100_000)
    assert hearth.task(receipt["task_id"]).status == "queued"
    assert expire_letters(hearth) == [receipt["task_id"]]
    assert hearth.task(receipt["task_id"]).status == "failed"
    expired = [fact for fact in hearth.audit() if fact["kind"] == "letter.expired"]
    assert len(expired) == 1 and expired[0]["resource_id"] == receipt["task_id"]
    assert expired[0]["detail"]["reason"] == "letter_expired"
    assert expired[0]["detail"]["sender_resident_id"] == karen
    assert expired[0]["detail"]["recipient_resident_id"] == reporter
    # A closed letter is closed once; the sweep is safe to keep running.
    assert expire_letters(hearth) == []


def test_a_letter_that_was_started_in_time_is_not_swept_out_from_under_its_run(household):
    hearth, now = household
    karen = resident(hearth, "karen", sends=True)
    reporter = resident(hearth, "reporter", accepts=True)
    receipt = send(hearth, running(hearth, karen), reporter, expires_at=NOW + 60)
    admitted = hearth.admit(receipt["task_id"], reserve=100_000)
    now[0] = NOW + 61
    assert expire_letters(hearth) == []
    assert hearth.run(admitted.id).status == "starting"


def test_the_door_is_operator_authority_and_a_reconfiguration_carries_it_forward(tmp_path):
    from fastapi.testclient import TestClient
    from hearth.app import create_app

    from tests.fake_runtime import fake_runtime
    from tests.support import seed_reader_via

    token = "synthetic-letters-operator"
    auth = {"Authorization": "Bearer " + token}
    app = create_app(tmp_path, token, supervise=False, runtime=fake_runtime())
    with TestClient(app) as client:
        seed_reader_via(client)
        saved = client.get("/api/residents/reader", headers=auth).json()
        assert saved["declaration"]["letters_accept"] is False
        opened = client.put(
            "/api/residents/reader",
            headers=auth,
            json=saved["declaration"]
            | {"letters_accept": True, "expected_revision": saved["revision"]},
        )
        assert opened.status_code == 200
        assert opened.json()["declaration"]["letters_accept"] is True
        # A configuration form that never learned about the door cannot close it.
        route = "/api/residents/reader/configuration"
        current = client.get(route, headers=auth).json()
        assert "letters_accept" not in current["declaration"]
        configured = client.put(
            route,
            headers={**auth, "Idempotency-Key": "reconfigure"},
            json={
                "expected_lifecycle_revision": current["lifecycle"]["revision"],
                "declaration": {**current["declaration"], "purpose": "A revised purpose"},
            },
        )
        assert configured.status_code == 200
        after = client.get("/api/residents/reader", headers=auth).json()["declaration"]
        assert after["letters_accept"] is True and after["purpose"] == "A revised purpose"
        # Omitting the door on the operator route likewise keeps it exactly as it stands.
        latest = client.get("/api/residents/reader", headers=auth).json()
        kept = client.put(
            "/api/residents/reader",
            headers=auth,
            json={
                key: value
                for key, value in latest["declaration"].items()
                if key != "letters_accept"
            }
            | {"expected_revision": latest["revision"]},
        )
        assert kept.json()["declaration"]["letters_accept"] is True


def test_the_door_can_be_written_alone_and_half_a_declaration_cannot(tmp_path):
    """A control that only opens a door does not have to restate what the resident is.

    Townhall's door control sends the door and the revision it saw, and nothing else, so
    it cannot overwrite a purpose or a skill text it never read. A body that says some of
    the declaration and not the rest is refused rather than merged into what stands.
    """
    from fastapi.testclient import TestClient
    from hearth.app import create_app

    from tests.fake_runtime import fake_runtime
    from tests.support import seed_reader_via

    token = "synthetic-letters-operator"
    auth = {"Authorization": "Bearer " + token}
    app = create_app(tmp_path, token, supervise=False, runtime=fake_runtime())
    with TestClient(app) as client:
        seed_reader_via(client)
        saved = client.get("/api/residents/reader", headers=auth).json()
        assert saved["declaration"]["letters_accept"] is False
        opened = client.put(
            "/api/residents/reader",
            headers=auth,
            json={"letters_accept": True, "expected_revision": saved["revision"]},
        )
        assert opened.status_code == 200
        assert opened.json()["revision"] == saved["revision"] + 1
        # Everything the body never mentioned is exactly what it was.
        assert opened.json()["declaration"] == saved["declaration"] | {"letters_accept": True}
        # The same body shuts it again, and the revision it names has moved on.
        assert (
            client.put(
                "/api/residents/reader",
                headers=auth,
                json={"letters_accept": False, "expected_revision": saved["revision"]},
            ).json()["error"]
            == "revision_conflict"
        )
        shut = client.put(
            "/api/residents/reader",
            headers=auth,
            json={"letters_accept": False, "expected_revision": opened.json()["revision"]},
        )
        assert shut.status_code == 200
        assert shut.json()["declaration"]["letters_accept"] is False
        # Half a declaration is refused: a body that renames a resident says all of it.
        latest = client.get("/api/residents/reader", headers=auth).json()
        half = client.put(
            "/api/residents/reader",
            headers=auth,
            json={"name": "Renamed", "expected_revision": latest["revision"]},
        )
        assert half.status_code == 409
        assert half.json()["error"] == "declaration_fields_invalid"
        assert (
            client.get("/api/residents/reader", headers=auth).json()["declaration"]
            == latest["declaration"]
        )
        # A body that says nothing at all is refused rather than writing a revision that
        # changes nothing and spends the expected revision every other client holds.
        empty = client.put(
            "/api/residents/reader",
            headers=auth,
            json={"expected_revision": latest["revision"]},
        )
        assert empty.status_code == 409
        assert empty.json()["error"] == "declaration_fields_invalid"
        assert (
            client.get("/api/residents/reader", headers=auth).json()["revision"]
            == latest["revision"]
        )
        # A door written at a resident that does not exist writes nothing.
        assert (
            client.put(
                "/api/residents/nobody",
                headers=auth,
                json={"letters_accept": True, "expected_revision": 1},
            ).json()["error"]
            == "revision_conflict"
        )


def test_the_operator_s_own_letter_starts_a_chain_the_receiver_can_walk_on(household):
    """An operator's letter has no resident and no run behind it, and is still a hop."""
    from hearth.work.letters import validate_letters

    hearth, _ = household
    reporter = resident(hearth, "reporter", accepts=True, sends=True)
    archivist = resident(hearth, "archivist", accepts=True)
    first = hearth.send_operator_letter("post-1", reporter, "One question", "Name one fact.")
    assert first["sender"] == "operator" and first["parent_task_id"] is None
    assert first["depth"] == 1 and first["root_task_id"] == first["task_id"]
    hop = hearth.admit(first["task_id"], reserve=100_000)
    second = send(hearth, hop, archivist, operation_id="letter-2")
    # Cost attributes to the question the operator asked, one hop further along.
    assert second["depth"] == 2 and second["root_task_id"] == first["task_id"]
    assert second["parent_task_id"] == first["task_id"]
    with hearth.database.transaction() as db:
        validate_letters(db)
        assert tuple(
            db.execute(
                "SELECT sender_resident_id,sender_run_id FROM letters WHERE task_id=?",
                (first["task_id"],),
            ).fetchone()
        ) == (None, None)


def test_the_daily_cap_bounds_what_one_resident_may_be_handed_in_its_own_day(household):
    """A neighbour's day is the household's to spend, and one sender cannot spend it all."""
    hearth, now = household
    karen = resident(hearth, "karen", sends=True)
    reporter = resident(hearth, "reporter", accepts=True)
    Household(hearth).save(
        daily_limit=100_000_000,
        timezone="UTC",
        resident_limit=20,
        concurrency_limit=10,
        expected_revision=Household(hearth).read()["revision"],
        letter_daily_limit=2,
    )
    run = running(hearth, karen)
    send(hearth, run, reporter, operation_id="letter-1")
    send(hearth, run, reporter, operation_id="letter-2")
    error = refused(hearth, run, reporter, operation_id="letter-3")
    assert error.code == "letter_daily_limit_reached"
    assert error.details == {"received_today": 2, "letter_daily_limit": 2}
    # The operator writes with its own hand and still cannot outspend the neighbour's day,
    # and its refused letter leaves no command behind to replay.
    before = written(hearth)
    with pytest.raises(Refused, match="letter_daily_limit_reached"):
        hearth.send_operator_letter("operator-letter-1", reporter, "One more", "Please answer.")
    assert written(hearth) == before
    with hearth.database.transaction() as db:
        assert not db.execute("SELECT 1 FROM commands WHERE id='operator-letter-1'").fetchone()
    # The cap counts what one resident was handed, not what the household wrote.
    other = resident(hearth, "other", accepts=True)
    assert send(hearth, run, other, operation_id="letter-4")["status"] == "queued"
    # Tomorrow in the receiver's own timezone is a fresh day.
    settle(hearth, run)
    now[0] = NOW + DAY
    tomorrow = running(hearth, karen)
    assert send(hearth, tomorrow, reporter, operation_id="letter-5")["status"] == "queued"


def test_a_finished_run_and_unreadable_text_send_nothing(household):
    hearth, _ = household
    karen = resident(hearth, "karen", sends=True)
    reporter = resident(hearth, "reporter", accepts=True)
    run = running(hearth, karen)
    assert refused(hearth, run, reporter, title="").code == "invalid_letter_title"
    assert refused(hearth, run, reporter, detail=" ").code == "invalid_letter_detail"
    settle(hearth, run)
    assert refused(hearth, run, reporter).code == "run_not_active"
