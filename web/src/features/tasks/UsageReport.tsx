import { useRef, useState } from "react";
import { Client } from "../../shared/client";

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
