# Offline Codex exec event interpretation

`CodexEvents` is a bounded incremental parser tested with synthetic JSONL. It starts
no process, reads no credentials or files, computes no dollar amounts, and does not
produce Hearth runtime evidence. It is preparation for the selected Codex Astra
adapter, not proof of real CLI compatibility or model behavior.

## Source boundary

Official exec documentation shows thread/turn lifecycle, completed `agent_message`
text and four token counters. It documents final-message file output, but does not
fully specify ordering, updates/errors, phase fields or billing relationships.
[Non-interactive mode](https://learn.chatgpt.com/docs/non-interactive-mode) and
[CLI reference](https://learn.chatgpt.com/docs/developer-commands?surface=cli),
checked 2026-09-06.

App-server's camelCase item types, slash-separated notifications and message phase
fields belong to a different protocol. They are not imported into the exec parser.
[App-server reference](https://learn.chatgpt.com/docs/app-server).

## Hearth acceptance policy

The following are deliberate local rules, not claims that every legitimate CLI
stream follows them. The profile is `codex-exec-jsonl-2026-09-06`.

- One thread and one turn; repeated or contradictory lifecycle events refuse.
  Unknown control events, generic errors and events after a terminal turn remain
  invalid. Retry/resume and multiple turns require a separately verified profile.
- Item envelopes require bounded IDs and stable types. Started/updated payloads
  stay opaque. Only completed agent-message text becomes a candidate; duplicate
  completions or a phase field from an unverified schema refuse. The parser does
  not infer tool termination from item events.
- One candidate can be returned after a completed turn and observed exit code zero.
  Several candidates stay ambiguous unless a caller supplies independently verified
  final-message-file content matching a completed candidate exactly. It never
  chooses the last message by guess or concatenates update payloads. The future
  worker must own and bound that file and verify its association with the run.
- A failed turn can be reported after observed process exit, but its partial text
  cannot become successful output. Unknown termination, abnormal completion exit,
  missing messages and unfinished turns have no publishable output.
- Usage retains input, cached-input, output and reasoning-output counts separately.
  Missing fields stay `None`; explicit zero remains zero. Provided values must be
  nonnegative integers within the local limit. No subset relationships, summation,
  pricing, completeness of billing or provider spending ceiling are inferred.
  Invalid or unrecognized usage fields refuse interpretation.
- UTF-8 may span input chunks. Duplicate JSON keys, non-finite numbers, invalid
  encoding, malformed JSON and excessive nesting refuse. A complete final JSON
  record without a newline is accepted; a partial record is not repaired.

Limits: 1 MiB per record, 4 MiB per stream, 10,000 records including blank lines,
512 KiB of candidate text, 128 messages and one billion per reported token field.
Framing scans the buffer without repeatedly copying its unconsumed tail. Rejection
is sticky and discards pending input. `finish` seals the parser once; a caller
cannot later reinterpret an unknown exit as a confirmed completion on the same object.

```python
from hearth.codex_events import CodexEvents

parser = CodexEvents()
# Feed bounded byte chunks from a future trusted worker's stdout reader.
parser.feed(synthetic_jsonl_bytes)
transcript = parser.finish(exit_code=0)  # Must be an independently observed exit.
```

Fixture tests cover byte-wise Unicode, completed/failed/incomplete streams,
contradictory events, opaque updates, final-message ambiguity and matching,
partial/invalid usage, adversarial framing and all resource limits. The installed
application remains limited to inline/process mocks. Real wiring still needs a
pinned CLI version, actual event/final-file verification, Mac isolation, verified
usage/pricing and explicit selection of a real test.
