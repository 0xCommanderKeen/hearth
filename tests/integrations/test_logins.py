"""A resident's own login: where it lives, which run spends it, and when it holds work.

Nothing here reaches a provider. The probe is a callable, because that is exactly what
`Logins` is given at start -- the adapter's own answer to "is this directory logged in"
-- and this file is about everything around it (`docs/adr/0016-sandbox-per-run.md`).
"""

import pytest
from hearth.integrations.logins import (
    HOUSEHOLD,
    RESIDENT,
    Logins,
    directory_for,
    prepare,
    resident_login,
    scope,
    seeded,
)
from hearth.residents.models import Refused

CODEX = "codex_subscription"
CLAUDE = "claude_subscription"


def seed(data, resident_id, kind, *, credential="auth.json"):
    directory = data / "credentials" / resident_id / kind
    directory.mkdir(parents=True)
    if credential is not None:
        (directory / credential).write_text("synthetic-only")
    return directory


def test_a_resident_without_a_directory_of_its_own_spends_the_household_login(tmp_path):
    assert scope(tmp_path, "reader", CODEX) == HOUSEHOLD
    assert resident_login(tmp_path, "reader", CODEX) is None


def test_a_directory_of_its_own_is_the_resident_s_login_for_that_kind_alone(tmp_path):
    seed(tmp_path, "reader", CODEX)
    assert scope(tmp_path, "reader", CODEX) == RESIDENT
    # One kind at a time: the same resident on the other runtime is the household's.
    assert scope(tmp_path, "reader", CLAUDE) == HOUSEHOLD
    assert scope(tmp_path, "karen", CODEX) == HOUSEHOLD


def test_an_empty_directory_is_still_the_resident_s_own_login(tmp_path):
    """A login that is seeded and broken must not become the household's quietly."""
    seed(tmp_path, "reader", CODEX, credential=None)
    assert scope(tmp_path, "reader", CODEX) == RESIDENT


def test_nothing_a_caller_names_can_reach_out_of_the_credentials_directory(tmp_path):
    for bad in ("../../etc", "reader/..", ".", "", "a/b"):
        with pytest.raises(Refused) as error:
            resident_login(tmp_path, bad, CODEX)
        assert error.value.code == "invalid_identity"
    with pytest.raises(Refused) as error:
        resident_login(tmp_path, "reader", "../secrets")
    assert error.value.code == "runtime_configuration_invalid"


def test_the_credentials_shelf_is_created_private_and_holds_no_login(tmp_path):
    directory = prepare(tmp_path)
    assert directory.is_dir() and directory.stat().st_mode & 0o777 == 0o700
    assert list(directory.iterdir()) == []
    assert prepare(tmp_path) == directory  # idempotent


def test_the_session_is_launched_with_the_directory_its_admission_pinned(tmp_path):
    household = tmp_path / "household"
    household.mkdir()
    own = seed(tmp_path, "reader", CODEX)
    assert directory_for(tmp_path, household, "reader", CODEX, HOUSEHOLD) == household
    assert directory_for(tmp_path, household, "reader", CODEX, RESIDENT) == own


def test_a_login_taken_away_after_admission_refuses_rather_than_spending_the_household_s(
    tmp_path,
):
    household = tmp_path / "household"
    household.mkdir()
    own = seed(tmp_path, "reader", CODEX)
    (own / "auth.json").unlink()
    own.rmdir()
    with pytest.raises(Refused) as error:
        directory_for(tmp_path, household, "reader", CODEX, RESIDENT)
    assert error.value.code == "login_required"
    with pytest.raises(Refused) as error:
        directory_for(tmp_path, household, "reader", CODEX, "whatever")
    assert error.value.code == "run_login_scope_invalid"


def test_the_survey_names_every_seeded_login_and_skips_what_is_not_one(tmp_path):
    seed(tmp_path, "reader", CODEX)
    seed(tmp_path, "reader", CLAUDE)
    seed(tmp_path, "karen", CODEX)
    (tmp_path / "credentials" / "reader" / "not-a-runtime").mkdir()
    (tmp_path / "credentials" / "../elsewhere").resolve().mkdir(exist_ok=True)
    found = seeded(tmp_path, (CODEX, CLAUDE))
    assert [(login.resident_id, login.kind) for login in found] == [
        ("karen", CODEX),
        ("reader", CODEX),
        ("reader", CLAUDE),
    ]
    assert seeded(tmp_path / "nowhere", (CODEX,)) == []


def test_a_lapsed_login_is_named_and_a_working_one_is_not(tmp_path):
    seed(tmp_path, "reader", CODEX)
    seed(tmp_path, "karen", CODEX)
    logins = Logins(tmp_path, {CODEX: lambda directory: "reader" in directory.parts})
    assert logins.survey() == [
        {"resident_id": "karen", "kind": CODEX, "logged_in": False},
        {"resident_id": "reader", "kind": CODEX, "logged_in": True},
    ]
    assert logins.lapsed() == [{"resident_id": "karen", "kind": CODEX}]
    assert logins.holds("karen", CODEX) is True
    assert logins.holds("reader", CODEX) is False


def test_a_kind_this_process_cannot_ask_is_not_known_rather_than_lapsed(tmp_path):
    seed(tmp_path, "reader", CLAUDE)
    logins = Logins(tmp_path, {CLAUDE: None})
    assert logins.survey() == [{"resident_id": "reader", "kind": CLAUDE, "logged_in": None}]
    assert logins.lapsed() == []
    # Hearth does not hold a resident's work over a question it could not ask.
    assert logins.holds("reader", CLAUDE) is False


def test_a_login_that_is_gone_holds_the_run_at_once_however_fresh_the_last_answer_was(tmp_path):
    directory = seed(tmp_path, "reader", CODEX)
    logins = Logins(tmp_path, {CODEX: lambda _: True}, refresh=1000)
    assert logins.holds("reader", CODEX) is False
    (directory / "auth.json").unlink()
    directory.rmdir()
    assert logins.holds("reader", CODEX) is True


def test_the_answer_is_remembered_for_a_while_and_asked_again_when_the_login_changes(tmp_path):
    directory = seed(tmp_path, "reader", CODEX)
    asked = []
    answer = [False]

    def probe(path):
        asked.append(path)
        return answer[0]

    now = [1000.0]
    logins = Logins(tmp_path, {CODEX: probe}, refresh=60, clock=lambda: now[0])
    assert logins.holds("reader", CODEX) is True
    assert logins.holds("reader", CODEX) is True
    assert len(asked) == 1, "a held run must not start a CLI twice a second"
    # An operator seeds the login again: the directory changes and so does the answer.
    answer[0] = True
    (directory / "auth.json").write_text("re-seeded")
    import os

    os.utime(directory, ns=(2_000_000_000, 2_000_000_000))
    assert logins.holds("reader", CODEX) is False
    assert len(asked) == 2
    # And an answer nothing disturbed is asked again once it is old enough.
    now[0] += 61
    assert logins.holds("reader", CODEX) is False
    assert len(asked) == 3


def test_a_provider_that_cannot_be_asked_has_not_said_the_login_works(tmp_path):
    seed(tmp_path, "reader", CODEX)

    def probe(_):
        raise Refused("codex_subscription_configuration_required")

    logins = Logins(tmp_path, {CODEX: probe})
    assert logins.lapsed() == [{"resident_id": "reader", "kind": CODEX}]
    assert logins.holds("reader", CODEX) is True


def test_a_run_admitted_to_a_login_this_host_cannot_even_name_is_held(tmp_path):
    """`holds` is only ever asked about a run pinned to the resident's own login.

    A directory that was never there, and a name no path could be built from, are both
    reasons to wait rather than to launch on somebody else's subscription.
    """
    logins = Logins(tmp_path, {CODEX: lambda _: True})
    assert logins.holds("reader", CODEX) is True
    with pytest.raises(Refused):
        resident_login(tmp_path, "not an id", CODEX)
    assert logins.holds("not an id", CODEX) is True
