"""The letter etiquettes reach the library of a household Karen's setup can no longer fill."""

import json

from fastapi.testclient import TestClient
from hearth.app import create_app
from hearth.skills.bootstrap import (
    ANSWER_A_LETTER,
    ANSWER_SKILL_NAME,
    ASK_A_COLLEAGUE,
    ASK_SKILL_NAME,
)
from hearth.skills.catalog import Skills
from hearth.storage.database import Database
from hearth.work.service import Hearth

from tests.fake_runtime import fake_runtime

TOKEN = "synthetic-library-seed-operator"
AUTH = {"Authorization": "Bearer " + TOKEN}
# What a household set up before letters existed holds: Karen's receipt and no etiquette.
SETUP = {"resident_id": "karen", "status": "ready"}


def _set_up_store(tmp_path) -> Hearth:
    """A real store on disk that has been set up, opened without starting Hearth over it."""
    database = Database(tmp_path / "hearth.db")
    database.initialize()
    hearth = Hearth(database)
    with hearth.database.transaction(write=True) as db:
        db.execute("INSERT INTO system_meta VALUES ('karen_setup',?)", (json.dumps(SETUP),))
    return hearth


def _library(hearth) -> dict:
    with hearth.database.transaction() as db:
        return {
            row["name"]: dict(row)
            for row in db.execute(
                "SELECT r.name AS name,r.instructions AS instructions,s.id AS skill_id "
                "FROM skills s JOIN skill_revisions r "
                "ON r.skill_id=s.id AND r.revision=s.revision WHERE r.status='active'"
            )
        }


def _names(hearth) -> list[str]:
    with hearth.database.transaction() as db:
        return [
            row["name"]
            for row in db.execute(
                "SELECT r.name AS name FROM skills s JOIN skill_revisions r "
                "ON r.skill_id=s.id AND r.revision=s.revision"
            )
        ]


def test_a_household_set_up_before_letters_gains_both_etiquettes_on_start(tmp_path):
    """Setup returns its first receipt forever, so start puts the wording in the library."""
    assert not _names(_set_up_store(tmp_path))

    app = create_app(tmp_path, TOKEN, supervise=False, runtime=fake_runtime())
    client = TestClient(app)
    # Both are in the catalog Townhall's Skills page reads.
    catalog = client.get("/api/skills", headers=AUTH).json()
    assert {ASK_SKILL_NAME, ANSWER_SKILL_NAME} <= {item["name"] for item in catalog}
    library = _library(app.state.hearth)
    assert library[ASK_SKILL_NAME]["instructions"] == ASK_A_COLLEAGUE
    assert library[ANSWER_SKILL_NAME]["instructions"] == ANSWER_A_LETTER
    # The seed sets nobody up and hands the wording to nobody: setup keeps its receipt,
    # and a household with no residents has gained none.
    assert client.post("/api/management/bootstrap", headers=AUTH).json() == SETUP
    assert client.get("/api/state", headers=AUTH).json()["residents"] == []


def test_a_second_start_reuses_the_etiquettes_the_first_one_seeded(tmp_path):
    """Seeding is by identity: starting again adds no second entry of either name."""
    _set_up_store(tmp_path)
    first = create_app(tmp_path, TOKEN, supervise=False, runtime=fake_runtime()).state.hearth
    before = _library(first)

    second = create_app(tmp_path, TOKEN, supervise=False, runtime=fake_runtime()).state.hearth
    names = _names(second)
    assert names.count(ASK_SKILL_NAME) == names.count(ANSWER_SKILL_NAME) == 1
    after = _library(second)
    for name in (ASK_SKILL_NAME, ANSWER_SKILL_NAME):
        assert after[name]["skill_id"] == before[name]["skill_id"]


def test_an_etiquette_written_by_hand_is_adopted_rather_than_seeded_twice(tmp_path):
    """An operator who wrote the wording first keeps that one library entry."""
    hearth = _set_up_store(tmp_path)
    by_hand = Skills(hearth).save(
        "operator-ask-a-colleague",
        name=ASK_SKILL_NAME,
        description="The operator's own wording",
        instructions="Ask plainly.",
        actor="operator",
    )
    # A same-named archived entry is not adopted; the oldest active one is.
    stale = Skills(hearth).save(
        "stale-ask", name=ASK_SKILL_NAME, description="Older", instructions="x", actor="operator"
    )
    Skills(hearth).archive(
        "archive-stale", stale["skill_id"], expected_revision=stale["revision"], actor="operator"
    )

    started = create_app(tmp_path, TOKEN, supervise=False, runtime=fake_runtime()).state.hearth
    library = _library(started)
    assert library[ASK_SKILL_NAME]["skill_id"] == by_hand["skill_id"]
    assert library[ASK_SKILL_NAME]["instructions"] == "Ask plainly."
    assert library[ANSWER_SKILL_NAME]["instructions"] == ANSWER_A_LETTER
    assert _names(started).count(ANSWER_SKILL_NAME) == 1


def test_a_household_that_has_not_been_set_up_receives_them_from_setup_as_before(tmp_path):
    """Start seeds only what setup can no longer reach; an empty library stays empty."""
    from hearth.management.bootstrap import bootstrap

    hearth = create_app(tmp_path, TOKEN, supervise=False, runtime=fake_runtime()).state.hearth
    assert not _names(hearth)
    bootstrap(hearth)
    assert {ASK_SKILL_NAME, ANSWER_SKILL_NAME} <= set(_library(hearth))
