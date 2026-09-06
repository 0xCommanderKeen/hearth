# Mock core simplicity review

Updated 2026-09-06 after the fresh-start decision. Hearth keeps one operational
SQLite authority, one browser client and one release artifact. Commands and audit
commit together; runtime and effect receipts describe external evidence without
becoming competing authorities over work.

Migration requirements had added a second ownership registry, portable formats,
reconstruction and historical upgrade paths. Those requirements are now cancelled,
so their implementation is removed. A fresh database is defined directly. Runtime
run tokens and the supervisor lock remain because they enforce safe local work.
Current Hearth backups still matter for recovery; restore stays held until runtime
and effect authority can be reconciled.

The remaining modules own concrete behavior: Execution handles launch/cancel/finish;
Authority and Broker handle exact permission and uncertain effects; Routines handles
occurrence identity; Notifications handles delivery; Backup handles consistent held
copies; Residents handles revisioned configuration and memory. There is no reason
for another service, generic plugin system or broad compatibility layer.

Mock lifecycle, authority and browser evidence are useful but do not prove the real
Reader workflow. Before adding scope, select one bounded real task, verify actual
read-only host/runtime behavior, and measure whether recovery and daily use are
understandable. Real testing remains deferred by the current user direction.
