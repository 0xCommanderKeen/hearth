"""Which login a run spends: the resident's own, or the household's.

ADR 0016 decided that a login is a directory Hearth mounts read-only at the CLI's own
configuration path, that a resident may have one of its own, and that a resident
without one uses the household's. This module is that decision's whole surface: where
a resident's own login lives, which of the two a run was admitted to, and whether the
one it was admitted to still works.

The layout is `<data>/credentials/<resident id>/<kind>/`, one directory per resident
per runtime kind, `0700` and owned by Hearth's uid like everything else under the data
directory. **Hearth never creates a login and never copies one.** The operator seeds
one by running that CLI's own login flow with its configuration path pointed at that
directory (`docs/sandbox.md`); all Hearth ever does with the contents is mount them
and ask the CLI whether it is logged in.

Two rules the rest of Hearth leans on:

- **The directory decides the scope, and validity decides whether the run happens.** A
  resident with a directory for its run's kind is on its own login even if that
  directory is empty or the login in it has lapsed. Such a run waits until an operator
  fixes it; it never falls back to the household, because falling back would spend a
  different subscription than the operator chose.
- **Nothing here reads a credential.** The probe is the provider's own answer to "is
  this directory logged in", one boolean, and nothing else of the answer is kept,
  logged or passed on.
"""

import os
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from hearth.residents.models import Refused, identifier

# Under the data directory, the one place a resident's own logins live. It is inside
# the data directory on purpose: everything under that is already refused to every
# grant on this installation (`management/authority.protected_paths`), so no mount can
# ever reach a login, whatever a grant written on some other day says.
CREDENTIALS = "credentials"

RESIDENT = "resident"
HOUSEHOLD = "household"
SCOPES = (RESIDENT, HOUSEHOLD)

# How stale a probe's answer may be before the next question is asked of the CLI. The
# supervisor looks at a held run twice a second and a probe starts a provider's CLI, so
# an answer is remembered for a while. What is *not* remembered is whether the
# directory is there at all: that is a stat, it is asked every time, and an operator
# who takes a login away is obeyed at once.
REFRESH = 60.0

Probe = Callable[[Path], bool]


@dataclass(frozen=True)
class Seeded:
    """One resident's own login for one runtime kind, as it is on disk."""

    resident_id: str
    kind: str
    directory: Path


def root(data: Path) -> Path:
    """Where every resident's own login lives, under Hearth's own data directory."""
    return Path(data) / CREDENTIALS


def prepare(data: Path) -> Path:
    """Make the credentials directory, so an operator has somewhere to seed a login.

    The directory, never a login: Hearth creates the shelf and puts nothing on it.
    """
    directory = root(data)
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    return directory


def resident_login(data: Path, resident_id: str, kind: str) -> Path | None:
    """The resident's own login directory for one runtime kind, if it has one.

    Both parts of the path are checked before they are joined. A resident id is an
    identifier and a runtime kind is one this release knows, so neither can carry a
    path of its own into the credentials directory.
    """
    identifier(resident_id)
    from hearth.integrations.interface import RUNTIMES

    if kind not in RUNTIMES:
        raise Refused("runtime_configuration_invalid")
    directory = root(data) / resident_id / kind
    return directory if directory.is_dir() else None


def scope(data: Path, resident_id: str, kind: str) -> str:
    """Which login a run of this resident on this kind is admitted to spend.

    Read once, at admission, and written onto the run: a login seeded or taken away
    afterwards changes the next run, never one already admitted.
    """
    return RESIDENT if resident_login(data, resident_id, kind) is not None else HOUSEHOLD


def scopes(data: Path, resident_id: str, kinds) -> dict[str, str]:
    """Which login this resident is on, kind by kind, for an operator to read."""
    return {kind: scope(data, resident_id, kind) for kind in kinds}


def directory_for(data: Path, household: Path, resident_id: str, kind: str, admitted: str) -> Path:
    """The configuration directory a run's session is launched with.

    `admitted` is the scope its admission pinned. A run admitted to a resident's own
    login whose directory is gone refuses rather than launching on the household's:
    the operator chose which subscription pays for this resident's work, and a silent
    fallback would spend the other one.
    """
    if admitted not in SCOPES:
        raise Refused("run_login_scope_invalid")
    if admitted == HOUSEHOLD:
        return Path(household)
    directory = resident_login(data, resident_id, kind)
    if directory is None:
        raise Refused("login_required")
    return directory


def spent(value) -> bool:
    """Is this a receipt's own statement of which login the session spent?

    One of the two, or nothing at all: a receipt written before a resident could have a
    login of its own says nothing, and it settles exactly as it always did.
    """
    return value is None or value in SCOPES


def seeded(data: Path, kinds: tuple[str, ...] | list[str]) -> list[Seeded]:
    """Every resident login on this host, for the kinds asked about, in a stable order.

    A directory that is not a resident id, and a kind this release does not know, are
    somebody else's files: they are skipped rather than refused, because the operator
    owns this directory and Hearth is only reading it.
    """
    from hearth.integrations.interface import RUNTIMES

    found: list[Seeded] = []
    directory = root(data)
    try:
        residents = sorted(entry.name for entry in os.scandir(directory) if entry.is_dir())
    except OSError:
        return found
    for resident_id in residents:
        try:
            identifier(resident_id)
        except Refused:
            continue
        for kind in kinds:
            if kind not in RUNTIMES:
                continue
            if (directory / resident_id / kind).is_dir():
                found.append(Seeded(resident_id, kind, directory / resident_id / kind))
    return found


def _stamp(directory: Path) -> int | None:
    """When this login directory last changed, or nothing if it is not there."""
    try:
        return os.stat(directory).st_mtime_ns
    except OSError:
        return None


class Logins:
    """What Hearth knows about the residents that have logins of their own.

    One of these is built at start from the adapters this instance opened, so the probe
    for a kind is that provider's own -- nothing outside `integrations/` names a
    provider, and nothing here reads more of the answer than `loggedIn`.

    It is asked two questions. An operator asks for the whole survey (`GET /api/health`
    and the `credentials` command), which probes afresh. The executor asks whether one
    resident's own login still works before it launches that resident's run, which is
    asked twice a second while a run waits, so that answer is remembered for `refresh`
    seconds. A login that is simply *gone* is noticed immediately either way.
    """

    def __init__(
        self,
        data: Path,
        probes: dict[str, Probe | None],
        *,
        refresh: float = REFRESH,
        clock: Callable[[], float] = time.monotonic,
    ):
        self.data = Path(data)
        self.probes = probes
        self.refresh = refresh
        self.clock = clock
        self._answers: dict[Path, tuple[float, int | None, bool | None]] = {}

    @property
    def kinds(self) -> tuple[str, ...]:
        return tuple(sorted(self.probes))

    def seeded(self) -> list[Seeded]:
        return seeded(self.data, self.kinds)

    def survey(self) -> list[dict]:
        """Every resident login on this host and whether it is logged in, probed now.

        `logged_in` is `None` for a kind this process cannot ask -- an operator command
        run without that provider's binary in its environment. Not knowing is said, not
        rounded to a "no": a login nobody probed has not lapsed.
        """
        return [
            {
                "resident_id": login.resident_id,
                "kind": login.kind,
                "logged_in": self._probe(login.kind, login.directory),
            }
            for login in self.seeded()
        ]

    def lapsed(self) -> list[dict]:
        """The resident logins that are seeded and do not work, probed now."""
        return [
            {"resident_id": answer["resident_id"], "kind": answer["kind"]}
            for answer in self.survey()
            if answer["logged_in"] is False
        ]

    def holds(self, resident_id: str, kind: str) -> bool:
        """Is this resident's own login for this kind a reason not to launch its run?

        Only ever asked about a run whose admission pinned the resident's own login. A
        kind with no probe here holds nothing: Hearth does not hold work over a question
        it cannot ask.
        """
        probe = self.probes.get(kind)
        if probe is None:
            return False
        try:
            directory = resident_login(self.data, resident_id, kind)
        except Refused:
            return True
        if directory is None:
            # Admitted on the resident's own login and there is none any more. The run
            # waits for the operator rather than quietly spending the household's.
            return True
        return self._remembered(kind, directory) is False

    def _remembered(self, kind: str, directory: Path) -> bool | None:
        """The last answer about this directory, re-asked when it is old or it changed.

        The stamp is taken **after** the probe, never before, because asking a CLI
        whether a directory is logged in writes into that directory: the pinned Claude
        build leaves a `.claude.json`, a lock and a `backups/` behind on the cheapest
        question there is (measured, `docs/claude-runtime.md`, spike 8). Stamped before,
        every answer would look stale the moment it was given, and a held run would
        start a CLI twice a second -- which is the one thing this remembers to avoid.
        What the stamp still catches is an operator seeding the login again.
        """
        remembered = self._answers.get(directory)
        if remembered is not None:
            at, seen, answer = remembered
            if self.clock() - at < self.refresh and _stamp(directory) == seen:
                return answer
        answer = self._probe(kind, directory)
        self._answers[directory] = (self.clock(), _stamp(directory), answer)
        return answer

    def _probe(self, kind: str, directory: Path) -> bool | None:
        probe = self.probes.get(kind)
        if probe is None:
            return None
        try:
            return bool(probe(directory))
        except Refused:
            # A provider that cannot be asked -- a CLI that is gone, a configuration
            # half written -- has not said this login works, and nothing here guesses
            # that it does.
            return False
