# Resident-local budget windows

Declarations have an explicit IANA `budget_timezone`, validated at save time.
New declarations default to UTC unless an explicit budget zone is selected.
Each new run pins its declaration revision, local budget date and timezone for
provenance. The browser displays the resident's budget timezone.

Admission derives the current local calendar day's half-open interval from midnight
to the next local midnight, then compares UTC timestamps. It does not assume a day
is 86,400 seconds. Repeated boundary times use their earlier occurrence. Recorded
costs are selected by admission timestamp inside this interval; the stored date
string is provenance, not a mutable spending counter.

A timezone edit therefore retains costs whose timestamps fall within the newly
configured current day. The edit may change calendar membership for costs outside
that window; this is an explicit policy change, not a promise of a rolling 24-hour
cap. Outstanding exposure still spans day boundaries, and missing-usage/operator
holds remain enforced. Reconciliation preserves the original admission timestamp.

Synthetic tests cover local versus UTC midnight, spring/fall day lengths, repeated
hour spending, timezone edits and invalid timezone refusal.
External imports must preserve their source accounting timestamps and explicitly
validate any differences before execution is enabled.
