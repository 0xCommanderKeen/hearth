"""Explicit operator grants; instruction text never creates management authority."""

import hashlib
import json
import os
from collections.abc import Iterable
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from hearth.inputs.catalog import read_input
from hearth.integrations.interface import management_protocol
from hearth.residents.models import Refused, identifier
from hearth.work.service import Hearth, _audit

# Paths no grant may name, whatever an operator writes, on any host: the root itself,
# the machine's own configuration and the kernel's two trees. Everything else a grant
# may not reach is this installation's own and arrives through `protected_paths` --
# Hearth's data directory, the logins and the container runtime's socket.
FORBIDDEN = ("/", "/etc", "/proc", "/sys")
# Where a container runtime's socket ordinarily is. It is root-equivalent on the host
# (`docs/adr/0016-sandbox-per-run.md`), so it is refused wherever it is found, and the
# one an operator configured explicitly is added beside these.
SOCKETS = ("/var/run/docker.sock", "/run/docker.sock")


class Mount(BaseModel):
    """One host path this resident's runs may reach, and how far into it they may go.

    The name is what the run's own context calls the folder and what it is mounted as
    inside the sandbox (`/mounts/<name>`); the host path is this household's business
    and never travels in a bundle. Read-only unless the grant says `rw`, which is
    audited when it is granted and again when a run is seen to have used it.
    """

    model_config = ConfigDict(extra="forbid", strict=True)
    name: str = Field(min_length=1, max_length=64)
    host_path: str = Field(min_length=1, max_length=4096)
    mode: Literal["ro", "rw"] = "ro"


def protected_paths(
    data: Path, logins: Iterable[Path] = (), *, socket: str | None = None
) -> tuple[str, ...]:
    """The paths this installation refuses to mount, beside the ones no host may.

    Hearth's own data directory holds the store, the run folders and the artifacts; a
    login directory holds a credential; the container runtime's socket is root on the
    host. None of them is a folder a resident works in, and a grant naming one is
    refused when it is written rather than when a run is admitted.
    """
    named = None
    if isinstance(socket, str) and socket.startswith("unix://"):
        named = socket[len("unix://") :]
    values = [str(Path(data).resolve()), *(str(Path(login).resolve()) for login in logins)]
    values += [path for path in (*SOCKETS, named) if path]
    return tuple(dict.fromkeys(values))


def _within(path: str, other: str) -> bool:
    """Is one of these two paths the other, or inside it? Either way round is refused.

    A mount below a protected path reaches part of it; a mount above one reaches all
    of it. The grant is refused for both, so no operator has to reason about which
    direction of containment was the dangerous one.

    The root is the exception that proves it: everything is under `/`, so protecting it
    the same way would refuse every grant there is. What `/` protects is itself.
    """
    first, second = path.rstrip("/") or "/", other.rstrip("/") or "/"
    if first == second:
        return True
    if first == "/" or second == "/":
        return False
    return first.startswith(second + "/") or second.startswith(first + "/")


def check_mounts(mounts: list[Mount], protected: Iterable[str] = ()) -> None:
    """Refuse a filesystem grant nothing should ever hold, at the moment it is written.

    Every refusal here is `grant_mount_forbidden`: a relative or unnormalised path, a
    path this host protects, a name two mounts share, and a path two names share --
    which would be one folder a run could reach under two names and, inside a sandbox,
    two bind mounts of the same source.
    """
    names: set[str] = set()
    paths: set[str] = set()
    for mount in mounts:
        try:
            identifier(mount.name)
        except Refused:
            raise Refused("grant_mount_forbidden") from None
        path = mount.host_path
        if (
            not path.startswith("/")
            or path != os.path.normpath(path)
            or any(character in path for character in ",=\n\0")
        ):
            raise Refused("grant_mount_forbidden")
        if any(_within(path, other) for other in (*FORBIDDEN, *protected)):
            raise Refused("grant_mount_forbidden")
        if mount.name in names or path in paths:
            raise Refused("grant_mount_forbidden")
        names.add(mount.name)
        paths.add(path)


class GrantPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    enabled: bool = False
    profiles: list[str] = Field(default_factory=list, max_length=4)
    input_set_ids: list[str] = Field(default_factory=list, max_length=32)
    capabilities: list[
        Literal[
            "create_residents",
            "assign_work",
            "routines",
            "author_skills",
            "update_residents",
            "manage_lifecycle",
            "assign_skills",
            "writable_memory",
            "send_letters",
        ]
    ] = Field(default_factory=list, max_length=9)
    # Whom this resident may write to. Empty means every resident that opens its own
    # letters door; a listed set narrows that and never widens anything else.
    letter_recipient_ids: list[str] = Field(default_factory=list, max_length=20)
    # What this resident reaches on disk. A bounded list, read-only by default, refused
    # at write time for anything Hearth's own machinery lives in
    # (`docs/adr/0016-sandbox-per-run.md`). It is not gated by `enabled`: reaching a
    # folder is not a management tool, and a resident with no management authority at
    # all may still be given one to read.
    mounts: list[Mount] = Field(default_factory=list, max_length=16)
    max_residents: int = Field(default=5, ge=0, le=20)
    max_daily_limit: int = Field(default=1_000_000, ge=0, le=10_000_000)
    max_reserve: int = Field(default=500_000, ge=1, le=2_000_000)
    max_calls: int = Field(default=64, ge=1, le=64)


class GrantPut(GrantPolicy):
    expected_revision: int = Field(ge=0)


def digest(value) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def read_grant(db, resident_id: str) -> dict:
    identifier(resident_id)
    if not db.execute("SELECT 1 FROM residents WHERE id=?", (resident_id,)).fetchone():
        raise Refused("resident_not_found")
    row = db.execute(
        "SELECT g.revision,r.policy,r.sha256 FROM management_grants g "
        "LEFT JOIN management_grant_revisions r ON r.resident_id=g.resident_id "
        "AND r.revision=g.revision WHERE g.resident_id=?",
        (resident_id,),
    ).fetchone()
    if row is None:
        return dict(resident_id=resident_id, revision=0, **GrantPolicy().model_dump())
    try:
        policy = GrantPolicy.model_validate(json.loads(row["policy"])).model_dump()
        if row["sha256"] != digest(policy):
            raise ValueError
    except ValueError, TypeError, ValidationError:
        raise Refused("management_grant_corrupt") from None
    return dict(resident_id=resident_id, revision=row["revision"], **policy)


class Management:
    def __init__(self, hearth: Hearth, protected: Iterable[str] = ()):
        self.hearth = hearth
        # What this installation refuses to let any grant mount. It is passed in
        # rather than discovered here because it is host configuration -- where the
        # store is, which logins were opened, which container runtime is being
        # driven -- and this module is the store's own rules.
        self.protected = tuple(protected)

    def read(self, resident_id: str) -> dict:
        with self.hearth.database.transaction() as db:
            return read_grant(db, resident_id)

    def save(self, resident_id: str, request: dict) -> dict:
        with self.hearth.database.transaction(write=True) as db:
            return self.save_in_transaction(db, resident_id, request)

    def save_in_transaction(self, db, resident_id: str, request: dict) -> dict:
        try:
            body = GrantPut.model_validate(request)
        except ValidationError:
            raise Refused("invalid_management_policy") from None
        previous = read_grant(db, resident_id)
        if previous["revision"] != body.expected_revision:
            raise Refused("revision_conflict")
        from hearth.work.service import configured_runtime

        # A grant names the runtimes it reaches, and a store may be configured for
        # several of them. One this store was never configured for reaches nothing.
        if any(not configured_runtime(db, profile) for profile in body.profiles):
            raise Refused("management_profile_unavailable")
        for item in body.input_set_ids:
            read_input(db, item)
        # A named recipient is checked for shape, not existence: an operator may write
        # the allowlist before the resident exists, and the send itself refuses a
        # recipient that is missing, archived or does not accept letters.
        for item in body.letter_recipient_ids:
            identifier(item)
        for values in (
            body.profiles,
            body.input_set_ids,
            body.capabilities,
            body.letter_recipient_ids,
        ):
            if len(values) != len(set(values)):
                raise Refused("management_duplicate_scope")
        # What a resident reaches on disk is refused here, where an operator is writing
        # it, and never at admission: a run that waits on a grant nobody will fix is a
        # resident that never works again.
        check_mounts(body.mounts, self.protected)
        policy = body.model_dump(exclude={"expected_revision"})
        revision = previous["revision"] + 1
        db.execute(
            "INSERT INTO management_grants VALUES (?,?) ON CONFLICT(resident_id) "
            "DO UPDATE SET revision=excluded.revision",
            (resident_id, revision),
        )
        db.execute(
            "INSERT INTO management_grant_revisions VALUES (?,?,?,?)",
            (resident_id, revision, json.dumps(policy, sort_keys=True), digest(policy)),
        )
        result = dict(resident_id=resident_id, revision=revision, **policy)
        now = int(self.hearth.clock())
        _audit(
            db,
            "resident.management_granted",
            resident_id,
            now,
            {"actor": "operator", **result},
        )
        # A folder a resident may change is the one grant that alters the host, so it is
        # a fact of its own rather than a field inside a larger one. Only the writable
        # ones are named: the read-only reach is in the grant fact above.
        writable = [
            {"name": mount.name, "host_path": mount.host_path}
            for mount in body.mounts
            if mount.mode == "rw"
        ]
        if writable:
            _audit(
                db,
                "grant.mount_rw_granted",
                resident_id,
                now,
                {"actor": "operator", "revision": revision, "mounts": writable},
            )
        return result


def works_a_letter(db, run_id: str) -> bool:
    """Is the task this run was admitted for a letter someone is owed an answer to?"""
    return (
        db.execute(
            "SELECT 1 FROM letters l JOIN runs r ON r.task_id=l.task_id WHERE r.id=?", (run_id,)
        ).fetchone()
        is not None
    )


def pin_management(db, run_id: str, resident_id: str, now: int, *, memory_writable=False) -> None:
    """A run reaches the native tools with a grant, with writable memory, with post, or not.

    All of them pin a row here, because all of them need the tool transport; only the
    granted one pins a grant revision. A run pinned without one carries no management
    authority at all, whatever the operator grants afterwards.

    The letter half of the gate is exactly `run_letter_scope`'s `post`: the letter this run
    was admitted to answer, or any letter this resident already had an end of. A resident
    that has only ever received letters holds no grant and may still read its own post, and
    a run with no row here would be launched with no native surface to read it on.
    """
    from hearth.work.letters import holds_post

    grant = read_grant(db, resident_id)
    if grant["enabled"]:
        policy = {
            key: value for key, value in grant.items() if key not in {"resident_id", "revision"}
        }
        db.execute(
            "INSERT INTO run_management VALUES (?,?,?,?,?,NULL,NULL,NULL,NULL)",
            (run_id, resident_id, grant["revision"], digest(policy), now + 600),
        )
    elif memory_writable or works_a_letter(db, run_id) or holds_post(db, run_id):
        db.execute(
            "INSERT INTO run_management VALUES (?,?,NULL,NULL,?,NULL,NULL,NULL,NULL)",
            (run_id, resident_id, now + 600),
        )


def validate_management(db) -> None:
    from hearth.work.letters import holds_post

    for row in db.execute("SELECT * FROM management_grant_revisions"):
        try:
            policy = GrantPolicy.model_validate(json.loads(row["policy"])).model_dump()
            if digest(policy) != row["sha256"]:
                raise ValueError
        except ValueError, TypeError:
            raise Refused("management_grant_corrupt") from None
    for row in db.execute(
        "SELECT p.*,r.resident_id AS owner,r.created_at,g.sha256,d.memory_writable,"
        "EXISTS(SELECT 1 FROM letters l WHERE l.task_id=r.task_id) AS letter "
        "FROM run_management p JOIN runs r ON r.id=p.run_id "
        "LEFT JOIN management_grant_revisions g "
        "ON g.resident_id=p.resident_id AND g.revision=p.grant_revision "
        "LEFT JOIN declarations d ON d.resident_id=r.resident_id AND d.revision=r.resident_revision"
    ).fetchall():
        if (
            row["resident_id"] != row["owner"]
            or row["grant_sha256"] != row["sha256"]
            or row["expires_at"] != row["created_at"] + 600
        ):
            raise Refused("management_admission_changed")
        # A pin without a grant revision exists only for a run that could write its own
        # memory, answer the letter it was admitted for, or read its own post.
        if (
            row["grant_revision"] is None
            and not row["memory_writable"]
            and not row["letter"]
            and not holds_post(db, row["run_id"])
        ):
            raise Refused("management_admission_changed")


def management_summary(db, identity: str, *, run: bool = False) -> dict | None:
    try:
        if run:
            # The protocol is the run's own runtime's, read from the pin the run keeps,
            # because that is the transport the tools really travelled on.
            row = db.execute(
                "SELECT m.grant_revision,m.expires_at,r.runtime_kind FROM run_management m "
                "JOIN runs r ON r.id=m.run_id WHERE m.run_id=?",
                (identity,),
            ).fetchone()
            # A memory-only pin is not management authority and is never reported as any.
            if row is None or row["grant_revision"] is None:
                return None
            calls = db.execute(
                "SELECT COUNT(*) FROM management_calls WHERE run_id=?", (identity,)
            ).fetchone()[0]
            return {
                "grant_revision": row["grant_revision"],
                "expires_at": row["expires_at"],
                "calls": calls,
                "protocol": management_protocol(row["runtime_kind"]),
            }
        return read_grant(db, identity)
    except Refused as error:
        return {"error": error.code, "enabled": False}
