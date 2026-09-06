# Bind token pricing to admission and settle with evidence

Use the same API-equivalent token calculator for subscription authentication and a
future API backend, as selected by Miha. Keep provider charges distinct from the
internal dollar estimate. Pin model, mode and schedule before execution; never infer
request-level prices from an aggregate CLI counter.

Keep a small optional pricing row per run and one immutable settled receipt in
SQLite. Commit receipt, cost, output reference and audit together. Preserve the
existing fixed mock path while testing this seam with sealed synthetic journals.
Do not add an authentication abstraction, API dispatch or a second budget ledger.

A settled receipt must survive independently of worker scratch and be verifiable
in a held backup. SQLite owns the committed copy; the file journal owns durable
pre-settlement capture. Production collector ownership and its final handoff still
need operational runtime integration. Fresh tables are defined directly with no
historical conversion or data migration.
