# The inbox

Every notification Hearth raises lands in the inbox and stays there. A run that
succeeds, fails or is cancelled writes one row of `notifications` in the same
transaction as its terminal state, its artifact reference and its audit fact, so the
inbox can never claim work that did not happen, and no run the executor settles finishes
without the operator being told. If the write fails, the run does not finish either.

One event is one notification: `(kind, resource_id)` is unique, so a caller that
retries its own transaction cannot fill the inbox with copies of the same news. The
payload carries an allowlisted kind, the resource identity and a local browser link,
and nothing else — no instruction, output, credential or ownership token. Opening the
link still requires the normal operator session.

A notification is read or unread, and nothing else. `POST /api/notifications/{id}/read`
with `{"read": true}` marks it; the same call with `false` puts it back. Marking is
idempotent and audits once per change (`notification.read` / `notification.unread`),
and a restored copy refuses it like every other mutation: the copy may show its inbox,
never change it. Nothing is deleted, expired or archived — the inbox is the durable
record of what Hearth has told the household, and Townhall's Inbox page shows it unread
first, then newest, with the unread count on the nav.

## Forwarding

Sending a notification somewhere else — ntfy, a chat channel — is a separate job, and
one Hearth does not do yet. `observation/notifications.Forwarder` is the seam it will
use: `deliver(notification)` over what the inbox already holds. Because the record is
written first and kept, a forwarder that is absent, late, retrying or permanently
broken cannot lose a notification; at worst the operator reads it in Townhall instead.
Delivery state — attempts, backoff, receipts — belongs to the transport that has it,
and arrives with the first real one (#127, #136).

## What this replaced

Until 2026-09-08 the inbox was a queue: `deliveries` rows drained by a supervisor pass
into `MockInbox`, a folder of local markdown files, with attempts, exponential backoff,
obsolescence and checksummed receipt recovery for a lost acknowledgement. All of that
existed because the "transport" was a stand-in that could pretend to fail. Writing the
record to the store Hearth already commits to needs no acknowledgement and can lose
none, so the machinery went with the mock and the record stayed.
