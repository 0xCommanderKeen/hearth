# ADR 0020: Body-free Discord reverse scan progress

Status: accepted, 2026-09-11. Slice #139 of epic #239; refines ADR 0019.

Discord documents channel history as newest-first. It does not promise that an
`after` query selects the oldest N pending messages. Sorting one full returned page
therefore cannot establish chronological admission of a larger pending interval.

Keep the worker's fixed upper window and processed watermark. Schema 17 adds a
nullable `scan_before` to its cursor. Each scheduled poll examines one backwards
page with an overlapping lowest ID. Full pages move only that body-free frontier;
no transcript, ignored body, admission or processed decision is retained. A short
page or a page reaching the processed watermark establishes an oldest interval.
The adapter returns that interval ascending with its inclusive examined prefix,
and the worker commits its decisions/watermark and clears the scan frontier in one
transaction. Remaining work is scanned again against the same fixed upper bound.
This may rescan pages for a large backlog, but work per pass and retained data stay
bounded. Restart resumes the frontier; deletion cannot skip unexamined IDs.

Schema 17 also permits guild and channel scope keys in the existing schedule.
Successful responses can exhaust a rate bucket, so adapters export observed limits
and the worker persists them even without a 429, before completing a send receipt.
Guild/channel limits defer their selected scope, including queued sends, while a
Discord global limit defers the connection. New configured destinations consult the
same scope deadlines. Existing stores fill the frontier with null; backup validation
preserves the window/frontier ordering and schedule keys. Held copies remain inert.

No new authority, body-retention category, external source, or runtime is added.
The single-message bound and evidence-based unknown-send recovery remain ADR 0019.
