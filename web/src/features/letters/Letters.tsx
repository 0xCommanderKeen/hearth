import { useRef, useState, type FormEvent } from "react";
import {
  Client,
  type Letter,
  type LetterState,
  type Resident,
  type ResidentLetters,
} from "../../shared/client";
import { RunLink } from "../residents/RunLink";

const PAGE = 20;
const stamp = (at: number) => new Date(at * 1000).toLocaleString();
const usd = (microdollars: number) => (microdollars / 1e6).toFixed(2);

/** What a letter came to, said out loud. A question that goes quiet is worse than one
 *  that is refused, so every state has a name here and none of them is silence. */
const STATE_LABEL: Record<LetterState, string> = {
  pending: "Open",
  replied: "Answered",
  unanswered: "Worked, never answered",
  failed: "The run did not finish",
  expired: "Went stale before anyone started it",
};

/** The Ledger's colour-as-state, reused: answered reads as done, open as running,
 *  unanswered and expired as needing a look, failed as failed. */
export function LetterStateChip({ state }: { state: LetterState }) {
  return <span className={`state state-${state}`}>{STATE_LABEL[state]}</span>;
}

function displayName(residents: Resident[], id: string | null) {
  if (id === null || id === "operator") return "Operator";
  return residents.find((r) => r.id === id)?.name ?? id;
}

function LetterRow({
  letter,
  residents,
  openable,
  incoming,
}: {
  letter: Letter;
  residents: Resident[];
  openable: (runId: string) => boolean;
  incoming: boolean;
}) {
  const other = incoming
    ? displayName(residents, letter.sender_resident_id)
    : displayName(residents, letter.recipient_resident_id);
  return (
    <li>
      <div className="memory-revision">
        <strong>{letter.title}</strong>
        <LetterStateChip state={letter.state} />
        <time>{stamp(letter.created_at)}</time>
      </div>
      <small>
        {incoming ? "From" : "To"} {other} · hop {letter.depth} ·{" "}
        {letter.state === "pending"
          ? `goes stale ${stamp(letter.expires_at)}`
          : `settled ${letter.settled_at === null ? "—" : stamp(letter.settled_at)}`}
      </small>
      <p className="journal-text">
        {letter.instruction}
        {letter.instruction_truncated && "…"}
      </p>
      {letter.reply ? (
        <>
          <p className="journal-text">
            <strong>Answer · </strong>
            {letter.reply.text}
          </p>
          <RunLink
            runId={letter.reply.run_id}
            openable={openable(letter.reply.run_id)}
            label="Open the run that answered it →"
          />
        </>
      ) : (
        <small>
          {letter.state === "pending"
            ? "No answer yet."
            : "No answer was ever written."}
        </small>
      )}
    </li>
  );
}

/** Every letter one resident received and every letter it wrote, and the operator's own
 *  hand for writing one more.
 *
 *  Two states decide whether a letter here can ever be answered, and both are read from
 *  the resident rather than guessed: a shut door refuses the letter outright, and a
 *  daily limit below what one run costs refuses the answer at allocation, leaving the
 *  letter unanswered with nothing else to show for it. Both are named beside the list.
 */
export function Letters({
  client,
  resident,
  residents,
  busy,
  readOnly,
  act,
  openable,
}: {
  client: Client;
  resident: Resident;
  residents: Resident[];
  busy: boolean;
  readOnly: boolean;
  act: (operation: () => Promise<unknown>) => Promise<void>;
  openable: (runId: string) => boolean;
}) {
  const [loaded, setLoaded] = useState<ResidentLetters | null>(null);
  const [title, setTitle] = useState("");
  const [detail, setDetail] = useState("");
  const [refusal, setRefusal] = useState("");
  const [written, setWritten] = useState("");
  // The command identity survives an uncertain response, so a retry replays the letter
  // Hearth already accepted rather than writing a second one.
  const pending = useRef<{ id: string; title: string; detail: string } | null>(
    null,
  );
  const accepts = !!resident.letters_accept;
  const read = () =>
    act(async () => setLoaded(await client.letters(resident.id, PAGE, 0)));
  async function send(event: FormEvent) {
    event.preventDefault();
    pending.current ??= { id: crypto.randomUUID(), title, detail };
    const draft = pending.current;
    setRefusal("");
    setWritten("");
    try {
      const receipt = await client.sendLetter(resident.id, draft.id, {
        title: draft.title,
        detail: draft.detail,
      });
      pending.current = null;
      setTitle("");
      setDetail("");
      setWritten(`Letter queued as task ${receipt.task_id}.`);
      await read();
    } catch (error) {
      setRefusal(
        error instanceof Error ? error.message : "Something went wrong.",
      );
    }
  }
  return (
    <details className="skill-editor">
      <summary>{resident.name} · Letters</summary>
      <p>
        A letter is an ordinary task with an address. It is worked by the
        resident it reached, on that resident's own allowance, and answered on
        its own run; nothing wakes the sender.
      </p>
      <p
        className={accepts ? "notice" : "notice error"}
        aria-label={`Whether ${resident.name} can be written to`}
      >
        {accepts
          ? `${resident.name} accepts letters.`
          : `${resident.name} accepts no letters. Every letter written here is refused as “letters not accepted”; the door is the resident's own declared letters.accept.`}{" "}
        Daily spending limit ${usd(resident.daily_limit)} — an answer is a run
        under it, so a limit below one run's cost refuses the answer at
        allocation and leaves the letter unanswered.
      </p>
      <button disabled={busy} onClick={() => void read()}>
        {loaded ? "Reload letters" : "Read letters"}
      </button>
      {loaded && (
        <>
          <h4>Received</h4>
          {!loaded.inbox.length && <p>No letter has reached this resident.</p>}
          {!!loaded.inbox.length && (
            <ol className="journal" aria-label={`Letters to ${resident.name}`}>
              {loaded.inbox.map((letter) => (
                <LetterRow
                  key={letter.task_id}
                  letter={letter}
                  residents={residents}
                  openable={openable}
                  incoming
                />
              ))}
            </ol>
          )}
          <h4>Sent</h4>
          {!loaded.sent.length && <p>This resident has written no letters.</p>}
          {!!loaded.sent.length && (
            <ol
              className="journal"
              aria-label={`Letters from ${resident.name}`}
            >
              {loaded.sent.map((letter) => (
                <LetterRow
                  key={letter.task_id}
                  letter={letter}
                  residents={residents}
                  openable={openable}
                  incoming={false}
                />
              ))}
            </ol>
          )}
        </>
      )}
      <form onSubmit={send} aria-label={`Send a letter to ${resident.name}`}>
        <h4>Send a letter</h4>
        <p>
          You write with your own hand: no grant bounds the operator. This
          resident's door, its archive state and the household's own depth and
          daily cap hold exactly as they do for a letter one resident writes to
          another.
        </p>
        <label htmlFor="letter-title">What it is about</label>
        <input
          id="letter-title"
          value={title}
          maxLength={200}
          required
          disabled={busy || readOnly || !!pending.current}
          onChange={(e) => setTitle(e.target.value)}
        />
        <label htmlFor="letter-detail">The request</label>
        <textarea
          id="letter-detail"
          value={detail}
          maxLength={8000}
          required
          disabled={busy || readOnly || !!pending.current}
          onChange={(e) => setDetail(e.target.value)}
        />
        <button className="primary" disabled={busy || readOnly}>
          {pending.current ? "Retry the letter" : "Send the letter"}
        </button>
      </form>
      {refusal && (
        <p className="notice error" role="alert">
          Letter refused · {refusal}. Nothing was written.
        </p>
      )}
      {written && (
        <p className="notice" role="status">
          {written}
        </p>
      )}
    </details>
  );
}
