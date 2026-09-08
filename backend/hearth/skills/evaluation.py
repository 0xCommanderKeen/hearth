"""Trusted case-to-task bindings and checks of ordinary saved execution evidence."""

import hashlib
import json

from hearth.execution.context import CONTEXT_VERSION
from hearth.inputs.catalog import read_input
from hearth.management.authority import read_grant
from hearth.residents.models import Refused
from hearth.skills.authoring import digest, manifest, structure
from hearth.storage.artifacts import Artifact, Artifacts


def request_context(db, resident_id):
    """What a validation pins about the resident that asked for it, at request time."""
    declaration = db.execute(
        "SELECT d.* FROM declarations d JOIN residents r ON r.id=d.resident_id "
        "AND r.revision=d.revision WHERE r.id=?",
        (resident_id,),
    ).fetchone()
    if declaration is None:
        raise Refused("resident_not_found")
    memory = db.execute(
        "SELECT MAX(revision) FROM memory_revisions WHERE resident_id=?", (resident_id,)
    ).fetchone()[0]
    if memory is None:
        raise Refused("skill_validation_resident_incomplete")
    return {
        "resident_revision": declaration["revision"],
        "memory_revision": memory,
        "daily_limit": declaration["daily_limit"],
    }


def run_context(db, run):
    """What an example run actually carried, read from the run's own immutable pins."""
    declaration = db.execute(
        "SELECT daily_limit FROM declarations WHERE resident_id=? AND revision=?",
        (run["resident_id"], run["resident_revision"]),
    ).fetchone()
    memory = db.execute(
        "SELECT m.revision,m.sha256 FROM run_memory p JOIN memory_revisions m "
        "ON m.resident_id=p.resident_id AND m.revision=p.revision "
        "WHERE p.run_id=? AND p.resident_id=?",
        (run["id"], run["resident_id"]),
    ).fetchone()
    if declaration is None or memory is None:
        raise Refused("skill_evaluation_run_changed")
    return {
        "resident_revision": run["resident_revision"],
        "memory_revision": memory["revision"],
        "memory_sha256": memory["sha256"],
        "daily_limit": declaration["daily_limit"],
    }


def case_validation(db, run_id):
    """The validation this run is an example case of, or nothing for ordinary work."""
    return db.execute(
        "SELECT v.* FROM skill_validation_cases c "
        "JOIN skill_validations v ON v.id=c.validation_id "
        "JOIN runs r ON r.task_id=c.task_id WHERE r.id=?",
        (run_id,),
    ).fetchone()


REQUEST_FIELDS = (
    "id",
    "skill_id",
    "candidate_revision",
    "candidate_sha256",
    "manifest_sha256",
    "resident_id",
    "resident_revision",
    "memory_revision",
    "context_version",
    "actor",
    "originating_run_id",
    "grant_revision",
    "reserve",
    "created_at",
    "expires_at",
)


def checked_validation(db, validation_id):
    from hearth.skills.assignments import exact_skill

    row = db.execute("SELECT * FROM skill_validations WHERE id=?", (validation_id,)).fetchone()
    if row is None:
        raise Refused("skill_validation_not_found")
    value = dict(row)
    if digest({key: value[key] for key in REQUEST_FIELDS}) != value["request_sha256"]:
        raise Refused("skill_validation_changed")
    candidate = exact_skill(db, value["skill_id"], value["candidate_revision"])
    authored = db.execute(
        "SELECT * FROM skill_authoring_revisions WHERE skill_id=? AND revision=?",
        (value["skill_id"], value["candidate_revision"]),
    ).fetchone()
    if authored is None or candidate["status"] != "draft":
        raise Refused("skill_validation_candidate_changed")
    examples = manifest(json.loads(authored["manifest"]))
    if (
        candidate["sha256"] != value["candidate_sha256"]
        or digest(examples) != authored["sha256"]
        or authored["sha256"] != value["manifest_sha256"]
        or structure(candidate["instructions"], examples) != json.loads(authored["structure"])
    ):
        raise Refused("skill_validation_candidate_changed")
    return value, candidate, examples


def check_request_authority(db, validation, now):
    if now >= validation["expires_at"]:
        raise Refused("skill_validation_expired")
    if validation["actor"] != "operator":
        grant = read_grant(db, validation["actor"])
        runtime = db.execute("SELECT value FROM system_meta WHERE key='runtime_kind'").fetchone()[0]
        if (
            not grant["enabled"]
            or grant["revision"] != validation["grant_revision"]
            or "author_skills" not in grant["capabilities"]
            or runtime not in grant["profiles"]
            or validation["reserve"] > grant["max_reserve"]
        ):
            raise Refused("skill_validation_authority_changed")
    # An example carries the declaration text the request pinned, because that text is
    # part of the context the case runs in; an edit to it would answer the question for
    # a different resident. A budget or name change is not that, and does not block.
    # Memory needs no check at all: admission pins the exact revision the request named,
    # so the resident's ordinary writing never moves the case.
    promised = db.execute(
        "SELECT purpose,skill_text FROM declarations WHERE resident_id=? AND revision=?",
        (validation["resident_id"], validation["resident_revision"]),
    ).fetchone()
    current = db.execute(
        "SELECT d.purpose,d.skill_text FROM declarations d JOIN residents r "
        "ON r.id=d.resident_id AND r.revision=d.revision WHERE r.id=?",
        (validation["resident_id"],),
    ).fetchone()
    if promised is None or current is None or tuple(promised) != tuple(current):
        raise Refused("skill_validation_resident_changed")


def case_binding(db, run_id, resident_id):
    """Only a previously authorized durable case task can pin a draft at admission."""
    case = db.execute(
        "SELECT c.* FROM skill_validation_cases c JOIN runs r ON r.task_id=c.task_id WHERE r.id=?",
        (run_id,),
    ).fetchone()
    if case is None:
        return None
    validation, candidate, examples = checked_validation(db, case["validation_id"])
    run = db.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
    check_request_authority(db, validation, run["created_at"])
    if (
        validation["status"] != "pending"
        or validation["resident_id"] != resident_id
        or case["run_id"] not in {None, run_id}
        or case["result"] is not None
    ):
        raise Refused("skill_evaluation_binding_invalid")
    if case["position"] == 1:
        previous = db.execute(
            "SELECT result FROM skill_validation_cases WHERE validation_id=? AND position=0",
            (validation["id"],),
        ).fetchone()
        if previous is None or previous[0] is None or not json.loads(previous[0])["passed"]:
            raise Refused("skill_evaluation_previous_case_pending")
    example = examples["examples"][case["position"]]
    task = db.execute("SELECT * FROM tasks WHERE id=?", (case["task_id"],)).fetchone()
    source = read_input(db, case["input_set_id"], case["input_revision"])
    if (
        task["resident_id"] != resident_id
        or task["instruction"] != example["instruction"]
        or source["sha256"] != case["input_sha256"]
        or source["notes"] != example["notes"]
    ):
        raise Refused("skill_evaluation_input_changed")
    return {"candidate": candidate, "input": source}


def result_for_case(db, validation, case, artifacts: Artifacts):
    from hearth.inputs.selection import run_inputs
    from hearth.skills.assignments import run_skills

    _, candidate, manifest_value = checked_validation(db, validation["id"])
    example = manifest_value["examples"][case["position"]]
    run = db.execute("SELECT * FROM runs WHERE id=?", (case["run_id"],)).fetchone()
    if (
        run is None
        or run["task_id"] != case["task_id"]
        or run["resident_id"] != validation["resident_id"]
    ):
        raise Refused("skill_evaluation_run_changed")
    pins = run_context(db, run)
    # A request names the memory revision its cases must carry. A store upgraded from
    # before this release named none, and its cases are checked by their own pins alone.
    if validation["memory_revision"] not in {None, pins["memory_revision"]}:
        raise Refused("skill_evaluation_run_changed")
    # An example run reaches no management tools at all: admission pins none for it, so
    # a candidate that asks for authority has nothing to ask.
    if db.execute("SELECT 1 FROM run_management WHERE run_id=?", (run["id"],)).fetchone():
        raise Refused("skill_examples_must_be_read_only")
    # Rebuilding the pinned context is only evidence while this release still builds it
    # the same way. A validation from an older context version keeps the digest its run
    # reserved against, checked above through the run's own pins, and is not rebuilt.
    if validation["context_version"] == CONTEXT_VERSION:
        from hearth.execution.context import read_context
        from hearth.residents.memory import MemoryFiles

        context = read_context(db, run["id"], MemoryFiles(artifacts.root.parent / "memory"))
        context_sha = hashlib.sha256(
            json.dumps(context, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        if context_sha != run["input_digest"]:
            raise Refused("skill_evaluation_context_changed")
    skills = run_skills(db, run["id"])
    inputs = run_inputs(db, run["id"])
    if (
        len(skills) != 1
        or skills[0]["skill_id"] != candidate["skill_id"]
        or skills[0]["revision"] != candidate["revision"]
        or skills[0]["sha256"] != candidate["sha256"]
        or len(inputs) != 1
        or inputs[0]["sha256"] != case["input_sha256"]
        or inputs[0]["input_set_id"] != case["input_set_id"]
        or inputs[0]["revision"] != case["input_revision"]
        or inputs[0]["notes"] != example["notes"]
    ):
        raise Refused("skill_evaluation_pins_changed")
    if run["finished_at"] is None:
        return None
    if not run["usage_known"]:
        raise Refused("skill_evaluation_usage_unknown")
    from hearth.execution.usage import pricing, verify_stored

    if pricing(db, run["id"]) is not None:
        verify_stored(db, run)
    base = (
        dict(
            checker="output-assertions-v1",
            run_id=run["id"],
            actual_cost=run["actual_cost"],
            passed=False,
            artifact_id=None,
            artifact_sha256=None,
            checks=[],
            reasons=[],
            input_digest=run["input_digest"],
        )
        | pins
    )
    if run["status"] != "succeeded":
        return base | {"reasons": ["Evaluation run ended: " + run["status"]]}
    row = db.execute(
        "SELECT * FROM artifacts WHERE id=? AND run_id=?", (run["artifact_id"], run["id"])
    ).fetchone()
    if row is None:
        raise Refused("skill_evaluation_artifact_missing")
    output = artifacts.read(Artifact(**dict(row)))
    assertions = example["assertions"]
    checks: list[dict] = [
        {
            "assertion": "max_characters",
            "expected": assertions["max_characters"],
            "actual": len(output),
            "passed": len(output) <= assertions["max_characters"],
        }
    ]
    checks.extend(
        {"assertion": "contains", "expected": phrase, "passed": phrase in output}
        for phrase in assertions["contains"]
    )
    checks.extend(
        {"assertion": "excludes", "expected": phrase, "passed": phrase not in output}
        for phrase in assertions["excludes"]
    )
    return base | {
        "artifact_id": row["id"],
        "artifact_sha256": row["sha256"],
        "checks": checks,
        "passed": all(check["passed"] for check in checks),
        "reasons": [
            "Output failed " + check["assertion"] for check in checks if not check["passed"]
        ],
    }


def verify_authoring_backup(db, root):
    """Validate candidate/case/publication identities in a current-data held snapshot."""
    from hearth.skills.assignments import exact_skill

    artifacts = Artifacts(root / "artifacts")
    for row in db.execute("SELECT * FROM skill_authoring_revisions"):
        candidate = exact_skill(db, row["skill_id"], row["revision"])
        examples = manifest(json.loads(row["manifest"]))
        if digest(examples) != row["sha256"] or structure(
            candidate["instructions"], examples
        ) != json.loads(row["structure"]):
            raise Refused("skill_examples_changed")
        if (
            candidate["status"] == "active"
            and not db.execute(
                "SELECT 1 FROM skill_publications WHERE skill_id=? AND revision=?",
                (row["skill_id"], row["revision"]),
            ).fetchone()
        ):
            raise Refused("skill_publication_missing")
    for row in db.execute("SELECT id FROM skill_validations"):
        validation, candidate, examples = checked_validation(db, row[0])
        cases = list(
            db.execute(
                "SELECT * FROM skill_validation_cases WHERE validation_id=? ORDER BY position",
                (row[0],),
            )
        )
        structural = structure(candidate["instructions"], examples)["passed"]
        if not structural:
            if cases or validation["status"] != "failed" or validation["resident_id"] is not None:
                raise Refused("skill_validation_cases_changed")
            continue
        if [case["position"] for case in cases] != [0, 1]:
            raise Refused("skill_validation_cases_changed")
        for case in cases:
            example = examples["examples"][case["position"]]
            task = db.execute("SELECT * FROM tasks WHERE id=?", (case["task_id"],)).fetchone()
            source = read_input(db, case["input_set_id"], case["input_revision"])
            if (
                task is None
                or task["resident_id"] != validation["resident_id"]
                or task["instruction"] != example["instruction"]
                or source["sha256"] != case["input_sha256"]
                or source["notes"] != example["notes"]
            ):
                raise Refused("skill_evaluation_input_changed")
            runs = list(db.execute("SELECT id FROM runs WHERE task_id=?", (case["task_id"],)))
            if [run[0] for run in runs] != ([case["run_id"]] if case["run_id"] else []):
                raise Refused("skill_evaluation_run_changed")
            if case["result"] is not None:
                actual = result_for_case(db, validation, case, artifacts)
                if actual is None or actual != json.loads(case["result"]):
                    raise Refused("skill_evaluation_result_changed")
            if validation["status"] == "passed" and (
                case["result"] is None or not json.loads(case["result"])["passed"]
            ):
                raise Refused("skill_evaluation_result_changed")
    for row in db.execute("SELECT * FROM skill_publications"):
        validation, candidate, _ = checked_validation(db, row["validation_id"])
        published = exact_skill(db, row["skill_id"], row["revision"])
        if (
            validation["status"] != "passed"
            or row["skill_id"] != candidate["skill_id"]
            or row["candidate_revision"] != candidate["revision"]
            or published["status"] != "active"
            or published["sha256"] != candidate["sha256"]
            or row["sha256"] != candidate["sha256"]
        ):
            raise Refused("skill_publication_changed")
