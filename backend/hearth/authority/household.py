"""Shared admission policy; callers enforce checks in their owning write transaction."""

import json
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from hearth.residents.models import Refused, microdollars

DEFAULTS: dict = dict(
    revision=0,
    daily_limit=10_000_000,
    timezone="Europe/Ljubljana",
    resident_limit=20,
    concurrency_limit=2,
    journal_limit=30,
    # How many hops a letter chain may take, and how long an unanswered letter is worth
    # answering. Zero hops disables letters for the whole household.
    max_letter_depth=2,
    letter_ttl_seconds=86_400,
    # How many letters one resident may be handed in its own day. A letter is worked as
    # an ordinary task, so this is the neighbour's time and the household's money; the
    # default is deliberately small, and zero shuts a household's post entirely.
    letter_daily_limit=5,
)
MAX_LETTER_DEPTH = 5
LETTER_TTL_RANGE = (60, 604_800)
MAX_LETTER_DAILY_LIMIT = 100
ACTIVE = "('starting', 'running', 'stopping', 'interrupted')"


def read_journal_limit(db) -> int:
    """How many journal entries a resident keeps before older ones roll to files."""
    row = db.execute("SELECT journal_limit FROM household_policy WHERE id=1").fetchone()
    return row[0] if row else DEFAULTS["journal_limit"]


def read_letter_policy(db) -> dict:
    """The household's letter reach, shelf life and daily cap; a fresh store uses defaults."""
    row = db.execute(
        "SELECT max_letter_depth,letter_ttl_seconds,letter_daily_limit "
        "FROM household_policy WHERE id=1"
    ).fetchone()
    keys = ("max_letter_depth", "letter_ttl_seconds", "letter_daily_limit")
    return dict(row) if row else {key: DEFAULTS[key] for key in keys}


def validate_windows(db) -> None:
    for row in db.execute(
        "SELECT r.created_at,w.* FROM runs r LEFT JOIN run_household_windows w ON w.run_id=r.id"
    ):
        if row["run_id"] is None:
            raise Refused("household_accounting_corrupt")
        try:
            window = _day_window(row["created_at"], row["timezone"])
            valid = all(row[key] == value for key, value in window.items())
        except ZoneInfoNotFoundError, ValueError, TypeError, OverflowError:
            valid = False
        if not valid:
            raise Refused("household_accounting_corrupt")


def _policy_window(db, now: int) -> dict:
    row = db.execute("SELECT * FROM household_policy WHERE id=1").fetchone()
    policy = {key: row[key] for key in DEFAULTS} if row else dict(DEFAULTS)
    return policy | _day_window(now, policy["timezone"])


def _day_window(now: int, timezone: str) -> dict:
    local = datetime.fromtimestamp(now, ZoneInfo(timezone))
    start = local.replace(hour=0, minute=0, second=0, microsecond=0, fold=0)
    end = (start + timedelta(days=1)).replace(fold=0)
    return dict(
        starts_at=int(start.timestamp()),
        ends_at=int(end.timestamp()),
        budget_day=local.date().isoformat(),
    )


def household_state(db, now: int) -> dict:
    validate_windows(db)
    policy = _policy_window(db, now)
    spent = db.execute(
        "SELECT COALESCE(SUM(actual_cost),0) FROM runs r "
        "JOIN run_household_windows w ON w.run_id=r.id WHERE usage_known=1 "
        "AND ((created_at>=? AND created_at<?) OR (w.starts_at<=? AND w.ends_at>?))",
        (policy["starts_at"], policy["ends_at"], now, now),
    ).fetchone()[0]
    reserved = db.execute(
        f"SELECT COALESCE(SUM(reserved),0) FROM runs WHERE status IN {ACTIVE}"
    ).fetchone()[0]
    unknown = db.execute(
        f"SELECT COALESCE(SUM(reserved),0) FROM runs WHERE usage_known=0 AND status NOT IN {ACTIVE}"
    ).fetchone()[0]
    return policy | dict(
        spent=spent,
        reserved=reserved,
        unknown=unknown,
        uncertain_reserved=db.execute(
            "SELECT COALESCE(SUM(reserved),0) FROM runs WHERE status='interrupted'"
        ).fetchone()[0],
        remaining=max(0, policy["daily_limit"] - spent - reserved - unknown),
        resident_count=db.execute("SELECT COUNT(*) FROM residents").fetchone()[0],
        active_runs=db.execute(f"SELECT COUNT(*) FROM runs WHERE status IN {ACTIVE}").fetchone()[0],
    )


def check_creation(db, now: int) -> None:
    state = household_state(db, now)
    if state["resident_count"] >= state["resident_limit"]:
        raise Refused("household_resident_limit")


def check_admission(db, now: int, reserve: int) -> None:
    state = household_state(db, now)
    if state["active_runs"] >= state["concurrency_limit"]:
        raise Refused("household_concurrency_limit")
    if reserve > state["remaining"]:
        raise Refused("household_budget_exhausted")


class Household:
    def __init__(self, hearth):
        self.hearth = hearth

    def read(self) -> dict:
        with self.hearth.database.transaction() as db:
            return household_state(db, int(self.hearth.clock()))

    def save(
        self,
        *,
        daily_limit: int,
        timezone: str,
        resident_limit: int,
        concurrency_limit: int,
        expected_revision: int,
        journal_limit: int | None = None,
        max_letter_depth: int | None = None,
        letter_ttl_seconds: int | None = None,
        letter_daily_limit: int | None = None,
    ) -> dict:
        """Operator-only API. No runtime bridge exposes this authority."""
        microdollars(daily_limit)
        if not isinstance(timezone, str) or len(timezone) > 100:
            raise Refused("invalid_budget_timezone")
        try:
            ZoneInfo(timezone)
        except ZoneInfoNotFoundError, ValueError:
            raise Refused("invalid_budget_timezone") from None
        for value, maximum in ((resident_limit, 1000), (concurrency_limit, 100)):
            if type(value) is not int or not 1 <= value <= maximum:
                raise Refused("invalid_household_limit")
        if type(expected_revision) is not int or expected_revision < 0:
            raise Refused("invalid_revision")
        with self.hearth.database.transaction(write=True) as db:
            now = int(self.hearth.clock())
            current = household_state(db, now)
            if current["revision"] != expected_revision:
                raise Refused("revision_conflict")
            # Omitting the journal bound keeps the stored one; it has no separate revision.
            if journal_limit is None:
                journal_limit = current["journal_limit"]
            if type(journal_limit) is not int or not 1 <= journal_limit <= 1000:
                raise Refused("invalid_household_limit")
            # A client that never learned about letters keeps the household's own reach
            # and shelf life rather than silently resetting them to the shipped default.
            if max_letter_depth is None:
                max_letter_depth = current["max_letter_depth"]
            if letter_ttl_seconds is None:
                letter_ttl_seconds = current["letter_ttl_seconds"]
            if letter_daily_limit is None:
                letter_daily_limit = current["letter_daily_limit"]
            if type(max_letter_depth) is not int or not 0 <= max_letter_depth <= MAX_LETTER_DEPTH:
                raise Refused("invalid_letter_policy")
            if (
                type(letter_ttl_seconds) is not int
                or not LETTER_TTL_RANGE[0] <= letter_ttl_seconds <= LETTER_TTL_RANGE[1]
            ):
                raise Refused("invalid_letter_policy")
            if (
                type(letter_daily_limit) is not int
                or not 0 <= letter_daily_limit <= MAX_LETTER_DAILY_LIMIT
            ):
                raise Refused("invalid_letter_policy")
            revision = expected_revision + 1
            db.execute(
                "INSERT INTO household_policy VALUES (1,?,?,?,?,?,?,?,?,?) ON CONFLICT(id) "
                "DO UPDATE SET revision=excluded.revision,daily_limit=excluded.daily_limit,"
                "timezone=excluded.timezone,resident_limit=excluded.resident_limit,"
                "concurrency_limit=excluded.concurrency_limit,"
                "journal_limit=excluded.journal_limit,"
                "max_letter_depth=excluded.max_letter_depth,"
                "letter_ttl_seconds=excluded.letter_ttl_seconds,"
                "letter_daily_limit=excluded.letter_daily_limit",
                (
                    revision,
                    daily_limit,
                    timezone,
                    resident_limit,
                    concurrency_limit,
                    journal_limit,
                    max_letter_depth,
                    letter_ttl_seconds,
                    letter_daily_limit,
                ),
            )
            db.execute(
                "INSERT INTO audit(kind,resource_id,at,detail) VALUES (?,?,?,?)",
                (
                    "household.policy_saved",
                    "household",
                    now,
                    json.dumps(
                        dict(
                            revision=revision,
                            daily_limit=daily_limit,
                            timezone=timezone,
                            resident_limit=resident_limit,
                            concurrency_limit=concurrency_limit,
                            journal_limit=journal_limit,
                            max_letter_depth=max_letter_depth,
                            letter_ttl_seconds=letter_ttl_seconds,
                            letter_daily_limit=letter_daily_limit,
                            actor="operator",
                        )
                    ),
                ),
            )
            return household_state(db, now)


def pin_admission(db, now: int, run_id: str) -> None:
    state = _policy_window(db, now)
    db.execute(
        "INSERT INTO run_household_windows VALUES (?,?,?,?,?,?)",
        (
            run_id,
            state["timezone"],
            state["budget_day"],
            state["starts_at"],
            state["ends_at"],
            state["revision"],
        ),
    )
