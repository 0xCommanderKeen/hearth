"""Definition-only resident bundles: export one resident's content, import it elsewhere.

A bundle carries what defines a resident (declaration, memory, exact skill and input
content, one routine) and nothing that happened to it (runs, artifacts, usage, audit,
lifecycle, receipts). Skill and input identities are per-database, so the bundle
embeds content and its existing digest; import reuses catalog entries whose digest
matches and creates the rest. A carried management grant is informational only and
is never applied on import. Import is ordinary provisioning, so it keeps that path's
capacity checks, savepoint rollback, durable receipt and retry semantics without any
schema change.
"""

import hashlib
import json
import re
from typing import Literal

from pydantic import Field, ValidationError

from hearth.inputs.catalog import Inputs
from hearth.inputs.catalog import content_digest as input_digest
from hearth.inputs.selection import read_selection
from hearth.management.authority import GrantPolicy, read_grant
from hearth.residents.maintenance import Maintenance
from hearth.residents.models import Refused, identifier
from hearth.residents.provisioning import (
    Provisioning,
    RoutineSetup,
    Strict,
    profile_summary,
)
from hearth.skills.assignments import read_assignments
from hearth.skills.catalog import Skills
from hearth.skills.catalog import content_digest as skill_digest
from hearth.work.service import Hearth

BUNDLE_VERSION = 1
SUFFIX = ".hearth-resident.json"
IMPORTED_REASON = "Imported resident bundle"


class BundleSource(Strict):
    resident_id: str
    declaration_revision: int = Field(ge=1)
    memory_revision: int = Field(ge=0)
    exported_at: int = Field(ge=0)


class BundleResident(Strict):
    name: str = Field(min_length=1, max_length=100)
    purpose: str = Field(min_length=1, max_length=8000)
    instructions: str = Field(default="", max_length=32000)
    memory: str = Field(default="", max_length=131072)
    daily_limit: int = Field(ge=0)
    budget_timezone: str = "Europe/Ljubljana"
    execution_profile: str = Field(min_length=1, max_length=100)
    creation_reason: str = Field(min_length=1, max_length=2000)


class BundleSkill(Strict):
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=2000)
    instructions: str = Field(default="", max_length=32000)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class BundleInput(Strict):
    name: str = Field(min_length=1, max_length=120)
    notes: list[str] = Field(default_factory=list, max_length=32)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class ResidentBundle(Strict):
    bundle_version: Literal[1]
    source: BundleSource
    resident: BundleResident
    skills: list[BundleSkill] = Field(default_factory=list, max_length=8)
    input_sets: list[BundleInput] = Field(default_factory=list, max_length=4)
    routine: RoutineSetup | None = None
    management: GrantPolicy | None = None


class ImportOverrides(Strict):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    daily_limit: int | None = Field(default=None, ge=0)
    budget_timezone: str | None = Field(default=None, min_length=1, max_length=100)


class ImportRequest(Strict):
    bundle: ResidentBundle
    overrides: ImportOverrides | None = None
    manager: str = "operator"


def file_name(name: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9_-]+", "-", name).strip("-").lower()
    return (safe or "resident") + SUFFIX


def export_in_transaction(db, hearth: Hearth, resident_id: str) -> dict:
    identifier(resident_id)
    configuration = Maintenance(hearth).configuration_in_transaction(db, resident_id)
    if configuration["lifecycle"].get("state") == "unavailable":
        raise Refused("resident_lifecycle_unavailable")
    if len(configuration["routines"]) > 1:
        raise Refused("bundle_routines_unsupported")
    profile = profile_summary(db, resident_id)
    if profile is None:
        raise Refused("resident_profile_not_found")
    assignments = read_assignments(db, resident_id)
    selection = read_selection(db, resident_id)
    grant = read_grant(db, resident_id)
    declaration = configuration["declaration"]
    routine = configuration["routines"][0] if configuration["routines"] else None
    bundle = {
        "bundle_version": BUNDLE_VERSION,
        "source": {
            "resident_id": resident_id,
            "declaration_revision": declaration["expected_revision"],
            "memory_revision": configuration["memory"]["expected_revision"],
            "exported_at": int(hearth.clock()),
        },
        "resident": {
            "name": declaration["name"],
            "purpose": declaration["purpose"],
            "instructions": declaration["instructions"],
            "memory": configuration["memory"]["text"],
            "daily_limit": declaration["daily_limit"],
            "budget_timezone": declaration["budget_timezone"],
            "execution_profile": profile["execution_profile"],
            "creation_reason": profile["creation_reason"],
        },
        "skills": [
            {key: entry[key] for key in ("name", "description", "instructions", "sha256")}
            for entry in assignments["skills"]
        ],
        "input_sets": [
            {key: entry[key] for key in ("name", "notes", "sha256")}
            for entry in selection["input_sets"]
        ],
        "routine": None
        if routine is None
        else {key: routine[key] for key in ("instruction", "local_time", "timezone", "enabled")},
        "management": None
        if grant["revision"] == 0
        else GrantPolicy.model_validate(
            {key: grant[key] for key in GrantPolicy.model_fields}
        ).model_dump(),
    }
    try:
        ResidentBundle.model_validate(bundle)
    except ValidationError:
        raise Refused("bundle_export_invalid") from None
    return bundle


def _existing_skill(db, sha256: str):
    return db.execute(
        "SELECT r.skill_id, r.revision FROM skill_revisions r "
        "JOIN skills s ON s.id=r.skill_id "
        "JOIN skill_revisions cur ON cur.skill_id=s.id AND cur.revision=s.revision "
        "WHERE r.sha256=? AND r.status='active' AND cur.status!='archived' "
        "ORDER BY r.skill_id, r.revision LIMIT 1",
        (sha256,),
    ).fetchone()


def _existing_input(db, sha256: str):
    return db.execute(
        "SELECT s.id, s.revision FROM input_sets s JOIN input_revisions r "
        "ON r.input_set_id=s.id AND r.revision=s.revision WHERE r.sha256=? "
        "ORDER BY s.id LIMIT 1",
        (sha256,),
    ).fetchone()


def import_in_transaction(
    db, hearth: Hearth, command_id: str, request: dict, *, actor: str = "operator"
) -> dict:
    """Materialise bundle content, then provision through the ordinary path."""
    identifier(command_id)
    identifier(actor)
    try:
        body = ImportRequest.model_validate(request)
    except ValidationError:
        raise Refused("invalid_resident_bundle") from None
    bundle = body.bundle
    for skill in bundle.skills:
        if skill_digest(skill.model_dump()) != skill.sha256:
            raise Refused("bundle_digest_mismatch")
    for item in bundle.input_sets:
        if input_digest(item.name, item.notes) != item.sha256:
            raise Refused("bundle_digest_mismatch")
    inner = hashlib.sha256(command_id.encode()).hexdigest()[:32]
    resolution: dict = {"skills": [], "input_sets": []}
    skill_refs = []
    for position, skill in enumerate(bundle.skills):
        row = _existing_skill(db, skill.sha256)
        if row:
            skill_id, revision, outcome = row[0], row[1], "reused"
        else:
            receipt = Skills(hearth).save_in_transaction(
                db,
                f"import-skill:{inner}:{position}",
                name=skill.name,
                description=skill.description,
                instructions=skill.instructions,
                actor=actor,
            )
            skill_id, revision, outcome = receipt["skill_id"], receipt["revision"], "created"
        skill_refs.append({"skill_id": skill_id, "revision": revision})
        resolution["skills"].append(
            {"name": skill.name, "skill_id": skill_id, "revision": revision, "outcome": outcome}
        )
    input_refs = []
    for position, item in enumerate(bundle.input_sets):
        row = _existing_input(db, item.sha256)
        if row:
            input_set_id, revision, outcome = row[0], row[1], "reused"
        else:
            receipt = Inputs(hearth).save_in_transaction(
                db,
                f"import-input:{inner}:{position}",
                name=item.name,
                notes=item.notes,
                actor=actor,
            )
            input_set_id, revision, outcome = (
                receipt["input_set_id"],
                receipt["revision"],
                "created",
            )
        input_refs.append({"input_set_id": input_set_id})
        resolution["input_sets"].append(
            {
                "name": item.name,
                "input_set_id": input_set_id,
                "revision": revision,
                "outcome": outcome,
            }
        )
    configured = db.execute("SELECT value FROM system_meta WHERE key='runtime_kind'").fetchone()[0]
    overrides = body.overrides or ImportOverrides()
    resident = bundle.resident
    setup = {
        "name": overrides.name or resident.name,
        "purpose": resident.purpose,
        "instructions": resident.instructions,
        "initial_memory": resident.memory,
        "skills": skill_refs,
        "execution_profile": configured,
        "input_sets": input_refs,
        "daily_limit": resident.daily_limit
        if overrides.daily_limit is None
        else overrides.daily_limit,
        "budget_timezone": overrides.budget_timezone or resident.budget_timezone,
        "creation_reason": f"{IMPORTED_REASON}: {resident.creation_reason}"[:2000],
        "manager": body.manager,
        "routine": None if bundle.routine is None else bundle.routine.model_dump(),
        "first_assignment": None,
    }
    receipt = Provisioning(hearth).create_in_transaction(db, command_id, setup, actor=actor)
    resolution["execution_profile"] = {
        "requested": resident.execution_profile,
        "used": configured,
    }
    resolution["management_ignored"] = bundle.management is not None
    return receipt | {"resolution": resolution}


class Bundles:
    def __init__(self, hearth: Hearth):
        self.hearth = hearth

    def export(self, resident_id: str) -> dict:
        with self.hearth.database.transaction() as db:
            return export_in_transaction(db, self.hearth, resident_id)

    def import_(self, command_id: str, request: dict, *, actor: str = "operator") -> dict:
        with self.hearth.database.transaction(write=True) as db:
            return import_in_transaction(db, self.hearth, command_id, request, actor=actor)


def load_bundle_file(path) -> dict:
    """Read a bundle file with the same transport bound as the HTTP route."""
    with open(path, "rb") as file:
        data = file.read(1_500_001)
    if len(data) > 1_500_000:
        raise Refused("bundle_file_too_large")
    try:
        value = json.loads(data)
    except ValueError:
        raise Refused("invalid_resident_bundle") from None
    if not isinstance(value, dict):
        raise Refused("invalid_resident_bundle")
    return value
