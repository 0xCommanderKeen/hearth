import { useEffect, useRef, useState } from "react";
import {
  Client,
  RequestError,
  type ImportReceipt,
  type ImportRequest,
  type ResidentBundle,
} from "../../shared/client";
import "./provisioning.css";

const LIMITS = { skills: 8, input_sets: 4, bytes: 1_500_000 };

function parseBundle(text: string): ResidentBundle {
  if (text.length > LIMITS.bytes)
    throw new Error("This file is larger than a resident bundle can be.");
  let value: unknown;
  try {
    value = JSON.parse(text);
  } catch {
    throw new Error("This file is not JSON.");
  }
  const bundle = value as ResidentBundle;
  if (
    typeof bundle !== "object" ||
    bundle === null ||
    bundle.bundle_version !== 1 ||
    typeof bundle.resident?.name !== "string" ||
    typeof bundle.resident?.purpose !== "string" ||
    !Array.isArray(bundle.skills) ||
    !Array.isArray(bundle.input_sets)
  )
    throw new Error("This file is not a Hearth resident bundle (version 1).");
  if (bundle.skills.length > LIMITS.skills)
    throw new Error("A resident carries at most 8 skills.");
  if (bundle.input_sets.length > LIMITS.input_sets)
    throw new Error("A resident carries at most 4 input sets.");
  return bundle;
}

export function ImportResident({
  client,
  readOnly,
}: {
  client: Client;
  readOnly: boolean;
}) {
  const [bundle, setBundle] = useState<ResidentBundle | null>(null);
  const [fileName, setFileName] = useState("");
  const [name, setName] = useState("");
  const [dailyLimit, setDailyLimit] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [uncertain, setUncertain] = useState(false);
  const [receipt, setReceipt] = useState<ImportReceipt | null>(null);
  const pending = useRef<{ id: string; body: ImportRequest } | null>(null);
  const live = useRef(true);
  useEffect(() => {
    live.current = true;
    return () => {
      live.current = false;
    };
  }, []);

  async function choose(file: File | undefined) {
    setError("");
    setReceipt(null);
    pending.current = null;
    setUncertain(false);
    if (!file) return;
    try {
      const parsed = parseBundle(await file.text());
      if (!live.current) return;
      setBundle(parsed);
      setFileName(file.name);
      setName(parsed.resident.name);
      setDailyLimit((parsed.resident.daily_limit / 1e6).toFixed(2));
    } catch (e) {
      if (!live.current) return;
      setBundle(null);
      setError(e instanceof Error ? e.message : "Unreadable file");
    }
  }

  async function submit() {
    if (!bundle || busy || readOnly) return;
    if (!pending.current) {
      const overrides: NonNullable<ImportRequest["overrides"]> = {};
      const trimmed = name.trim();
      if (trimmed && trimmed !== bundle.resident.name) overrides.name = trimmed;
      const limit = Math.round(Number(dailyLimit) * 1e6);
      if (
        Number.isSafeInteger(limit) &&
        limit >= 0 &&
        limit !== bundle.resident.daily_limit
      )
        overrides.daily_limit = limit;
      pending.current = {
        id: crypto.randomUUID(),
        body: Object.keys(overrides).length
          ? { bundle, overrides }
          : { bundle },
      };
    }
    setBusy(true);
    setError("");
    try {
      const result = await client.importResident(
        pending.current.id,
        pending.current.body,
      );
      if (!live.current) return;
      setReceipt(result);
      setUncertain(false);
      if (result.status === "ready")
        window.location.hash = `#new-resident/${encodeURIComponent(result.command_id)}`;
    } catch (e) {
      if (!live.current) return;
      if (
        e instanceof RequestError &&
        [401, 409, 413, 422].includes(e.status)
      ) {
        pending.current = null;
        setUncertain(false);
        setError(e.message);
      } else {
        setUncertain(true);
        setError(
          "Import is unconfirmed. Retry the same operation to recover its result; the exact request is retained.",
        );
      }
    } finally {
      if (live.current) setBusy(false);
    }
  }

  const substituted =
    bundle && receipt?.resolution.execution_profile.requested !== undefined
      ? receipt.resolution.execution_profile
      : null;
  return (
    <section className="provisioning" aria-label="Import resident bundle">
      <h2>Choose a resident bundle</h2>
      <p>
        A bundle is a resident definition exported from Hearth: purpose,
        instructions, memory, exact skill and input content, and one daily
        routine. Skills and inputs already in this library are reused when their
        content matches; the rest are created. Nothing about the source's runs,
        history or spending is imported.
      </p>
      <label>
        Bundle file
        <input
          type="file"
          accept=".json,application/json"
          disabled={busy || readOnly}
          onChange={(e) => void choose(e.target.files?.[0])}
        />
      </label>
      {error && (
        <p className="notice error" role="alert">
          {error}
        </p>
      )}
      {readOnly && <p>This restored copy is read-only; import is disabled.</p>}
      {bundle && (
        <div className="import-summary" aria-label="Bundle summary">
          <h3>{bundle.resident.name}</h3>
          <p>{bundle.resident.purpose}</p>
          <dl className="provision-fields">
            <div>
              <dt>File</dt>
              <dd>{fileName}</dd>
            </div>
            <div>
              <dt>Skills</dt>
              <dd>
                {bundle.skills.length
                  ? bundle.skills.map((skill) => skill.name).join(", ")
                  : "None"}
              </dd>
            </div>
            <div>
              <dt>Input sets</dt>
              <dd>
                {bundle.input_sets.length
                  ? bundle.input_sets.map((item) => item.name).join(", ")
                  : "None"}
              </dd>
            </div>
            <div>
              <dt>Routine</dt>
              <dd>
                {bundle.routine
                  ? `Daily at ${bundle.routine.local_time} (${bundle.routine.timezone})`
                  : "None"}
              </dd>
            </div>
            <div>
              <dt>Memory</dt>
              <dd>
                {bundle.resident.memory
                  ? `${bundle.resident.memory.length} characters`
                  : "Empty"}
              </dd>
            </div>
            <div>
              <dt>Exported profile</dt>
              <dd>{bundle.resident.execution_profile}</dd>
            </div>
          </dl>
          {bundle.management && (
            <p className="notice" role="status">
              This bundle carried a management grant. Authority never travels
              with a resident: the imported resident starts with no management
              tools. Grant them explicitly afterwards if you choose.
            </p>
          )}
          <p className="muted">
            The resident will use this installation&apos;s configured execution
            profile, which may differ from the exported one.
          </p>
          <div className="provision-fields">
            <label>
              Resident name
              <input
                value={name}
                maxLength={100}
                disabled={busy || readOnly || !!pending.current}
                onChange={(e) => setName(e.target.value)}
              />
            </label>
            <label>
              Daily limit ($)
              <input
                type="number"
                min="0"
                step="0.01"
                value={dailyLimit}
                disabled={busy || readOnly || !!pending.current}
                onChange={(e) => setDailyLimit(e.target.value)}
              />
            </label>
          </div>
          <div className="maintenance-actions">
            <button
              className="primary"
              disabled={busy || readOnly}
              onClick={() => void submit()}
            >
              {uncertain ? "Retry import" : "Import resident"}
            </button>
            <a className="btn" href="#residents">
              Cancel
            </a>
          </div>
          {receipt && receipt.status !== "ready" && (
            <p role="status">
              Import {receipt.status}
              {receipt.reason
                ? ` · ${receipt.reason.replaceAll("_", " ")}`
                : ""}
              .{" "}
              <a
                href={`#new-resident/${encodeURIComponent(receipt.command_id)}`}
              >
                Inspect setup and retry →
              </a>
            </p>
          )}
          {substituted && substituted.requested !== substituted.used && (
            <p role="status">
              Execution profile {substituted.requested} is not available here;{" "}
              {substituted.used} was used.
            </p>
          )}
        </div>
      )}
    </section>
  );
}
