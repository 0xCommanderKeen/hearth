# Changelog

One line per merged PR, newest first. Decisions live in `docs/adr/`.

- The Codex subscription is the only runtime: mock runtimes, their stores and the
  runtime/process-boundary selectors are gone, tests drive a fake under `tests/`,
  and schema v3 retires runs recorded against a runtime this release cannot honour.
- Older Hearth stores upgrade forward on start with the original kept beside them;
  a quiet store may change runtime kind; docs rule trimmed to ADR + changelog (ADR 0013).
