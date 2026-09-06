# API-equivalent token accounting

Miha selected subscription authentication with API-price token accounting: the
same usage calculator and $10/day Hearth policy should carry over to a future
API backend. The calculator does not take an authentication mode. Its result is
an `api_equivalent_estimate`, separately identified from actual provider charges.

The pinned schedule is `gpt-6-astra-api-equivalent-2026-09-06`. Per million tokens,
standard rates are $10 ordinary input, $1 cache reads, $12.50 cache writes and $50
output. Requests above 272,000 input tokens use twice the input/cache rates and
1.5 times the output rate for that full request. Fast doubles applicable rates.
Other models and service modes remain unknown.
[Astra pricing](https://developers.openai.com/api/docs/models/gpt-6-astra),
checked 2026-09-06.

Cache reads and writes are disjoint portions of total input. Ordinary input is
the remainder; a contradictory negative remainder refuses estimation.
[Prompt caching](https://developers.openai.com/api/docs/guides/prompt-caching).
Reasoning is included in output and is not charged again. Its optional detail
counter may be absent without making a known output total unknown.
[Reasoning usage](https://developers.openai.com/api/docs/guides/reasoning).

`estimate_api_equivalent` requires request-level `TokenUsage` values and explicit
model/service mode. It preserves absent required counts as unknown, checks integer
and subset constraints, and bounds request/count sizes. Empty usage does not prove
that no request occurred; an explicitly reported zero request is distinct. It uses
integer half-microdollars internally, rounds upward once after summing requests,
and returns the exact schedule identity. Price updates require a new schedule;
historical run accounting must retain its admitted schedule.

A whole-turn CLI aggregate is insufficient to select per-request long-context
rates. Do not feed it to this function as though it were one request. The caller
must prove coverage, pricing mode and association with the admitted run before
settling its budget. Missing requests, cancelled or uncertain dispatch and absent
terminal usage still require the existing hold. Hosted-tool charges are outside
this text-only calculation; it cannot price runs that enable paid hosted tools.

The offline subscription probe now supplies nonzero synthetic cache/read/write and
reasoning values in each local response, compares their sum with actual CLI
reported counters, and estimates the complete observed fixture request sequence.
The fixed success case is 623 microdollars after rounding; the two-request tool
injection case is 1,245, rounded after aggregation. The report marks these amounts
synthetic. This verifies counter mapping and arithmetic, not real subscription
usage or operational budget settlement. Application integration remains next.
