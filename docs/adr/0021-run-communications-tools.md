# ADR 0021: Durable run communications requests

Status: accepted, 2026-09-11. Slice #242 of epic #239; refines ADR 0019.

History tools prepare a bounded durable request through the existing exact-run
bridge and return queued while the single communications worker performs the read.
Repeating the same operation ID retrieves its immutable completed result; a changed
payload conflicts, even with a different transport call ID. Every replay rechecks
current run and origin scope. The worker commits a reading permit before HTTP and
rechecks authority before storing text. An interrupted read becomes an explicit
refusal; it never silently fetches newer content under an old operation identity.

Schema 18 adds initially empty request and call-identity tables. Each run has at
most 32 distinct communications call IDs and four publications of at most 2,000
characters each. History keeps at most 50 messages/32 KiB per request, including its escaped
native envelope. An oversized first message is explicitly omitted with a cursor
that lets the next request continue. Request and
result text belongs to run evidence retention, not the conversation transcript's
30-day pruning promise. Backup preserves request identities, pins and outcomes;
held copies cannot execute or replay tools. Existing stores upgrade forward without
new authority or activation.

Each worker pass rotates two read requests, separately from polling, admission and
delivery budgets. Read network I/O uses its existing protected client, connection
ownership, deadlines and persisted rate limits outside SQLite writers. The native
bridge never receives the connector secret or resolver. Known worker credentials
and run-owner values are redacted from returned history. Announcements redact the
run-owner value before immutable enqueue; a payload containing a worker-known
credential is refused before HTTP, without rewriting its committed digest. This
boundary does not identify arbitrary unknown user-supplied secrets.

Publication receipts report the existing Delivery operation's state and exact
attempt evidence. Successful run completion may hand off already-authorized intent;
failed, cancelled, paused, archived or revoked work cannot obtain a new send permit.
A lost send acknowledgement remains unknown. Task success and delivery success
remain independent. Tool installation and editable Herald etiquette grant nothing.
No live source, credential, setup or real-host acceptance is included.
