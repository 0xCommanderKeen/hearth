# Resident skill text

Skill text is operator-authored Markdown instruction content. It belongs to a
resident declaration revision alongside purpose, daily allowance and budget zone.
It does not grant permissions or execute code. Only synthetic text is used in this phase.

Townhall's **Skill text** drawer loads a declaration explicitly. Editing preserves
its other fields and uses its expected revision. Incoming snapshots never replace
a draft. If another edit changes the revision, saving is disabled until the
operator chooses **Load current revision (replace draft)**. A failed or lost save
response leaves the draft and original revision intact. Read back the current
revision before resolving an ambiguous response; do not overwrite a concurrent edit.
Restored copies permit reading but refuse saves.

The authenticated operator interface is `GET /api/residents/{id}` (optionally
`?revision=N`) and `PUT /api/residents/{id}`. PUT requires name, purpose, daily_limit,
budget_timezone, skill_text and expected_revision. Runtime credentials cannot call
these routes. Text is absent from ambient snapshots and audit; declaration reads
are authenticated and marked no-store. No browser storage persists drafts.

CLI commands call the same core validation and revision checks:

```sh
python -m hearth show-resident --data /tmp/synthetic-hearth --resident reader
python -m hearth save-resident --data /tmp/synthetic-hearth --resident reader \
  --source /tmp/declaration.json --expected-revision 1
python -m hearth show-resident --data /tmp/synthetic-hearth --resident reader --revision 1
```

The source file is the declaration object, with all five fields explicitly present:

```json
{
  "name": "Reader",
  "purpose": "Summarize synthetic notes.",
  "daily_limit": 1000000,
  "budget_timezone": "UTC",
  "skill_text": "# Summarizing\n\nSeparate completed work from next steps.\n"
}
```

Text may be empty and is preserved exactly, including whitespace and Unicode. The
limit is 32,000 characters of valid UTF-8; HTTP additionally retains its 64 KiB
request-body limit. Oversized input is refused without changing the revision.
Saves and their audit facts commit together; only one concurrent expected-revision
save wins. Earlier declaration revisions remain readable.

Context version 6 includes the skill text from the admitted declaration revision.
The executor and scoped runtime route use the same reader. An edit before launch
authorization refuses a new launch; an already-authorized input stays pinned, and
existing runtime evidence remains recoverable. Configuration changes revoke old
scoped context access. MockRuntime records only the input digest and still emits a
fixed simulated summary; this does not demonstrate a model following the skills.

Current-schema backups and held restores preserve all declaration revisions and
skill text. Persistent memory is covered separately in `resident-memory.md`.
