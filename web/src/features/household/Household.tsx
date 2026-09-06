import { useState, type FormEvent } from "react";
import { Client, type HouseholdPolicy } from "../../shared/client";

const money = (value: number) => `$${(value / 1_000_000).toFixed(4)}`;
export function HouseholdPanel({
  client,
  policy,
  readOnly,
  onSaved,
}: {
  client: Client;
  policy: HouseholdPolicy;
  readOnly: boolean;
  onSaved: () => void;
}) {
  const [draft, setDraft] = useState<HouseholdPolicy | null>(null);
  const [error, setError] = useState("");
  const [saving, setSaving] = useState(false);
  async function save(event: FormEvent) {
    event.preventDefault();
    if (!draft) return;
    setSaving(true);
    setError("");
    try {
      await client.request("/api/household", {
        method: "PUT",
        body: JSON.stringify({
          expected_revision: draft.revision,
          daily_limit: draft.daily_limit,
          timezone: draft.timezone,
          resident_limit: draft.resident_limit,
          concurrency_limit: draft.concurrency_limit,
        }),
      });
      setDraft(null);
      onSaved();
    } catch (e) {
      setError(
        `${e instanceof Error ? e.message : "Save failed"}. Your draft is preserved. Close and reopen the editor to load the current policy.`,
      );
    } finally {
      setSaving(false);
    }
  }
  return (
    <section className="household-policy" aria-label="Household allowance">
      <div className="section-title">
        <div>
          <span className="eyebrow">ONE HOUSEHOLD · {policy.budget_day}</span>
          <h2>Shared allowance</h2>
        </div>
        <strong>{money(policy.remaining)} remaining</strong>
      </div>
      <p>
        All residents share {money(policy.daily_limit)} per day ·{" "}
        {policy.timezone}. API-equivalent estimates are not subscription
        charges.
      </p>
      <dl className="household-meters">
        <div>
          <dt>Measured usage</dt>
          <dd>{money(policy.spent)}</dd>
        </div>
        <div>
          <dt>Active reservations</dt>
          <dd>{money(policy.reserved)}</dd>
        </div>
        <div>
          <dt>Unknown usage holds</dt>
          <dd>{money(policy.unknown)}</dd>
        </div>
        <div>
          <dt>Residents</dt>
          <dd>
            {policy.resident_count} / {policy.resident_limit}
          </dd>
        </div>
        <div>
          <dt>Concurrent runs</dt>
          <dd>
            {policy.active_runs} / {policy.concurrency_limit}
          </dd>
        </div>
      </dl>
      {(policy.uncertain_reserved ?? 0) > 0 && (
        <p>
          Active reservations include {money(policy.uncertain_reserved ?? 0)}{" "}
          for runs with an unknown execution outcome. They keep both capacity
          and budget until execution is reconciled.
        </p>
      )}
      {policy.unknown > 0 && (
        <p>
          Unknown usage keeps its reservation, including across midnight. In{" "}
          <a href="#tasks">Tasks</a>, a terminal run can receive an explicit
          operator usage report with evidence. That report is manual accounting,
          not provider verification.
        </p>
      )}
      {policy.remaining === 0 && (
        <p role="status">
          The household allowance has no headroom for another run.
        </p>
      )}
      {policy.active_runs >= policy.concurrency_limit && (
        <p role="status">The household concurrent-run limit is reached.</p>
      )}
      {policy.resident_count >= policy.resident_limit && (
        <p role="status">The household resident limit is reached.</p>
      )}
      {!draft ? (
        <button
          className="quiet"
          disabled={readOnly}
          onClick={() => {
            setDraft({ ...policy });
            setError("");
          }}
        >
          Edit household policy
        </button>
      ) : (
        <form onSubmit={(e) => void save(e)}>
          <p>
            Only the operator can change these limits. Lowering a limit blocks
            future admissions; it does not stop existing runs.
          </p>
          <div className="household-fields">
            <label>
              Daily allowance ($)
              <input
                type="number"
                min="0"
                step="0.000001"
                required
                value={draft.daily_limit / 1_000_000}
                onChange={(e) =>
                  setDraft({
                    ...draft,
                    daily_limit: Math.round(Number(e.target.value) * 1_000_000),
                  })
                }
              />
            </label>
            <label>
              Budget timezone
              <input
                required
                value={draft.timezone}
                onChange={(e) =>
                  setDraft({ ...draft, timezone: e.target.value })
                }
              />
            </label>
            <label>
              Resident limit
              <input
                type="number"
                required
                min="1"
                max="1000"
                step="1"
                value={draft.resident_limit}
                onChange={(e) =>
                  setDraft({ ...draft, resident_limit: Number(e.target.value) })
                }
              />
            </label>
            <label>
              Concurrent-run limit
              <input
                type="number"
                required
                min="1"
                max="100"
                step="1"
                value={draft.concurrency_limit}
                onChange={(e) =>
                  setDraft({
                    ...draft,
                    concurrency_limit: Number(e.target.value),
                  })
                }
              />
            </label>
          </div>
          <p>
            Timezone changes retain spend from any original budget day still in
            progress. Active and unknown reservations always carry forward.
          </p>
          {error && <p role="alert">{error}</p>}
          <button disabled={saving || readOnly}>Save policy</button>{" "}
          <button
            type="button"
            className="quiet"
            disabled={saving}
            onClick={() => setDraft(null)}
          >
            Close editor
          </button>
        </form>
      )}
    </section>
  );
}
