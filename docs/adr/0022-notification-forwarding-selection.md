# ADR 0022: Automatic operator notice forwarding

Status: accepted, 2026-09-11. Slice #243 of epic #239; refines ADR 0019.

The owned communications worker selects up to eight forwarding bindings and 100
notices per binding per pass, rotating independently of credentials and polling.
Each binding retains its audit-sequence cursor; empty automatic selections do not
write selection audit facts. A blocked binding cannot hide later healthy bindings.
No selection launches a model run or changes Inbox read status.

Every configuration revision starts automatic selection at the current watermark.
Notices from before activation, a disabled interval or an earlier filter require
explicit bounded backfill. The notice/binding identity survives all revisions.
The destination is immutable; replacing it requires a new binding. Operator URL
changes are revisioned, with historical origins retained to validate exact old
operation payloads. Configuration changes refuse queued operations in the same
writer. In-flight and unknown effects retain their evidence and never gain a retry.
Connection revocation is also swept without requiring a usable credential.

Schema 19 adds nullable `notification_forwarding.operator_url`, filled with null
on forward upgrade, and an initially empty `notification_forwarding_origins` table
recording each newly configured revision’s URL. It accepts only a bare HTTP(S) origin, without credentials,
path, query, fragment or escaped characters. New payloads contain kind and resource
reference, plus that origin's home URL when configured. They do not invent a run
link that the recent-window UI may no longer resolve. Existing exact legacy Inbox
JSON operations remain valid, with their original digest and attempts unchanged.
Current backups validate and retain the non-secret origin; held copies stay inert.

This is trusted operator/backend composition only. #247 supplies authenticated
configuration and inspection, #244 Discord setup and separately selected live
acceptance. No runtime authority, live account or deployment gate changes here.
