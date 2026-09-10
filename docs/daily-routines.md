# Daily routines

Townhall can enable one daily synthetic summary for Reader. The API also accepts
revisioned routines at `/api/routines/{id}` with resident, instruction, local
`HH:MM`, IANA timezone, enabled flag, and expected revision. Routine identity and
resident binding stay fixed; configuration changes create immutable revisions.

Saving computes the next strictly future occurrence. Disabling stops future
occurrence creation; it does not cancel tasks already created. Re-enabling starts
with a future occurrence and does not replay disabled days. Existing queued work
continues through ordinary admission, including allowance and capacity checks.

The executor's supervision loop ticks routines before admitting their queued tasks
and inspecting runtime evidence. Routine occurrence creation uses the same task
creation operation as manual submissions. Task, occurrence, schedule advancement,
and audit facts commit together. A database write transaction serializes ticks;
`(routine_id, scheduled_at)` is also a durable uniqueness constraint.

Daily times use the earlier fold once when clocks repeat. A nonexistent local time
is skipped for that day. On restart after an outage, only the newest due occurrence
is considered; older missed dates are not enumerated. The next due instant advances
from the current clock. An unfinished task from this routine causes a persisted
`skipped_overlap` occurrence. Queued and unknown tasks count as unfinished. Other
resident work is handled by ordinary admission ownership, rather than a separate
scheduler execution mechanism.

The bounded scheduler handles at most 100 due routines per tick and at most 100
queued scheduled tasks per admission pass. It does not run live schedules,
call models, or send notifications. Timezone rules come from the host's ZoneInfo
database; deployment must provide the configured zones. Unsupported calendar gaps
are refused instead of guessed.

Verification covers concurrent ticks, restart deduplication, skipped overlap,
bounded outage catch-up, DST gap/fold, disable/re-enable, revision conflicts,
budget refusal, atomic rollback, and the authenticated API through the real
background executor. Component tests verify schedule selection and revisioned
disabling. Native visual inspection remains pending.
