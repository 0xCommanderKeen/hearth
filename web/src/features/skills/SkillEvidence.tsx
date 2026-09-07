import { useEffect, useRef, useState } from "react";
import {
  Client,
  RequestError,
  type CatalogSkill,
  type SkillValidation,
} from "../../shared/client";

const readable = (reason: string) => reason.replaceAll("_", " ");

export function SkillEvidence({
  client,
  skill,
  readOnly,
  dirty,
  onPublished,
  onPendingChange,
}: {
  client: Client;
  skill: CatalogSkill;
  readOnly: boolean;
  dirty: boolean;
  onPublished: () => void;
  onPendingChange?: (pending: boolean) => void;
}) {
  const evidence = skill.authoring!;
  const [validation, setValidation] = useState<SkillValidation | null>(
    evidence.validation,
  );
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [retry, setRetry] = useState(false);
  const [reserve, setReserve] = useState(100000);
  const pending = useRef<Parameters<Client["publishSkill"]>[0] | null>(null);
  const live = useRef(true);
  useEffect(() => {
    onPendingChange?.(busy || retry);
  }, [busy, retry, onPendingChange]);
  useEffect(() => {
    live.current = true;
    return () => {
      live.current = false;
    };
  }, []);
  useEffect(() => {
    if (!validation || validation.status !== "pending" || readOnly) return;
    let current = true;
    const timer = window.setInterval(() => {
      client
        .skillValidation(validation.validation_id)
        .then((value) => {
          if (current) {
            setValidation(value);
            setError("");
          }
        })
        .catch((e) => {
          if (current) setError(`Evidence refresh failed: ${e.message}`);
        });
    }, 1000);
    return () => {
      current = false;
      window.clearInterval(timer);
    };
  }, [client, validation?.validation_id, validation?.status, readOnly]);

  async function validate() {
    if (busy || dirty || readOnly) return;
    setBusy(true);
    setError("");
    try {
      const result = await client.validateSkill(
        skill.skill_id,
        skill.revision,
        reserve,
      );
      if (live.current) setValidation(result);
    } catch (e) {
      if (live.current)
        setError(
          `Validation request is unconfirmed or refused: ${e instanceof Error ? e.message : "request failed"}. Retry this revision to recover its existing cases.`,
        );
    } finally {
      if (live.current) setBusy(false);
    }
  }

  async function publish() {
    if (!validation || busy || dirty || readOnly) return;
    pending.current ??= {
      command_id: crypto.randomUUID(),
      skill_id: skill.skill_id,
      expected_revision: skill.revision,
      validation_id: validation.validation_id,
    };
    setBusy(true);
    setError("");
    try {
      const result = await client.publishSkill(pending.current);
      if (
        result.command_id !== pending.current.command_id ||
        result.skill_id !== skill.skill_id ||
        result.revision !== skill.revision + 1
      )
        throw new Error("Incomplete publication receipt");
      if (!live.current) return;
      pending.current = null;
      setRetry(false);
      onPublished();
    } catch (e) {
      if (!live.current) return;
      if (
        e instanceof RequestError &&
        [401, 404, 409, 422].includes(e.status)
      ) {
        pending.current = null;
        setRetry(false);
        setError(
          `Publication refused: ${e.message}. Reload the skill before continuing.`,
        );
      } else {
        setRetry(true);
        setError(
          "Publication is unconfirmed. Retry the exact pending publication to recover its receipt.",
        );
      }
    } finally {
      if (live.current) setBusy(false);
    }
  }

  return (
    <section
      className="skill-evidence"
      aria-label={`Validation evidence for revision ${skill.revision}`}
    >
      <div className="skill-evidence-heading">
        <div>
          <span className="eyebrow">EVIDENCE, REVISION BY REVISION</span>
          <h3>Prove this revision.</h3>
        </div>
        <span
          className={`skill-evidence-state ${validation?.status ?? "draft"}`}
        >
          {validation?.status ?? "Not evaluated"}
        </span>
      </div>
      <div className="skill-evidence-structure">
        <span className="skill-step">01</span>
        <div>
          <h4>
            Structure {evidence.structure.passed ? "passed" : "needs attention"}
          </h4>
          <p>
            Narrow scope, required inputs, procedure, output, uncertainty and
            observable success.
          </p>
          {evidence.structure.reasons.map((reason) => (
            <p role="note" key={reason}>
              {reason}
            </p>
          ))}
        </div>
      </div>
      <div className="skill-evidence-structure">
        <span className="skill-step">02</span>
        <div>
          <h4>Two bounded examples</h4>
          <p>
            {validation?.assessment.includes("simulated")
              ? "Simulated runs · deterministic output checks. No model assessment was performed."
              : validation
                ? "Model runs · deterministic output checks over saved artifacts. No model grading or general quality guarantee."
                : "A visible read-only evaluator runs normal and edge examples serially. Both runs use the household allowance."}
          </p>
          {validation?.evaluator_id && (
            <a href={`#residents/${validation.evaluator_id}`}>
              Open evaluator →
            </a>
          )}
        </div>
      </div>
      {validation?.reason && (
        <p className="skill-evidence-reason">{readable(validation.reason)}</p>
      )}
      {!!validation?.cases.length && (
        <div className="skill-case-results">
          {validation.cases.map((entry) => (
            <article key={entry.position}>
              <span className="eyebrow">
                {entry.kind === "normal"
                  ? "NORMAL CASE"
                  : "EDGE / ADVERSARIAL CASE"}
              </span>
              <h4>
                {entry.result
                  ? entry.result.passed
                    ? "Passed checks"
                    : "Failed checks"
                  : !entry.run_id
                    ? validation.status === "failed"
                      ? "Not run · validation failed"
                      : "Waiting to run"
                    : validation.reason === "skill_evaluation_usage_unknown"
                      ? "Awaiting usage evidence"
                      : "Awaiting result"}
              </h4>
              {entry.result?.reasons.map((reason) => (
                <p key={reason}>{reason}</p>
              ))}
              {entry.run_id && (
                <a href={`#run-${entry.run_id}`}>Inspect run →</a>
              )}
              {entry.result && (
                <p className="muted">
                  Accounted ${(entry.result.actual_cost / 1000000).toFixed(6)}
                  {entry.result.simulated
                    ? " · simulation"
                    : " · API-equivalent estimate"}
                </p>
              )}
              {!!entry.result?.checks.length && (
                <ul>
                  {entry.result.checks.map((check, index) => (
                    <li key={index}>
                      {check.passed ? "✓" : "×"} {readable(check.assertion)}:{" "}
                      {String(check.expected)}
                    </li>
                  ))}
                </ul>
              )}
              <details>
                <summary>Exact case provenance</summary>
                <p>Task {entry.task_id}</p>
                <p>
                  Input revision {entry.input_revision} · {entry.input_sha256}
                </p>
                {entry.result?.artifact_id && (
                  <p>
                    Saved artifact {entry.result.artifact_id}
                    <br />
                    {entry.result.artifact_sha256}
                  </p>
                )}
              </details>
            </article>
          ))}
        </div>
      )}
      {evidence.publication && (
        <p className="skill-publication-proof">
          Published revision {skill.revision} preserves the exact content tested
          as candidate revision {evidence.publication.candidate_revision}.
          <small>{evidence.publication.sha256}</small>
        </p>
      )}
      {dirty && (
        <p className="muted">
          Save your edits before validating or publishing. These results belong
          to saved revision {skill.revision}.
        </p>
      )}
      {error && <p role="alert">{error}</p>}
      {!readOnly && skill.status === "draft" && !validation && (
        <div className="skill-validation-actions">
          <label>
            Reservation per example (USD)
            <input
              type="number"
              min="0.000001"
              max="0.5"
              step="0.000001"
              value={reserve / 1000000}
              onChange={(e) =>
                setReserve(Math.round(Number(e.target.value) * 1000000))
              }
              disabled={busy || dirty}
            />
          </label>
          <button
            type="button"
            disabled={
              busy ||
              dirty ||
              !evidence.structure.passed ||
              reserve < 1 ||
              reserve > 500000
            }
            onClick={() => void validate()}
          >
            Run two example checks
          </button>
        </div>
      )}
      {!readOnly &&
        skill.status === "draft" &&
        validation?.status === "passed" && (
          <button
            type="button"
            className="primary"
            disabled={busy || dirty}
            onClick={() => void publish()}
          >
            {retry ? "Retry pending publication" : "Publish validated revision"}
          </button>
        )}
      {validation?.status === "failed" && (
        <p className="muted">
          Revise the skill or its examples and save a new draft. The failed
          evidence stays with this revision.
        </p>
      )}
      {readOnly && validation?.status === "pending" && (
        <p className="muted">
          This view is read-only. Held restores do not start example runs.
        </p>
      )}
    </section>
  );
}
