"""Explicit operator grants; instruction text never creates management authority."""

import hashlib
import json
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from hearth.inputs.catalog import read_input
from hearth.residents.models import Refused, identifier
from hearth.work.service import Hearth, _audit


class GrantPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    enabled: bool = False
    profiles: list[str] = Field(default_factory=list, max_length=4)
    input_set_ids: list[str] = Field(default_factory=list, max_length=32)
    capabilities: list[Literal["create_residents", "assign_work", "routines"]] = Field(
        default_factory=list, max_length=3
    )
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
    def __init__(self, hearth: Hearth):
        self.hearth = hearth

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
        configured = db.execute(
            "SELECT value FROM system_meta WHERE key='runtime_kind'"
        ).fetchone()[0]
        if any(profile != configured for profile in body.profiles):
            raise Refused("management_profile_unavailable")
        for item in body.input_set_ids:
            read_input(db, item)
        for values in (body.profiles, body.input_set_ids, body.capabilities):
            if len(values) != len(set(values)):
                raise Refused("management_duplicate_scope")
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
        _audit(
            db,
            "resident.management_granted",
            resident_id,
            int(self.hearth.clock()),
            {"actor": "operator", **result},
        )
        return result


def pin_management(db, run_id: str, resident_id: str, now: int) -> None:
    grant = read_grant(db, resident_id)
    if grant["enabled"]:
        policy = {
            key: value for key, value in grant.items() if key not in {"resident_id", "revision"}
        }
        db.execute(
            "INSERT INTO run_management VALUES (?,?,?,?,?,NULL,NULL,NULL,NULL)",
            (run_id, resident_id, grant["revision"], digest(policy), now + 600),
        )


def validate_management(db) -> None:
    for row in db.execute("SELECT * FROM management_grant_revisions"):
        try:
            policy = GrantPolicy.model_validate(json.loads(row["policy"])).model_dump()
            if digest(policy) != row["sha256"]:
                raise ValueError
        except ValueError, TypeError:
            raise Refused("management_grant_corrupt") from None
    for row in db.execute(
        "SELECT p.*,r.resident_id AS owner,r.created_at,g.sha256 FROM run_management p "
        "JOIN runs r ON r.id=p.run_id LEFT JOIN management_grant_revisions g "
        "ON g.resident_id=p.resident_id AND g.revision=p.grant_revision"
    ):
        if (
            row["resident_id"] != row["owner"]
            or row["grant_sha256"] != row["sha256"]
            or row["expires_at"] != row["created_at"] + 600
        ):
            raise Refused("management_admission_changed")


def management_summary(db, identity: str, *, run: bool = False) -> dict | None:
    try:
        if run:
            row = db.execute(
                "SELECT grant_revision,expires_at FROM run_management WHERE run_id=?", (identity,)
            ).fetchone()
            if row is None:
                return None
            calls = db.execute(
                "SELECT COUNT(*) FROM management_calls WHERE run_id=?", (identity,)
            ).fetchone()[0]
            return dict(row) | {"calls": calls, "protocol": "native_management"}
        return read_grant(db, identity)
    except Refused as error:
        return {"error": error.code, "enabled": False}
