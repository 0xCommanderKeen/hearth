export type Routine = {
  id: string;
  resident_id: string;
  revision: number;
  enabled: number;
  next_at: number;
  instruction: string;
  local_time: string;
  timezone: string;
};
export type Approval = {
  id: string;
  artifact_id: string;
  resident_id: string;
  digest: string;
  expires_at: number;
  status: string;
  payload: {
    action: string;
    destination: string;
    sha256: string;
    resident_revision: number;
    policy_revision: number;
    destination_revision: number;
  };
};
export type Resident = {
  id: string;
  name: string;
  purpose: string;
  revision: number;
  daily_limit: number;
  budget_timezone?: string;
  presence: string;
  pause_reason: string | null;
  operator_paused?: number;
  control_revision?: number;
};
export type ResidentDeclaration = {
  id: string;
  revision: number;
  declaration: {
    name: string;
    purpose: string;
    daily_limit: number;
    budget_timezone: string;
    skill_text: string;
  };
};
export type Task = {
  id: string;
  resident_id: string;
  instruction: string;
  status: string;
  created_at: number;
};
export type Run = {
  id: string;
  task_id: string;
  resident_id: string;
  status: string;
  artifact_id: string | null;
  actual_cost: number | null;
  usage_known: number;
  usage_source?: string;
  cancellation_requested: number;
};
export type Snapshot = {
  restore_hold?: boolean;
  schema_version: 1;
  simulated: true;
  epoch: string;
  cursor: number;
  residents: Resident[];
  tasks: Task[];
  runs: Run[];
  notifications?: {
    id: string;
    kind: string;
    resource_id: string;
    status: string;
    attempts: number;
    next_at: number;
    reason: string | null;
    payload: { simulated: boolean; link: string };
  }[];
  routines?: Routine[];
  occurrences?: {
    routine_id: string;
    scheduled_at: number;
    status: string;
    task_id: string | null;
  }[];
  approvals?: Approval[];
  publication_policies?: {
    resident_id: string;
    revision: number;
    enabled: number;
  }[];
  actions?: { id: string; status: string; reason: string | null }[];
  activity: {
    sequence: number;
    kind: string;
    resource_id: string;
    at: number;
  }[];
};
export type PendingTask = {
  id: string;
  body: { resident_id: string; instruction: string; expires_at: number };
};

export class RequestError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
  }
}

export class StateFormatError extends Error {}

export function decodeSnapshot(value: unknown): Snapshot {
  const s = value as Partial<Snapshot> | null;
  if (
    !s ||
    s.schema_version !== 1 ||
    s.simulated !== true ||
    typeof s.epoch !== "string" ||
    !Number.isSafeInteger(s.cursor) ||
    !Array.isArray(s.residents) ||
    !Array.isArray(s.tasks) ||
    !Array.isArray(s.runs) ||
    !Array.isArray(s.activity)
  ) {
    throw new StateFormatError(
      "This interface cannot read the server’s state format.",
    );
  }
  return s as Snapshot;
}

export class Client {
  private readOnly = false;
  constructor(private token: string) {}
  clear() {
    this.token = "";
  }

  async request<T>(path: string, options: RequestInit = {}): Promise<T> {
    if (
      this.readOnly &&
      !["GET", "HEAD"].includes((options.method ?? "GET").toUpperCase())
    )
      throw new RequestError(409, "This restored copy is read-only.");
    // Callers supply only local API paths; credentials cannot be redirected to another origin.
    if (!path.startsWith("/api/") || path.includes("\\"))
      throw new Error("Invalid local API path");
    const response = await fetch(path, {
      ...options,
      redirect: "error",
      cache: "no-store",
      headers: {
        ...options.headers,
        "Content-Type": "application/json",
        Authorization: `Bearer ${this.token}`,
      },
    });
    if (!response.ok) {
      if (response.status === 401) this.clear();
      const error = await response.json().catch(() => ({}));
      throw new RequestError(
        response.status,
        typeof error.error === "string"
          ? error.error.replaceAll("_", " ")
          : `Request failed (${response.status})`,
      );
    }
    return response.json();
  }

  async state() {
    const state = decodeSnapshot(await this.request("/api/state"));
    this.readOnly = state.restore_hold === true;
    return state;
  }
  seed() {
    return this.request("/api/demo/reader", { method: "POST" });
  }
  resident(id: string) {
    return this.request<ResidentDeclaration>(
      `/api/residents/${encodeURIComponent(id)}`,
    );
  }
  saveResident(resident: ResidentDeclaration, skillText: string) {
    return this.request<ResidentDeclaration>(
      `/api/residents/${encodeURIComponent(resident.id)}`,
      {
        method: "PUT",
        body: JSON.stringify({
          ...resident.declaration,
          skill_text: skillText,
          expected_revision: resident.revision,
        }),
      },
    );
  }
  start(id: string) {
    return this.request(`/api/tasks/${encodeURIComponent(id)}/start`, {
      method: "POST",
    });
  }
  reconcileUsage(
    id: string,
    command: string,
    amount: number,
    evidence: string,
  ) {
    return this.request(`/api/runs/${encodeURIComponent(id)}/usage`, {
      method: "POST",
      headers: { "Idempotency-Key": command },
      body: JSON.stringify({ amount, evidence }),
    });
  }
  pauseResident(id: string, paused: boolean, revision: number) {
    return this.request(`/api/residents/${encodeURIComponent(id)}/pause`, {
      method: "POST",
      body: JSON.stringify({ paused, expected_revision: revision }),
    });
  }
  run(id: string) {
    return this.request<{
      id: string;
      status: string;
      artifact_id: string | null;
    }>(`/api/runs/${encodeURIComponent(id)}`);
  }
  cancel(id: string) {
    return this.request(`/api/runs/${encodeURIComponent(id)}/cancel`, {
      method: "POST",
    });
  }
  artifact(id: string) {
    return this.request<{ content: string }>(
      `/api/artifacts/${encodeURIComponent(id)}`,
    );
  }

  saveRoutine(
    id: string,
    body: {
      resident_id: string;
      instruction: string;
      local_time: string;
      timezone: string;
      enabled: boolean;
      expected_revision: number;
    },
  ) {
    return this.request(`/api/routines/${encodeURIComponent(id)}`, {
      method: "POST",
      body: JSON.stringify(body),
    });
  }
  publicationPolicy(resident: string, enabled: boolean, revision: number) {
    return this.request(
      `/api/residents/${encodeURIComponent(resident)}/publication-policy`,
      {
        method: "POST",
        body: JSON.stringify({ enabled, expected_revision: revision }),
      },
    );
  }
  propose(id: string, artifact: string, expires: number) {
    return this.request<Approval>("/api/approvals", {
      method: "POST",
      headers: { "Idempotency-Key": id },
      body: JSON.stringify({ artifact_id: artifact, expires_at: expires }),
    });
  }
  review(id: string) {
    return this.request<{ approval: Approval; content: string }>(
      `/api/approvals/${encodeURIComponent(id)}`,
    );
  }
  decide(approval: Approval, approve: boolean) {
    return this.request<Approval>(
      `/api/approvals/${encodeURIComponent(approval.id)}/decision`,
      {
        method: "POST",
        body: JSON.stringify({ reviewed_digest: approval.digest, approve }),
      },
    );
  }
  execute(id: string) {
    return this.request(`/api/approvals/${encodeURIComponent(id)}/execute`, {
      method: "POST",
    });
  }

  async submit(pending: PendingTask) {
    let receipt: { task_id: string; command_id: string };
    try {
      receipt = await this.request<{ task_id: string; command_id: string }>(
        "/api/tasks",
        {
          method: "POST",
          headers: { "Idempotency-Key": pending.id },
          body: JSON.stringify(pending.body),
        },
      );
    } catch (error) {
      if (
        error instanceof RequestError &&
        error.status === 409 &&
        error.message === "invalid command deadline"
      ) {
        try {
          receipt = await this.request(
            `/api/commands/${encodeURIComponent(pending.id)}`,
          );
        } catch (lookup) {
          if (lookup instanceof RequestError && lookup.status === 404)
            throw new RequestError(
              410,
              "The submission expired without being accepted. You can submit a new task.",
            );
          throw lookup;
        }
      } else {
        throw error;
      }
    }
    if (
      receipt.command_id !== pending.id ||
      typeof receipt.task_id !== "string"
    )
      throw new Error(
        "The submission receipt is incomplete. Retry to reconcile it.",
      );
    return receipt;
  }

  async watch(
    signal: AbortSignal,
    onState: (s: Snapshot) => void,
    onConnection: (s: boolean) => void,
  ) {
    while (!signal.aborted) {
      try {
        const initial = await this.state();
        if (signal.aborted) return;
        onState(initial);
        const response = await fetch(
          `/api/events?cursor=${initial.cursor}&epoch=${encodeURIComponent(initial.epoch)}`,
          {
            headers: { Authorization: `Bearer ${this.token}` },
            signal,
            redirect: "error",
            cache: "no-store",
          },
        );
        if (response.status === 401) {
          this.clear();
          throw new RequestError(401, "Session expired");
        }
        if (!response.ok || !response.body)
          throw new Error("Activity connection unavailable");
        onConnection(true);
        const reader = response.body.getReader();
        const decoder = new TextDecoder();
        let buffer = "";
        try {
          while (!signal.aborted) {
            const chunk = await reader.read();
            if (chunk.done) break;
            buffer += decoder.decode(chunk.value, { stream: true });
            if (buffer.length > 2_000_000)
              throw new Error("Activity snapshot too large");
            let boundary: number;
            while ((boundary = buffer.indexOf("\n\n")) >= 0) {
              const frame = buffer.slice(0, boundary);
              buffer = buffer.slice(boundary + 2);
              const data = frame
                .split("\n")
                .find((line) => line.startsWith("data: "));
              if (data) onState(decodeSnapshot(JSON.parse(data.slice(6))));
            }
          }
        } finally {
          await reader.cancel().catch(() => {});
        }
      } catch (error) {
        if (signal.aborted) return;
        if (
          (error instanceof RequestError && error.status === 401) ||
          error instanceof StateFormatError
        ) {
          onConnection(false);
          throw error;
        }
      }
      onConnection(false);
      await new Promise<void>((resolve) => {
        const done = () => {
          clearTimeout(timer);
          signal.removeEventListener("abort", done);
          resolve();
        };
        const timer = setTimeout(done, 1000);
        signal.addEventListener("abort", done, { once: true });
      });
    }
  }
}
