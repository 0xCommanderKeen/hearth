import { useEffect, useRef, useState } from "react";
import { Client, type Approval, type Snapshot } from "../../shared/client";

export function Approvals({
  client,
  snapshot,
  busy,
  act,
  linkedId,
}: {
  linkedId?: string | null;
  client: Client;
  snapshot: Snapshot;
  busy: boolean;
  act: (operation: () => Promise<unknown>) => Promise<void>;
}) {
  const [review, setReview] = useState<{
    approval: Approval;
    content: string;
  } | null>(null);
  const mounted = useRef(true);
  const pending = useRef<{
    id: string;
    artifact: string;
    expires: number;
  } | null>(null);
  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);
  useEffect(() => {
    if (!linkedId) return;
    let active = true;
    void act(async () => {
      const next = await client.review(linkedId);
      if (mounted.current && active) setReview(next);
    });
    return () => {
      active = false;
    };
  }, [linkedId, client]);
  const [residentId, setResidentId] = useState("");
  const eligible = snapshot.residents.filter(
    (r) => r.lifecycle?.state !== "archived",
  );
  const selected = residentId || eligible[0]?.id || "";
  const policy = snapshot.publication_policies?.find(
    (p) => p.resident_id === selected,
  );
  const artifact = snapshot.runs.find(
    (r) =>
      r.status === "succeeded" && r.artifact_id && r.resident_id === selected,
  )?.artifact_id;
  const proposals = snapshot.approvals ?? [];
  const reviewed =
    review &&
    (review.approval.status !== "pending"
      ? review.approval
      : (proposals.find((p) => p.id === review.approval.id) ??
        review.approval));
  async function propose() {
    if (!artifact) return;
    pending.current ??= {
      id: crypto.randomUUID(),
      artifact,
      expires: Math.floor(Date.now() / 1000) + 3600,
    };
    const p = pending.current;
    await client.propose(p.id, p.artifact, p.expires);
    pending.current = null;
  }
  return (
    <section className="output approvals" aria-label="Approvals">
      <span className="eyebrow">TOWNHALL / NOTICEBOARD</span>
      <h2>Review before publication.</h2>
      <p>
        Publishing a result to the noticeboard needs your explicit approval.
        Residents stay read-only until you grant it, and nothing is sent outside
        Hearth.
      </p>
      {eligible.length > 1 && (
        <>
          <label htmlFor="approvals-resident">Resident</label>
          <select
            id="approvals-resident"
            value={selected}
            onChange={(e) => setResidentId(e.target.value)}
          >
            {eligible.map((r) => (
              <option key={r.id} value={r.id}>
                {r.name}
              </option>
            ))}
          </select>
        </>
      )}
      <div className="task-actions">
        <button
          disabled={busy || !selected}
          onClick={() =>
            void act(() =>
              client.publicationPolicy(
                selected,
                !policy?.enabled,
                policy?.revision ?? 0,
              ),
            )
          }
        >
          {policy?.enabled
            ? "Revoke publication"
            : "Allow publication requests"}
        </button>
        <button
          disabled={busy || !artifact || !policy?.enabled}
          onClick={() => void act(propose)}
        >
          {pending.current
            ? "Retry approval request"
            : "Request review of latest summary"}
        </button>
      </div>
      {!proposals.length && (
        <p className="muted">
          No publication requests yet. Complete a run to begin.
        </p>
      )}
      <ul className="tasks">
        {proposals.map((p) => {
          const action = snapshot.actions?.find((a) => a.id === p.id);
          return (
            <li key={p.id} id={`approval-${p.id}`}>
              <h3>Summary for the noticeboard</h3>
              <p>
                {action
                  ? `Action ${action.status}${action.reason ? ` · ${action.reason.replaceAll("_", " ")}` : ""}`
                  : `Review ${p.status}`}{" "}
                · expires {new Date(p.expires_at * 1000).toLocaleString()}
              </p>
              <div className="task-actions">
                <button
                  disabled={busy}
                  onClick={() =>
                    void act(async () => {
                      const next = await client.review(p.id);
                      if (mounted.current) setReview(next);
                    })
                  }
                >
                  Review exact summary
                </button>
                {p.status === "approved" &&
                  (!action ||
                    ["executing", "unknown"].includes(action.status)) && (
                    <button
                      disabled={busy}
                      onClick={() => void act(() => client.execute(p.id))}
                    >
                      {action ? "Reconcile action" : "Publish approved summary"}
                    </button>
                  )}
              </div>
              {action?.status === "unknown" && (
                <p role="status">
                  Outcome unknown. The destination remains reserved.
                  Reconciliation checks evidence without sending again.
                </p>
              )}
            </li>
          );
        })}
      </ul>
      {review && (
        <div aria-label="Exact approval review">
          <h3>Exactly what you are approving</h3>
          <p>
            Action: {review.approval.payload.action} · destination:{" "}
            {review.approval.payload.destination}
          </p>
          <p>
            Resident revision {review.approval.payload.resident_revision} ·
            grant revision {review.approval.payload.policy_revision} ·
            destination revision {review.approval.payload.destination_revision}
          </p>
          <p>
            Expires{" "}
            {new Date(review.approval.expires_at * 1000).toLocaleString()}
          </p>
          <pre>{review.content}</pre>
          <details>
            <summary>Artifact and approval fingerprints</summary>
            <pre>
              {review.approval.payload.sha256}
              {"\n"}
              {review.approval.digest}
            </pre>
          </details>
          <div className="task-actions">
            <button
              disabled={busy || reviewed?.status !== "pending"}
              onClick={() =>
                void act(() =>
                  client.decide(review.approval, true).then((approval) => {
                    if (mounted.current) setReview({ ...review, approval });
                  }),
                )
              }
            >
              Approve this exact action
            </button>
            <button
              disabled={busy || reviewed?.status !== "pending"}
              onClick={() =>
                void act(() =>
                  client.decide(review.approval, false).then((approval) => {
                    if (mounted.current) setReview({ ...review, approval });
                  }),
                )
              }
            >
              Deny this action
            </button>
            <button onClick={() => setReview(null)}>Close review</button>
          </div>
        </div>
      )}
    </section>
  );
}
