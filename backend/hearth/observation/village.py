"""Small, body-free projection of the latest native tool receipt per visible run.

Places describe recorded work, not physical occupancy or inferred task intent.
No new authority, events or persistent state are introduced.
"""

import json

# Exact Hearth tool names only. Free-form instructions and provider transcripts are
# never used to guess where a resident belongs.
ACTIONS = {
    "hearth_catalog": ("research", "Read the catalog"),
    "hearth_residents_read": ("research", "Read resident records"),
    "hearth_skills_read": ("research", "Read skill instructions"),
    "hearth_skills_assignments": ("research", "Read skill assignments"),
    "hearth_skills_validation": ("research", "Inspected validation records"),
    "hearth_memory_read": ("research", "Read memory"),
    "hearth_memory_save": ("workshop", "Saved memory"),
    "hearth_journal_write": ("workshop", "Wrote a journal entry"),
    "hearth_skills_save": ("workshop", "Saved a skill draft"),
    "hearth_skills_validate": ("workshop", "Requested skill validation"),
    "hearth_skills_publish": ("workshop", "Published a skill"),
    "hearth_skills_assign": ("townhall", "Assigned skills"),
    "hearth_residents_configuration": ("townhall", "Read resident configuration"),
    "hearth_residents_configure": ("townhall", "Updated resident configuration"),
    "hearth_residents_lifecycle": ("townhall", "Updated resident lifecycle"),
    "hearth_residents_provision": ("townhall", "Set up a resident"),
    "hearth_work_assign": ("townhall", "Assigned work"),
    "hearth_work_start": ("townhall", "Requested work to start"),
    "hearth_letters_read": ("post", "Read letters"),
    "hearth_letters_send": ("post", "Sent a letter"),
    "hearth_letters_reply": ("post", "Answered a letter"),
    "hearth_read_channel_history": ("research", "Requested channel history"),
    "hearth_publish_announcement": ("post", "Queued an announcement"),
}


def run_actions(db, run_ids: list[str]) -> dict:
    if not run_ids:
        return {}
    placeholders = ",".join("?" for _ in run_ids)
    rows = db.execute(
        f"""SELECT a.resource_id, a.sequence, a.at, a.kind, a.detail FROM audit a
        JOIN (SELECT resource_id, MAX(sequence) AS sequence FROM audit
              WHERE resource_id IN ({placeholders})
              AND kind IN ('management.tool_completed', 'communications.tool_prepared')
              GROUP BY resource_id) latest ON a.sequence=latest.sequence""",
        run_ids,
    )
    result = {}
    for row in rows:
        try:
            payload = json.loads(row["detail"])
            if not isinstance(payload, dict):
                continue
            if row["kind"] == "management.tool_completed" and payload.get("success") is not True:
                continue
            tool = payload.get("tool")
            if not isinstance(tool, str) or tool not in ACTIONS:
                continue
            place, label = ACTIONS[tool]
            result[row["resource_id"]] = {
                "place": place,
                "label": label,
                "at": row["at"],
                "sequence": row["sequence"],
            }
        except ValueError, TypeError:
            continue
    return result
