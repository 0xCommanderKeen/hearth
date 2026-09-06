import { useEffect, useRef, useState } from "react";
import { Client, type Approval, type Snapshot } from "./client";

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
  const policy = snapshot.publication_policies?.find(
    (p) => p.resident_id === "reader",
  );
  const artifact = snapshot.runs.find(
    (r) => r.status === "succeeded" && r.artifact_id,
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
    <section className="output approvals" aria-label="Mock approvals">
      <span className="eyebrow">TOWNHALL / MOCK NOTICEBOARD</span>
      <h2>Review before publication.</h2>
      <p>
        A separate operator drill publishes a synthetic summary to a local mock
        noticeboard. Reader remains read-only. Nothing is sent outside Hearth.
      </p>
      <div className="task-actions">
        <button
          disabled={busy || !snapshot.residents.length}
          onClick={() =>
            void act(() =>
              client.publicationPolicy(
                "reader",
                !policy?.enabled,
                policy?.revision ?? 0,
              ),
            )
          }
        >
          {policy?.enabled
            ? "Revoke mock publication"
            : "Allow mock publication requests"}
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
          No publication requests yet. Complete a mock summary to begin.
        </p>
      )}
      <ul className="tasks">
        {proposals.map((p) => {
          const action = snapshot.actions?.find((a) => a.id === p.id);
          return (
            <li key={p.id} id={`approval-${p.id}`}>
              <h3>Summary for the mock noticeboard</h3>
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
                      {action
                        ? "Reconcile mock action"
                        : "Publish approved mock summary"}
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
              Approve this exact mock action
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
              Deny this mock action
            </button>
            <button onClick={() => setReview(null)}>Close review</button>
          </div>
        </div>
      )}
    </section>
  );
}
