import { useRef, useState } from "react";
import { Client, type UsageOrigin } from "../../shared/client";

const PAGE = 20;
const usd = (microdollars: number) => (microdollars / 1e6).toFixed(4);

/** What one question cost, gathered under the task its whole chain rolls up to.
 *
 * A letter is worked by the resident it reached, on that resident's own allowance, so
 * the money one question spends is spread over as many runs as the chain has hops.
 * Each run is counted once, at the amount its own row records; a run whose usage is
 * still unknown is named as unknown here and keeps the hold it placed on its resident.
 */
export function UsageByOrigin({
  client,
  busy,
  act,
}: {
  client: Client;
  busy: boolean;
  act: (operation: () => Promise<unknown>) => Promise<void>;
}) {
  const [origins, setOrigins] = useState<UsageOrigin[] | null>(null);
  const read = () =>
    act(async () => setOrigins((await client.usageByOrigin(PAGE)).origins));
  return (
    <details className="skill-editor">
      <summary>Cost by origin</summary>
      <p>
        What each question cost across every resident that worked on it. A
        letter is worked by the resident it reached, so one question can spend
        several allowances. Amounts are API-equivalent estimates.
      </p>
      <button disabled={busy} onClick={() => void read()}>
        {origins ? "Reload cost by origin" : "Read cost by origin"}
      </button>
      {origins?.length === 0 && <p>No run has been admitted yet.</p>}
      {!!origins?.length && (
        <ol aria-label="Cost by origin">
          {origins.map((origin) => (
            <li key={origin.root_task_id}>
              <div className="memory-revision">
                <strong>{origin.instruction || origin.root_task_id}</strong>
                <time>{new Date(origin.last_at * 1000).toLocaleString()}</time>
              </div>
              <small>
                {usd(origin.known_cost)} API-equivalent USD ·{" "}
                {origin.runs === 1 ? "1 run" : `${origin.runs} runs`} ·{" "}
                {origin.letters === 0
                  ? "no letters"
                  : origin.letters === 1
                    ? "1 letter"
                    : `${origin.letters} letters`}{" "}
                · {origin.residents_involved.join(", ")}
              </small>
              {origin.unknown_runs > 0 && (
                <small>
                  {origin.unknown_runs} finished{" "}
                  {origin.unknown_runs === 1 ? "run has" : "runs have"} unknown
                  usage and still hold their resident. Reconcile each run to
                  release it; this amount excludes them.
                </small>
              )}
              {origin.active_runs > 0 && (
                <small>
                  {origin.active_runs} still running · {usd(origin.reserved)}{" "}
                  USD reserved.
                </small>
              )}
            </li>
          ))}
        </ol>
      )}
    </details>
  );
}

export function UsageReport({
  client,
  runId,
  busy,
  act,
}: {
  client: Client;
  runId: string;
  busy: boolean;
  act: (operation: () => Promise<unknown>) => Promise<void>;
}) {
  const [amount, setAmount] = useState("");
  const [evidence, setEvidence] = useState("");
  const pending = useRef<{
    id: string;
    amount: number;
    evidence: string;
  } | null>(null);
  return (
    <details>
      <summary>Reconcile missing usage</summary>
      <p>
        Record an operator-reported amount and its evidence. This resolves only
        this run’s accounting hold; other pauses remain.
      </p>
      <form
        onSubmit={(event) => {
          event.preventDefault();
          pending.current ??= {
            id: crypto.randomUUID(),
            amount: Number(amount),
            evidence,
          };
          const report = pending.current;
          void act(() =>
            client.reconcileUsage(
              runId,
              report.id,
              report.amount,
              report.evidence,
            ),
          );
        }}
      >
        <label htmlFor={`usage-${runId}`}>
          Cost in micro-USD (1 USD = 1,000,000)
        </label>
        <input
          id={`usage-${runId}`}
          type="number"
          min="0"
          max="1000000000000"
          step="1"
          required
          disabled={busy || !!pending.current}
          value={amount}
          onChange={(e) => setAmount(e.target.value)}
        />
        <label htmlFor={`evidence-${runId}`}>Evidence for this amount</label>
        <textarea
          id={`evidence-${runId}`}
          required
          maxLength={2000}
          disabled={busy || !!pending.current}
          value={evidence}
          onChange={(e) => setEvidence(e.target.value)}
        />
        <button disabled={busy}>
          {pending.current ? "Retry usage report" : "Record usage"}
        </button>
      </form>
    </details>
  );
}
