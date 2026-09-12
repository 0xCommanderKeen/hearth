# Shared communications browser checks

Chromium on Linux, synthetic HTTP fixtures, 2026-09-11, slice #247 of #239.
The actual rendered React application was exercised at desktop (1280 CSS pixels)
and mobile (390 CSS pixels). This complements the real temporary SQLite API and
owning worker tests; these screenshots do not claim a live connected transport.

- `desktop-unknown.png`: completed source run with unknown external delivery.
- `mobile-unknown.png`: same distinction and controls at 390px without horizontal overflow.
- `resident-deep-link.png`: existing resident Letters deep link, preserved seven-tab layout.
- `result.json`: actual browser requests, completed keyboard/navigation/conflict checks,
  and page-error results. Ordinary rendering made no explicit health probe.

Keyboard submission/cancellation, revision-conflict reload and direct source-run
navigation were exercised. No physical phone, screen reader/native accessibility,
live credential, personal content, runtime execution or external send was involved.

A final Chromium check (`inactive-controls-result.json`) verifies that pending or
disabled connections cannot activate or probe even after the acknowledgement is
checked. A stopped worker or missing explicit binding also disables Probe; inactive
or ungranted routes are excluded. The corresponding mobile screenshots retain
these disabled states. This check issued no mutation or probe request.

`forms-api-result.json` records a second, integrated form journey: Chromium's
rendered forms called the real authenticated FastAPI app through TestClient, with
real temporary SQLite and the worker stopped. Eighteen writes create, edit and
reload Discord/Telegram/ntfy connection references, both chat route configurations,
all four grant scopes, and channel/notification-target forwarding bindings. The
journey verifies immutable fields stay disabled, ntfy stores a null bot identity,
target slots survive reload, and 390px layout has no overflow or page errors.
`mobile-ntfy-forwarding.png` shows its final state. All identities and references
are synthetic; no transport was activated, credential resolved or message sent.
