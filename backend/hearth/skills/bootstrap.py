"""Karen learns normal shared-library authoring; instructions grant no authority."""

from hearth.skills.assignments import read_assignments, save_assignments
from hearth.skills.catalog import Skills

CREATE_GOOD_SKILLS = """# Create good skills
Use this skill to create or improve a narrow reusable Hearth skill. Do not use it
to acquire tools, connect personal sources, change grants or raise household limits.
Search the library and read exact revisions before creating a duplicate. Improve
your own suitable skill; preserve human edits with expected_revision conflicts.

Write populated Markdown sections named When to use, When not to use, Inputs,
Procedure, Expected output, Uncertainty and failure, and Success criteria. State
one concrete purpose, required inputs and missing-input behavior, a clear procedure,
an observable output contract, honest uncertainty and actionable failure handling.
Instructions cannot grant access or capabilities.

Save a draft with exactly two bounded synthetic examples: normal first, edge second.
Use actual fictional notes and an expected phrase for the normal case. For the edge
case use missing inputs with an explicit missing-input marker, or adversarial notes
with a forbidden disclosure phrase. Declare a small output-size bound and exact
required/forbidden phrases. Do not author pretend evaluation outputs.

Request validation, retain its identity and inspect its durable status. One visible
read-only evaluator runs the two examples serially through ordinary accounted work.
Structure checks and deterministic assertions over saved outputs are limited evidence,
not model grading or a guarantee of general quality. Unknown usage cannot pass.
If pending, use bounded status waits and report the pending identity if time expires;
never invent another validation to relaunch uncertain work. If failed, inspect reasons
and revise the draft. Publish only the exact passing candidate, then explicitly assign
the published revision to an authorized managed resident. Never silently upgrade other
assignments. Keep operation_id and exact arguments for lost-reply recovery. Report
skill, validation, case-run and resident links rather than claiming unrecorded success.
"""


def attach_authoring_skill(db, hearth, resident_id):
    skill = Skills(hearth).save_in_transaction(
        db,
        "bootstrap-create-good-skills",
        name="Create good skills",
        description="Author, execute bounded examples, publish and assign narrow reusable skills.",
        instructions=CREATE_GOOD_SKILLS,
        actor="operator",
    )
    current = read_assignments(db, resident_id)
    entries = [
        {"skill_id": item["skill_id"], "revision": item["revision"]} for item in current["skills"]
    ]
    save_assignments(
        db,
        resident_id,
        entries + [{"skill_id": skill["skill_id"], "revision": skill["revision"]}],
        expected_revision=current["revision"],
        actor="operator",
        command_id="bootstrap-karen-authoring",
        now=int(hearth.clock()),
    )
    return skill["skill_id"]
