"""Immutable authoring examples and structural checks beside the normal skill revision."""

import hashlib
import json
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from hearth.residents.models import Refused


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class Assertions(Strict):
    max_characters: int = Field(ge=1, le=8000)
    contains: list[str] = Field(default_factory=list, max_length=4)
    excludes: list[str] = Field(default_factory=list, max_length=4)


class Example(Strict):
    kind: Literal["normal", "edge"]
    instruction: str = Field(min_length=1, max_length=4000)
    notes: list[str] = Field(max_length=4)
    assertions: Assertions


class Authoring(Strict):
    examples: list[Example] = Field(min_length=2, max_length=2)


SECTIONS = (
    "When to use",
    "When not to use",
    "Inputs",
    "Procedure",
    "Expected output",
    "Uncertainty and failure",
    "Success criteria",
)


def digest(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def manifest(value) -> dict:
    try:
        result = Authoring.model_validate(value).model_dump()
        if [case["kind"] for case in result["examples"]] != ["normal", "edge"]:
            raise ValueError
        for case in result["examples"]:
            for note in case["notes"]:
                if not note.strip() or len(note) > 2000:
                    raise ValueError
            assertions = case["assertions"]
            if not assertions["contains"] and not assertions["excludes"]:
                raise ValueError
            for phrase in assertions["contains"] + assertions["excludes"]:
                if not phrase.strip() or len(phrase) > 200:
                    raise ValueError
        json.dumps(result, ensure_ascii=False).encode()
    except ValueError, TypeError, ValidationError:
        raise Refused("skill_examples_invalid") from None
    return result


def structure(instructions: str, examples: dict) -> dict:
    sections = re.split(r"(?m)^#{1,6}\s+", instructions)
    present = {}
    for section in sections[1:]:
        heading, _, body = section.partition("\n")
        present[heading.strip().casefold()] = bool(body.strip())
    reasons = [
        "Missing populated section: " + name
        for name in SECTIONS
        if not present.get(name.casefold())
    ]
    normal, edge = examples["examples"]
    if not normal["notes"] or not normal["assertions"]["contains"]:
        reasons.append("The normal example needs synthetic notes and an expected phrase.")
    if edge["notes"] and not edge["assertions"]["excludes"]:
        reasons.append("An adversarial example needs an explicit forbidden output phrase.")
    if not edge["notes"] and not edge["assertions"]["contains"]:
        reasons.append("An empty-input example needs an explicit expected missing-input phrase.")
    return {"checker": "structure-v1", "passed": not reasons, "reasons": reasons}


def read_authoring(db, skill_id: str, revision: int) -> dict | None:
    row = db.execute(
        "SELECT * FROM skill_authoring_revisions WHERE skill_id=? AND revision=?",
        (skill_id, revision),
    ).fetchone()
    if row is None:
        return None
    value = manifest(json.loads(row["manifest"]))
    if digest(value) != row["sha256"]:
        raise Refused("skill_examples_changed")
    from hearth.skills.validation import read_validation

    publication = db.execute(
        "SELECT * FROM skill_publications WHERE skill_id=? AND revision=?",
        (skill_id, revision),
    ).fetchone()
    candidate_revision = publication["candidate_revision"] if publication else revision
    validation = db.execute(
        "SELECT id FROM skill_validations WHERE skill_id=? AND candidate_revision=?",
        (skill_id, candidate_revision),
    ).fetchone()
    return {
        "examples": value["examples"],
        "manifest_sha256": row["sha256"],
        "structure": json.loads(row["structure"]),
        "validation": read_validation(db, validation[0]) if validation else None,
        "publication": dict(publication) if publication else None,
    }


def decorate(db, revision: dict) -> dict:
    for field in ("created_by", "edited_by"):
        actor = revision[field]
        name = db.execute(
            "SELECT d.name FROM residents r JOIN declarations d "
            "ON d.resident_id=r.id AND d.revision=r.revision WHERE r.id=?",
            (actor,),
        ).fetchone()
        revision[field + "_name"] = (
            "Operator" if actor == "operator" else name[0] if name else actor
        )
    evidence = read_authoring(db, revision["skill_id"], revision["revision"])
    return revision | {"authoring": evidence} if evidence is not None else revision


def check_editor(db, skill_id: str, actor: str) -> None:
    row = db.execute("SELECT created_by FROM skills WHERE id=?", (skill_id,)).fetchone()
    if row is None:
        raise Refused("skill_not_found")
    if actor != "operator" and row[0] != actor:
        raise Refused("skill_editor_not_authorized")


def record(db, skill_id, revision, instructions, value):
    checked = manifest(value)
    db.execute(
        "INSERT INTO skill_authoring_revisions VALUES (?,?,?,?,?)",
        (
            skill_id,
            revision,
            json.dumps(checked, sort_keys=True),
            digest(checked),
            json.dumps(structure(instructions, checked), sort_keys=True),
        ),
    )
