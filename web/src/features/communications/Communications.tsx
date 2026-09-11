import { useEffect, useState } from "react";
import {
  Client,
  RequestError,
  type Resident,
  type Runtimes,
} from "../../shared/client";
import {
  api,
  time,
  words,
  cost,
  type Conversation,
  type Transcript,
  type Operation,
  type Delivery,
  type Usage,
} from "./api";
import { CommunicationSettings } from "./Settings";
import "./communications.css";
const sections = [
  "Conversations",
  "Announcements",
  "Notification deliveries",
  "Settings & diagnostics",
  "Usage",
] as const;
type Section = (typeof sections)[number];
export function Source({
  task,
  run,
}: {
  task: string | null;
  run: string | null;
}) {
  return (
    <p>
      {task && <span>Task {task} · </span>}
      {run && (
        <a href={`#runs/${encodeURIComponent(run)}`}>Open source run {run}</a>
      )}
    </p>
  );
}
export function Communications({
  client,
  readOnly,
  residentId,
  residents,
  runtimes,
}: {
  client: Client;
  readOnly: boolean;
  residentId?: string;
  residents?: Resident[];
  runtimes?: Runtimes;
}) {
  const [section, setSection] = useState<Section>("Conversations");
  return (
    <section
      className="communications resident-card resident-document"
      aria-label="Communications"
    >
      {residentId && <h2>Communications</h2>}
      <p>
        External conversations, resident announcements and operator notification
        deliveries. Resident Letters remain in their own section;{" "}
        <a href="#inbox">Inbox read status</a> is independent of delivery.
      </p>
      {readOnly && (
        <p className="notice">
          This restored copy is read-only. Activation, probing and changes are
          disabled.
        </p>
      )}
      <div className="communications-tabs" aria-label="Communications sections">
        {sections.map((name) => (
          <button
            key={name}
            aria-pressed={section === name}
            onClick={() => setSection(name)}
          >
            {name}
          </button>
        ))}
      </div>
      {section === "Settings & diagnostics" ? (
        <CommunicationSettings
          client={client}
          readOnly={readOnly}
          residentId={residentId}
          residents={residents}
          runtimes={runtimes}
        />
      ) : section === "Usage" ? (
        <UsageList client={client} />
      ) : (
        <Records
          key={`${section}:${residentId ?? "all"}`}
          client={client}
          readOnly={readOnly}
          residentId={residentId}
          section={section}
        />
      )}
    </section>
  );
}
function Records({
  client,
  readOnly,
  residentId,
  section,
}: {
  client: Client;
  readOnly: boolean;
  residentId?: string;
  section: Section;
}) {
  const [items, setItems] = useState<(Conversation | Operation)[]>([]);
  const [cursor, setCursor] = useState<string | number | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const conversations = section === "Conversations";
  async function load(next: string | number | null = null) {
    setLoading(true);
    setError("");
    try {
      const q = new URLSearchParams({ limit: "20" });
      if (residentId) q.set("resident_id", residentId);
      if (!conversations)
        q.set(
          "kind",
          section === "Announcements" ? "announcement" : "notification",
        );
      if (next !== null)
        q.set(conversations ? "after" : "offset", String(next));
      const result = await api(client).get<{
        items: (Conversation | Operation)[];
        next_after?: string | null;
        next_offset?: number | null;
      }>(`/${conversations ? "conversations" : "deliveries"}?${q}`);
      setItems(result.items);
      setCursor(result.next_after ?? result.next_offset ?? null);
      setSelected(null);
    } catch (e) {
      setError(
        e instanceof Error ? e.message : "Could not load communications.",
      );
    } finally {
      setLoading(false);
    }
  }
  useEffect(() => {
    void load();
  }, [client, residentId, section]);
  return (
    <>
      <h3>{section}</h3>
      <button disabled={loading} onClick={() => void load()}>
        Reload {section.toLowerCase()}
      </button>
      {error && <p role="alert">{error}</p>}
      {loading && <p role="status">Loading communications…</p>}
      {!loading && !error && !items.length && (
        <p>No {section.toLowerCase()} recorded.</p>
      )}
      <ul className="communications-records">
        {items.map((item) => (
          <li key={item.id}>
            <button onClick={() => setSelected(item.id)}>
              {"channel_label" in item
                ? `${item.channel_label || item.channel_id} · ${item.busy ? "Busy — work or reply unresolved" : "Closed"}`
                : `${item.id} · ${words(item.state)}`}
            </button>
            <small>
              {time("last_at" in item ? item.last_at : item.created_at)}
            </small>
            {"task_id" in item && (
              <Source task={item.task_id} run={item.run_id} />
            )}
          </li>
        ))}
      </ul>
      {cursor !== null && (
        <button disabled={loading} onClick={() => void load(cursor)}>
          Next bounded page
        </button>
      )}
      {selected &&
        (conversations ? (
          <ConversationDetail
            key={selected}
            client={client}
            id={selected}
            readOnly={readOnly}
          />
        ) : (
          <DeliveryDetail
            key={selected}
            client={client}
            id={selected}
            readOnly={readOnly}
          />
        ))}
    </>
  );
}
function ConversationDetail({
  client,
  id,
  readOnly,
}: {
  client: Client;
  id: string;
  readOnly: boolean;
}) {
  const [data, setData] = useState<Transcript | null>(null);
  const [error, setError] = useState("");
  const [delivery, setDelivery] = useState<string | null>(null);
  async function load(before?: string) {
    setError("");
    try {
      setData(
        await api(client).get<Transcript>(
          `/conversations/${encodeURIComponent(id)}?limit=20${before ? `&before=${encodeURIComponent(before)}` : ""}`,
        ),
      );
    } catch (e) {
      setError(String(e));
    }
  }
  useEffect(() => {
    void load();
  }, [client, id]);
  return (
    <section aria-label="Conversation detail">
      <h3>Conversation detail</h3>
      {error && <p role="alert">{error}</p>}
      {data && (
        <>
          <p>
            Source: {data.route.label || data.conversation.channel_id} · sender{" "}
            {data.conversation.sender_id}
          </p>
          <p>{data.retention}</p>
          <ol className="communications-records">
            {data.turns.map((turn) => (
              <li key={turn.id}>
                <h4>Inbound · {time(turn.created_at)}</h4>
                <p>
                  Turn: {words(turn.state)}
                  {turn.reason && ` · ${words(turn.reason)}`}
                </p>
                <pre>{turn.text ?? "Transcript not retained"}</pre>
                {turn.omission && <p>Text omitted: {words(turn.omission)}</p>}
                <Source task={turn.task_id} run={turn.run_id} />
                <p>
                  Run: {turn.run_status ?? "Not started"} ·{" "}
                  {cost(turn.usage_known, turn.actual_cost)}
                </p>
                <p>
                  Outbound reply:{" "}
                  {turn.delivery_state
                    ? words(turn.delivery_state)
                    : turn.reason
                      ? words(turn.reason)
                      : "Not prepared"}
                </p>
                {turn.reply && <pre>{turn.reply}</pre>}
                {turn.delivery_state === "unknown" && (
                  <p className="notice">
                    Reply delivery is unknown. This conversation remains busy
                    even when its run has completed.
                  </p>
                )}
                {turn.operation_id && (
                  <button onClick={() => setDelivery(turn.operation_id)}>
                    Inspect reply delivery
                  </button>
                )}
              </li>
            ))}
          </ol>
          {data.next_before && (
            <button onClick={() => void load(data.next_before!)}>
              Older bounded turns
            </button>
          )}
          <h4>Dropped messages in this channel</h4>
          <p>
            Dropped bodies are not retained. Counts cover this channel, not just
            this sender.
          </p>
          {data.dropped_in_channel.length ? (
            <ul>
              {data.dropped_in_channel.map((drop, i) => (
                <li key={i}>
                  {drop.count} · {words(drop.reason ?? "unspecified")}
                </li>
              ))}
            </ul>
          ) : (
            <p>No drops recorded.</p>
          )}
        </>
      )}
      {delivery && (
        <DeliveryDetail
          key={delivery}
          client={client}
          id={delivery}
          readOnly={readOnly || !!data?.read_only}
        />
      )}
    </section>
  );
}
export function DeliveryDetail({
  client,
  id,
  readOnly,
}: {
  client: Client;
  id: string;
  readOnly: boolean;
}) {
  const [data, setData] = useState<Delivery | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const [action, setAction] = useState("");
  const [reason, setReason] = useState("");
  const [ack, setAck] = useState(false);
  const [evidence, setEvidence] = useState<
    Record<string, { code: string; external: string; outcome?: string }>
  >({});
  async function load() {
    setBusy(true);
    setError("");
    try {
      setData(
        await api(client).get<Delivery>(
          `/deliveries/${encodeURIComponent(id)}`,
        ),
      );
      setAction("");
      setAck(false);
      setEvidence({});
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  }
  useEffect(() => {
    void load();
  }, [client, id]);
  const [related, setRelated] = useState<string | null>(null);
  const lateAttempts = new Set(
    data?.resolutions
      .filter((r) => r.action === "late_receipt")
      .flatMap((r) => {
        const e = r.evidence as { attempt_id?: string } | null;
        return e?.attempt_id ? [e.attempt_id] : [];
      }),
  );
  const uncertain =
    data?.attempts.filter((a) =>
      data.uncertain_attempt_ids
        ? data.uncertain_attempt_ids.includes(a.id)
        : a.state === "unknown" ||
          a.state === "dispatching" ||
          lateAttempts.has(a.id),
    ) ?? [];
  async function resolve(event: React.FormEvent) {
    event.preventDefault();
    if (!data || readOnly || data.read_only) return;
    setBusy(true);
    setError("");
    setMessage("");
    try {
      const result = await api(client).write(
        `/deliveries/${encodeURIComponent(id)}/resolve`,
        {
          expected_revision: data.revision,
          action,
          reason,
          duplicate_risk_acknowledged: ack,
          ...(["sent", "not_sent"].includes(action)
            ? {
                evidence: uncertain.map((attempt) => ({
                  attempt_id: attempt.id,
                  intent_sha256: data.sha256,
                  outcome:
                    action === "sent"
                      ? (evidence[attempt.id]?.outcome ?? "confirmed")
                      : "safe_failure",
                  evidence: evidence[attempt.id]?.code,
                  external_id:
                    action === "sent" &&
                    (evidence[attempt.id]?.outcome ?? "confirmed") ===
                      "confirmed"
                      ? evidence[attempt.id]?.external
                      : null,
                })),
              }
            : {}),
        },
      );
      setMessage(
        `Resolution recorded.${result && typeof result === "object" && "reissued_operation_id" in result && result.reissued_operation_id ? ` Linked operation: ${result.reissued_operation_id}` : ""}`,
      );
      await load();
    } catch (e) {
      setError(
        e instanceof RequestError && e.status === 409
          ? `${e.message}. Reload delivery to review current evidence before resolving again.`
          : String(e),
      );
    } finally {
      setBusy(false);
    }
  }
  return (
    <section className="communications-detail" aria-label="Delivery detail">
      <h3>Delivery detail</h3>
      <button disabled={busy} onClick={() => void load()}>
        Reload delivery
      </button>
      {error && <p role="alert">{error}</p>}
      {message && <p role="status">{message}</p>}
      {related && (
        <DeliveryDetail
          key={related}
          client={client}
          id={related}
          readOnly={readOnly}
        />
      )}
      {data && (
        <>
          <p>
            Outbound {data.kind} · {words(data.state)} · revision{" "}
            {data.revision}
          </p>
          <p>
            Created {time(data.created_at)} · next eligible{" "}
            {time(data.eligible_at)}
          </p>
          <p>Destination: {Object.values(data.destination).join(" / ")}</p>
          <Source task={data.task_id} run={data.run_id} />
          {data.routine_id && (
            <p>
              Source routine {data.routine_id} ·{" "}
              <a href="#routines">Routines</a>
            </p>
          )}
          <p>Source record {data.source_id}</p>
          {data.run && (
            <p>
              Run: {data.run.status} ·{" "}
              {cost(data.run.usage_known, data.run.actual_cost)}. Run outcome
              and delivery outcome are independent.
            </p>
          )}
          {data.parent_id && (
            <p>
              Reissued from{" "}
              <button
                onClick={() => {
                  setRelated(data.parent_id);
                }}
              >
                {data.parent_id}
              </button>
            </p>
          )}
          <pre>{data.text}</pre>
          {data.state === "unknown" && (
            <p className="notice">
              The external effect is unknown. Abandon ends local work without
              proving that nothing was sent. Reissue can create a duplicate.
            </p>
          )}
          {data.current_authority && (
            <p>
              Current authority:{" "}
              {data.current_authority.allowed ? "Allowed" : "Refused"}
              {data.current_authority.reason &&
                ` · ${words(data.current_authority.reason)}`}
            </p>
          )}
          {data.latest_reason && (
            <p>
              Latest delivery evidence: {words(data.latest_reason.reason)} ·{" "}
              {time(data.latest_reason.at)}
            </p>
          )}
          {data.notification && (
            <p>
              Operator notification {data.notification.kind} ·{" "}
              {data.notification.read_at == null
                ? "Unread in Inbox"
                : `Read in Inbox ${time(data.notification.read_at)}`}{" "}
              · read status is independent of delivery.
            </p>
          )}
          <h4>Dispatch and receipt evidence</h4>
          <p>
            Dispatch authorization precedes the external request. A later
            cancellation or revocation cannot retract an authorized send.
          </p>
          <ul>
            {data.attempts.map((attempt) => (
              <li key={attempt.id}>
                Attempt {attempt.id} · {words(attempt.state)} · authorized{" "}
                {time(attempt.dispatched_at)} · completed{" "}
                {time(attempt.completed_at)} ·{" "}
                {attempt.evidence ?? "No receipt evidence"}
                {attempt.external_id &&
                  ` · external receipt ${attempt.external_id}`}
              </li>
            ))}
          </ul>
          <h4>Resolutions ({data.resolution_count})</h4>
          <ul>
            {data.resolutions.map((r) => (
              <li key={r.revision}>
                {time(r.at)} · {words(r.action)} · {r.reason}
                <pre>
                  {r.evidence
                    ? JSON.stringify(r.evidence, null, 2)
                    : "No external receipt asserted"}
                </pre>
              </li>
            ))}
          </ul>
          {!!data.actions.length && !readOnly && !data.read_only && (
            <form onSubmit={resolve}>
              <label>
                Resolution
                <select
                  disabled={busy}
                  aria-label="Resolution"
                  required
                  value={action}
                  onChange={(e) => setAction(e.target.value)}
                >
                  <option value="">Choose an explicit outcome</option>
                  {data.actions.map((a) => (
                    <option key={a} value={a}>
                      {
                        (
                          {
                            sent: "Confirmed sent evidence",
                            not_sent: "Affirmative not-sent evidence",
                            abandon: "Abandon local work",
                            reissue: "Reissue with duplicate risk",
                            cancel: "Cancel queued work",
                          } as Record<string, string>
                        )[a]
                      }
                    </option>
                  ))}
                </select>
              </label>
              <label>
                Reason
                <input
                  required
                  maxLength={256}
                  value={reason}
                  onChange={(e) => setReason(e.target.value)}
                />
              </label>
              {["sent", "not_sent"].includes(action) &&
                uncertain.map((attempt) => (
                  <fieldset key={attempt.id}>
                    <legend>Evidence for attempt {attempt.id}</legend>
                    {action === "sent" && (
                      <label>
                        Attempt outcome
                        <select
                          value={evidence[attempt.id]?.outcome ?? "confirmed"}
                          onChange={(e) =>
                            setEvidence({
                              ...evidence,
                              [attempt.id]: {
                                code: evidence[attempt.id]?.code ?? "",
                                external: evidence[attempt.id]?.external ?? "",
                                outcome: e.target.value,
                              },
                            })
                          }
                        >
                          <option value="confirmed">Confirmed sent</option>
                          <option value="safe_failure">
                            Affirmatively not sent
                          </option>
                          <option value="refused">Refused before send</option>
                        </select>
                      </label>
                    )}
                    <label>
                      Evidence code
                      <input
                        required
                        pattern="[a-z][a-z0-9_]*"
                        maxLength={64}
                        value={evidence[attempt.id]?.code ?? ""}
                        onChange={(e) =>
                          setEvidence({
                            ...evidence,
                            [attempt.id]: {
                              ...evidence[attempt.id],
                              external: evidence[attempt.id]?.external ?? "",
                              code: e.target.value,
                            },
                          })
                        }
                      />
                    </label>
                    {action === "sent" &&
                      (evidence[attempt.id]?.outcome ?? "confirmed") ===
                        "confirmed" && (
                        <label>
                          External receipt ID
                          <input
                            required
                            maxLength={128}
                            pattern="[A-Za-z0-9_-]+"
                            value={evidence[attempt.id]?.external ?? ""}
                            onChange={(e) =>
                              setEvidence({
                                ...evidence,
                                [attempt.id]: {
                                  ...evidence[attempt.id],
                                  code: evidence[attempt.id]?.code ?? "",
                                  external: e.target.value,
                                },
                              })
                            }
                          />
                        </label>
                      )}
                  </fieldset>
                ))}
              {action === "reissue" && (
                <label>
                  <input
                    type="checkbox"
                    required
                    checked={ack}
                    onChange={(e) => setAck(e.target.checked)}
                  />
                  I acknowledge that a new send may duplicate the original
                  unknown send.
                </label>
              )}
              <button
                type="button"
                disabled={busy}
                onClick={() => {
                  setAction("");
                  setReason("");
                  setAck(false);
                  setEvidence({});
                }}
              >
                Cancel resolution
              </button>
              <button
                disabled={busy || !action || (action === "reissue" && !ack)}
              >
                Record resolution
              </button>
            </form>
          )}
        </>
      )}
    </section>
  );
}
function UsageList({ client }: { client: Client }) {
  const [data, setData] = useState<Usage | null>(null);
  const [error, setError] = useState("");
  const [offset, setOffset] = useState(0);
  useEffect(() => {
    let live = true;
    api(client)
      .get<Usage>(`/usage?limit=20&offset=${offset}`)
      .then(
        (v) => {
          if (live) setData(v);
        },
        (e) => {
          if (live) setError(String(e));
        },
      );
    return () => {
      live = false;
    };
  }, [client, offset]);
  return (
    <section>
      <h3>Usage by task origin</h3>
      <p>
        Known API-equivalent costs are estimates. Unknown runs are not counted
        as zero cost.
      </p>
      {error && <p role="alert">{error}</p>}
      {data && (
        <>
          <ul>
            {data.origins.map((item) => (
              <li key={item.root_task_id}>
                {words(item.origin)} · task {item.root_task_id} · {item.runs}{" "}
                runs · ${(item.known_cost / 1e6).toFixed(4)} known ·{" "}
                {item.unknown_runs} unknown · {item.active_runs ?? 0} active · $
                {((item.reserved ?? 0) / 1e6).toFixed(4)} reserved estimate
              </li>
            ))}
          </ul>
          {!data.origins.length && <p>No usage recorded.</p>}
          {data.truncated && (
            <button onClick={() => setOffset(offset + 20)}>
              Next usage page
            </button>
          )}
        </>
      )}
    </section>
  );
}
