export type SyntheticInput = {
  input_set_id: string;
  revision: number;
  name: string;
  notes: string[];
  sha256: string;
  synthetic: true;
  created_at: number;
  created_by: string;
  edited_at: number;
  edited_by: string;
};
export type InputChange = {
  command_id: string;
  input_set_id?: string;
  expected_revision?: number;
  name: string;
  notes: string[];
};
export type InputSelectionSet = {
  resident_id: string;
  revision: number;
  input_sets: SyntheticInput[];
};
export type InputProvenance = {
  input_sets?: Omit<SyntheticInput, "notes">[];
  inputs_error?: string | null;
  input_revision?: number | null;
  input_state?: "empty" | "configured" | "unavailable";
};
export type ProvisionRequest = {
  name: string;
  purpose: string;
  instructions: string;
  initial_memory: string;
  skills: { skill_id: string; revision: number }[];
  execution_profile: string;
  input_sets: { input_set_id: string }[];
  daily_limit: number;
  budget_timezone: string;
  creation_reason: string;
  manager: string;
  routine: {
    instruction: string;
    local_time: string;
    timezone: string;
    enabled: boolean;
  } | null;
  first_assignment: { instruction: string } | null;
};
export type ProvisionReceipt = {
  command_id: string;
  resident_id: string;
  status: "setup" | "ready" | "failed";
  reason: string | null;
  creator: string;
  manager: string;
  originating_run_id: string | null;
  created_at: number;
  routine_id: string | null;
  task_id: string | null;
  setup: ProvisionRequest;
};
export type ResidentBundle = {
  bundle_version: 1;
  source: {
    resident_id: string;
    declaration_revision: number;
    memory_revision: number;
    exported_at: number;
  };
  resident: {
    name: string;
    purpose: string;
    instructions: string;
    memory: string;
    daily_limit: number;
    budget_timezone: string;
    execution_profile: string;
    creation_reason: string;
  };
  skills: {
    name: string;
    description: string;
    instructions: string;
    sha256: string;
  }[];
  input_sets: { name: string; notes: string[]; sha256: string }[];
  routine: ProvisionRequest["routine"];
  management: Record<string, unknown> | null;
};
export type ImportRequest = {
  bundle: ResidentBundle;
  overrides?: { name?: string; daily_limit?: number; budget_timezone?: string };
  manager?: string;
};
export type ImportReceipt = ProvisionReceipt & {
  resolution: {
    skills: {
      name: string;
      skill_id: string;
      revision: number;
      outcome: string;
    }[];
    input_sets: {
      name: string;
      input_set_id: string;
      revision: number;
      outcome: string;
    }[];
    execution_profile: { requested: string; used: string };
    management_ignored: boolean;
  };
};
export type ResidentProfile = {
  inputs_error?: string | null;
  creator_name?: string;
  manager_name?: string;
  command_id: string | null;
  creator: string;
  manager: string;
  created_at: number;
  creation_reason: string;
  originating_run_id: string | null;
  execution_profile: string;
  input_sets: { input_set_id: string; name?: string }[];
  setup_status: string;
};
export type ResidentOptions = {
  execution_profiles: { id: string; name: string }[];
  input_sets: { input_set_id: string; name: string; synthetic: boolean }[];
  managers: { id: string; name: string }[];
};
export type AssignedSkill = Omit<
  CatalogSkill,
  "status" | "created_by" | "edited_by" | "created_at" | "edited_at"
> & { latest_revision?: number; catalog_status?: string };
export type AssignmentSet = {
  resident_id: string;
  revision: number;
  sha256: string;
  skills: AssignedSkill[];
};
export type AssignmentChange = {
  resident_id: string;
  command_id: string;
  expected_revision: number;
  skills: { skill_id: string; revision: number }[];
};
export type SkillUser = {
  resident_id: string;
  name: string;
  revision: number;
  position: number;
};
export type SkillDraft = {
  name: string;
  description: string;
  instructions: string;
  authoring?: SkillAuthoring | null;
};
export type SkillExample = {
  kind: "normal" | "edge";
  instruction: string;
  notes: string[];
  assertions: {
    max_characters: number;
    contains: string[];
    excludes: string[];
  };
};
export type SkillAuthoring = { examples: SkillExample[] };
export type SkillValidation = {
  validation_id: string;
  skill_id: string;
  candidate_revision: number;
  candidate_sha256: string;
  resident_id: string | null;
  memory_revision: number | null;
  status: "pending" | "passed" | "failed";
  reason: string | null;
  assessment: string;
  cases: {
    kind: "normal" | "edge";
    position: number;
    task_id: string;
    run_id: string | null;
    input_set_id: string;
    input_revision: number;
    input_sha256: string;
    result: {
      passed: boolean;
      reasons: string[];
      artifact_id: string | null;
      artifact_sha256: string | null;
      actual_cost: number;
      memory_revision?: number;
      resident_revision?: number;
      input_digest?: string;
      checks: {
        assertion: string;
        expected: string | number;
        actual?: number;
        passed: boolean;
      }[];
    } | null;
  }[];
};
export type SkillAuthoringEvidence = SkillAuthoring & {
  manifest_sha256: string;
  structure: { checker: string; passed: boolean; reasons: string[] };
  validation: SkillValidation | null;
  publication: {
    candidate_revision: number;
    validation_id: string;
    revision: number;
    sha256: string;
  } | null;
};
export type CatalogSkill = Omit<SkillDraft, "authoring"> & {
  skill_id: string;
  revision: number;
  status: "active" | "archived" | "draft";
  created_by: string;
  created_at: number;
  edited_by: string;
  edited_at: number;
  sha256: string;
  authoring?: SkillAuthoringEvidence | null;
  created_by_name?: string;
  edited_by_name?: string;
};
export type SkillReceipt = {
  command_id: string;
  skill_id: string;
  revision: number;
  operation: string;
  recorded_at: number;
  actor: string;
};
export type SkillChange = {
  command_id: string;
  skill_id?: string;
  content?: SkillDraft;
  expected_revision?: number;
};
export type HouseholdPolicy = {
  revision: number;
  daily_limit: number;
  timezone: string;
  resident_limit: number;
  concurrency_limit: number;
  resident_count: number;
  active_runs: number;
  uncertain_reserved?: number;
  spent: number;
  reserved: number;
  unknown: number;
  remaining: number;
  budget_day: string;
};
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
export type InboxNotification = {
  id: string;
  kind: string;
  resource_id: string;
  created_at: number;
  read_at: number | null;
  payload: { link: string };
};
export type Resident = InputProvenance & {
  lifecycle?: ResidentLifecycle;
  unresolved_runs?: number;
  safety_hold_reason?: string | null;
  profile?: ResidentProfile | null;
  management?: ManagementGrant | { enabled: false; error: string };
  id: string;
  name: string;
  purpose: string;
  revision: number;
  daily_limit: number;
  budget_timezone?: string;
  presence: string;
  pause_reason: string | null;
  operator_paused?: number | boolean;
  control_revision?: number;
  memory_revision?: number;
  skills?: AssignedSkill[];
  skills_error?: string | null;
};
export type ResidentLifecycle = {
  resident_id: string;
  state: "ready" | "paused" | "archived" | "unavailable";
  revision?: number;
  manager?: string;
  actor?: string;
  originating_run_id?: string | null;
  updated_at?: number;
  error?: string;
};
export type Configuration = {
  resident_id: string;
  lifecycle: ResidentLifecycle;
  execution_profile: string;
  declaration: {
    expected_revision: number;
    name: string;
    purpose: string;
    instructions: string;
    daily_limit: number;
    budget_timezone: string;
  };
  memory: { expected_revision: number; text: string };
  inputs: { expected_revision: number; input_sets: { input_set_id: string }[] };
  skills: {
    expected_revision: number;
    skills: { skill_id: string; revision: number }[];
  };
  routines: ConfigurationRoutine[];
};
export type ConfigurationRoutine = {
  routine_id: string;
  expected_revision: number;
  instruction: string;
  local_time: string;
  timezone: string;
  enabled: boolean;
};
export type ConfigurationChange = {
  expected_lifecycle_revision: number;
  declaration?: Configuration["declaration"];
  memory?: Configuration["memory"];
  inputs?: Configuration["inputs"];
  skills?: Configuration["skills"];
  routines?: ConfigurationRoutine[];
};
export type MaintenanceChange = {
  resident_id: string;
  command_id: string;
} & (
  | { kind: "configuration"; body: ConfigurationChange }
  | {
      kind: "lifecycle";
      body: {
        expected_revision: number;
        state: "ready" | "paused" | "archived";
      };
    }
  | { kind: "manager"; body: { expected_revision: number; manager: string } }
);
export type ResidentMemory = {
  resident_id: string;
  revision: number;
  sha256: string | null;
  text: string;
};
export type MemoryRevision = {
  revision: number;
  sha256: string;
  size: number;
  created_at: number;
  author: "operator" | "run";
  run_id: string | null;
};
export type MemoryHistory = {
  resident_id: string;
  limit: number;
  offset: number;
  total: number;
  revisions: MemoryRevision[];
};
export type JournalEntry = {
  resident_id: string;
  sequence: number;
  run_id: string;
  at: number;
  text: string;
};
export type ResidentJournal = {
  resident_id: string;
  limit: number;
  offset: number;
  total: number;
  entries: JournalEntry[];
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
export type Run = InputProvenance & {
  management?: {
    grant_revision: number;
    expires_at: number;
    calls: number;
  } | null;
  id: string;
  task_id: string;
  resident_id: string;
  status: string;
  artifact_id: string | null;
  actual_cost: number | null;
  usage_known: number;
  usage_source?: string;
  cancellation_requested: number;
  memory_revision?: number;
  memory_written?: number[];
  journal_opened?: number[];
  journal_written?: number | null;
  skills?: AssignedSkill[];
  skills_error?: string | null;
};
export type Snapshot = {
  provisioning?: (Omit<ProvisionReceipt, "setup"> & { name: string })[];
  household?: HouseholdPolicy;
  restore_hold?: boolean;
  schema_version: 1;
  epoch: string;
  cursor: number;
  residents: Resident[];
  tasks: Task[];
  runs: Run[];
  notifications?: InboxNotification[];
  routines?: Routine[];
  occurrences?: {
    routine_id: string;
    scheduled_at: number;
    status: string;
    task_id: string | null;
  }[];
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

export type ManagementCapability =
  | "create_residents"
  | "assign_work"
  | "routines"
  | "author_skills"
  | "update_residents"
  | "manage_lifecycle"
  | "assign_skills"
  | "writable_memory";
export type ManagementGrant = {
  resident_id: string;
  revision: number;
  enabled: boolean;
  profiles: string[];
  input_set_ids: string[];
  capabilities: ManagementCapability[];
  max_residents: number;
  max_daily_limit: number;
  max_reserve: number;
  max_calls: number;
};
export type ManagementChange = Omit<
  ManagementGrant,
  "resident_id" | "revision"
> & { expected_revision: number };
export type ManagementCatalog = {
  residents: { id: string; name: string; grant: ManagementGrant }[];
  profiles: string[];
  input_sets: {
    input_set_id: string;
    name: string;
    revision: number;
    synthetic: boolean;
  }[];
  operations: {
    resident_id: string;
    operation_id: string;
    actor: string;
    originating_run_id: string;
    status: string;
    resident_link: string;
    task_id?: string;
    run_id?: string;
  }[];
};

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

  managementCatalog() {
    return this.request<ManagementCatalog>("/api/management");
  }
  configuration(id: string) {
    return this.request<Configuration>(
      `/api/residents/${encodeURIComponent(id)}/configuration`,
    );
  }
  async maintainResident(change: MaintenanceChange) {
    const result = await this.request<{
      command_id: string;
      resident_id: string;
      revision?: number;
    }>(
      `/api/residents/${encodeURIComponent(change.resident_id)}/${change.kind}`,
      {
        method: "PUT",
        headers: { "Idempotency-Key": change.command_id },
        body: JSON.stringify(change.body),
      },
    );
    if (
      result.command_id !== change.command_id ||
      result.resident_id !== change.resident_id
    )
      throw new Error(
        "Resident operation is unconfirmed; retry the exact request.",
      );
    return result;
  }
  bootstrapManagement() {
    return this.request<{
      resident_id: string;
      skill_id: string;
      status: string;
    }>("/api/management/bootstrap", { method: "POST" });
  }
  saveManagement(id: string, change: ManagementChange) {
    return this.request<ManagementGrant>(
      `/api/residents/${encodeURIComponent(id)}/management`,
      { method: "PUT", body: JSON.stringify(change) },
    );
  }
  async state() {
    const state = decodeSnapshot(await this.request("/api/state"));
    this.readOnly = state.restore_hold === true;
    return state;
  }
  assignments(id: string) {
    return this.request<AssignmentSet>(
      `/api/residents/${encodeURIComponent(id)}/skills`,
    );
  }
  async saveAssignments(change: AssignmentChange) {
    const receipt = await this.request<{
      revision: number;
      command_id: string;
    }>(`/api/residents/${encodeURIComponent(change.resident_id)}/skills`, {
      method: "PUT",
      headers: { "Idempotency-Key": change.command_id },
      body: JSON.stringify({
        expected_revision: change.expected_revision,
        skills: change.skills,
      }),
    });
    if (
      receipt.command_id !== change.command_id ||
      !Number.isSafeInteger(receipt.revision)
    )
      throw new Error("Assignment receipt incomplete; retry to recover it.");
    return receipt;
  }
  skillUsers(id: string) {
    return this.request<SkillUser[]>(
      `/api/skills/${encodeURIComponent(id)}/assignments`,
    );
  }
  inputSets() {
    return this.request<SyntheticInput[]>("/api/input-sets");
  }
  inputSet(id: string, revision?: number) {
    return this.request<SyntheticInput>(
      `/api/input-sets/${encodeURIComponent(id)}${revision !== undefined ? `?revision=${revision}` : ""}`,
    );
  }
  async saveInput(change: InputChange) {
    const result = await this.request<{
      command_id: string;
      input_set_id: string;
      revision: number;
    }>(
      change.input_set_id
        ? `/api/input-sets/${encodeURIComponent(change.input_set_id)}`
        : "/api/input-sets",
      {
        method: change.input_set_id ? "PUT" : "POST",
        headers: { "Idempotency-Key": change.command_id },
        body: JSON.stringify({
          name: change.name,
          notes: change.notes,
          ...(change.input_set_id
            ? { expected_revision: change.expected_revision }
            : {}),
        }),
      },
    );
    if (
      result.command_id !== change.command_id ||
      typeof result.input_set_id !== "string" ||
      !Number.isSafeInteger(result.revision)
    )
      throw new Error("Input receipt is incomplete; retry the pending save.");
    return result;
  }
  inputSelection(residentId: string) {
    return this.request<InputSelectionSet>(
      `/api/residents/${encodeURIComponent(residentId)}/inputs`,
    );
  }
  async selectInputs(change: {
    resident_id: string;
    command_id: string;
    expected_revision: number;
    input_sets: { input_set_id: string }[];
  }) {
    const result = await this.request<{
      command_id: string;
      resident_id: string;
      revision: number;
    }>(`/api/residents/${encodeURIComponent(change.resident_id)}/inputs`, {
      method: "PUT",
      headers: { "Idempotency-Key": change.command_id },
      body: JSON.stringify({
        expected_revision: change.expected_revision,
        input_sets: change.input_sets,
      }),
    });
    if (
      result.command_id !== change.command_id ||
      result.resident_id !== change.resident_id ||
      !Number.isSafeInteger(result.revision)
    )
      throw new Error(
        "Input selection receipt is incomplete; retry the pending save.",
      );
    return result;
  }
  async provision(command_id: string, body: ProvisionRequest) {
    const receipt = await this.request<ProvisionReceipt>(
      "/api/residents/provision",
      {
        method: "POST",
        headers: { "Idempotency-Key": command_id },
        body: JSON.stringify(body),
      },
    );
    if (
      receipt.command_id !== command_id ||
      !["ready", "failed", "setup"].includes(receipt.status) ||
      typeof receipt.resident_id !== "string"
    )
      throw new Error(
        "Provisioning receipt is incomplete; retry the exact operation.",
      );
    return receipt;
  }
  exportResident(id: string) {
    return this.request<ResidentBundle>(
      `/api/residents/${encodeURIComponent(id)}/export`,
    );
  }
  async importResident(command_id: string, body: ImportRequest) {
    const receipt = await this.request<ImportReceipt>("/api/residents/import", {
      method: "POST",
      headers: { "Idempotency-Key": command_id },
      body: JSON.stringify(body),
    });
    if (
      receipt.command_id !== command_id ||
      !["ready", "failed", "setup"].includes(receipt.status) ||
      typeof receipt.resident_id !== "string"
    )
      throw new Error(
        "Import receipt is incomplete; retry the exact operation.",
      );
    return receipt;
  }
  skills(query = "", includeArchived = false) {
    return this.request<CatalogSkill[]>(
      `/api/skills?query=${encodeURIComponent(query)}&include_archived=${includeArchived}`,
    );
  }
  skill(id: string, revision?: number) {
    return this.request<CatalogSkill>(
      `/api/skills/${encodeURIComponent(id)}${revision ? `?revision=${revision}` : ""}`,
    );
  }
  skillHistory(id: string) {
    return this.request<CatalogSkill[]>(
      `/api/skills/${encodeURIComponent(id)}/history`,
    );
  }
  skillValidation(id: string) {
    return this.request<SkillValidation>(
      `/api/skill-validations/${encodeURIComponent(id)}`,
    );
  }
  validateSkill(
    skillId: string,
    revision: number,
    reserve: number,
    residentId: string,
  ) {
    return this.request<SkillValidation>(
      `/api/skills/${encodeURIComponent(skillId)}/validations`,
      {
        method: "POST",
        body: JSON.stringify({ revision, reserve, resident_id: residentId }),
      },
    );
  }
  publishSkill(change: {
    command_id: string;
    skill_id: string;
    expected_revision: number;
    validation_id: string;
  }) {
    return this.request<SkillReceipt>(
      `/api/skills/${encodeURIComponent(change.skill_id)}/publish`,
      {
        method: "POST",
        headers: { "Idempotency-Key": change.command_id },
        body: JSON.stringify({
          expected_revision: change.expected_revision,
          validation_id: change.validation_id,
        }),
      },
    );
  }
  async changeSkill(change: SkillChange) {
    const receipt = await this.request<SkillReceipt>(
      change.skill_id
        ? `/api/skills/${encodeURIComponent(change.skill_id)}${change.content ? "" : "/archive"}`
        : "/api/skills",
      {
        method: change.skill_id && change.content ? "PUT" : "POST",
        headers: { "Idempotency-Key": change.command_id },
        body: JSON.stringify({
          ...change.content,
          ...(change.skill_id
            ? { expected_revision: change.expected_revision }
            : {}),
        }),
      },
    );
    if (
      receipt.command_id !== change.command_id ||
      typeof receipt.skill_id !== "string" ||
      !Number.isSafeInteger(receipt.revision)
    )
      throw new Error(
        "The skill receipt is incomplete. Retry to reconcile it.",
      );
    return receipt;
  }
  resident(id: string) {
    return this.request<ResidentDeclaration>(
      `/api/residents/${encodeURIComponent(id)}`,
    );
  }
  memory(id: string) {
    return this.request<ResidentMemory>(
      `/api/residents/${encodeURIComponent(id)}/memory`,
    );
  }
  memoryHistory(id: string, limit = 20, offset = 0) {
    return this.request<MemoryHistory>(
      `/api/residents/${encodeURIComponent(id)}/memory/history?limit=${limit}&offset=${offset}`,
    );
  }
  memoryRevision(id: string, revision: number) {
    return this.request<ResidentMemory>(
      `/api/residents/${encodeURIComponent(id)}/memory?revision=${revision}`,
    );
  }
  journal(id: string, limit = 20, offset = 0) {
    return this.request<ResidentJournal>(
      `/api/residents/${encodeURIComponent(id)}/journal?limit=${limit}&offset=${offset}`,
    );
  }
  saveMemory(id: string, text: string, revision: number) {
    return this.request<ResidentMemory>(
      `/api/residents/${encodeURIComponent(id)}/memory`,
      {
        method: "PUT",
        body: JSON.stringify({ text, expected_revision: revision }),
      },
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
  markNotification(id: string, read: boolean) {
    return this.request<InboxNotification>(
      `/api/notifications/${encodeURIComponent(id)}/read`,
      { method: "POST", body: JSON.stringify({ read }) },
    );
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
