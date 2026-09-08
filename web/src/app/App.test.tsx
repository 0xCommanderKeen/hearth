// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { App } from "./App";
import { Client, RequestError, type Snapshot } from "../shared/client";

let state: Snapshot;
let publish: (snapshot: Snapshot) => void;

beforeEach(() => {
  sessionStorage.clear();
  window.history.replaceState(null, "", "/");
  state = {
    schema_version: 1,
    epoch: "demo",
    cursor: 0,
    residents: [],
    tasks: [],
    runs: [],
    activity: [],
  };
  vi.spyOn(Client.prototype, "configuration").mockImplementation(
    async (id) => ({
      resident_id: id,
      lifecycle: state.residents.find((r) => r.id === id)?.lifecycle ?? {
        resident_id: id,
        state: "ready",
        revision: 0,
        manager: "operator",
      },
      execution_profile: "codex_subscription",
      declaration: {
        expected_revision: 1,
        name: id,
        purpose: "Synthetic notes",
        instructions: "Read",
        daily_limit: 100000,
        budget_timezone: "UTC",
      },
      memory: { expected_revision: 0, text: "" },
      inputs: { expected_revision: 0, input_sets: [] },
      skills: { expected_revision: 0, skills: [] },
      routines: [],
    }),
  );
  vi.spyOn(Client.prototype, "request").mockResolvedValue({
    execution_profiles: [],
    input_sets: [],
    managers: [{ id: "operator", name: "Operator" }],
  });
  vi.spyOn(Client.prototype, "inputSets").mockResolvedValue([]);
  vi.spyOn(Client.prototype, "inputSelection").mockImplementation(
    async (resident_id) => ({ resident_id, revision: 0, input_sets: [] }),
  );
  vi.spyOn(Client.prototype, "skills").mockResolvedValue([]);
  vi.spyOn(Client.prototype, "assignments").mockImplementation(
    async (resident_id) => ({
      resident_id,
      revision: 0,
      sha256: "empty-fixture",
      skills: [],
    }),
  );
  vi.spyOn(Client.prototype, "state").mockImplementation(async () =>
    structuredClone(state),
  );
  vi.spyOn(Client.prototype, "watch").mockImplementation(
    async (signal, onState, onConnection) => {
      publish = onState;
      onConnection(true);
      await new Promise<void>((resolve) =>
        signal.addEventListener("abort", () => resolve(), { once: true }),
      );
    },
  );
});
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

async function login(profile = true) {
  render(<App />);
  fireEvent.change(screen.getByLabelText("Operator token"), {
    target: { value: "synthetic-operator-token-for-tests" },
  });
  fireEvent.click(screen.getByRole("button", { name: /Enter Hearth/ }));
  await screen.findByText("Connected to Codex");
  if (profile && state.residents.length && !window.location.hash) {
    fireEvent.click(screen.getByRole("link", { name: /View resident/ }));
    await screen.findByLabelText("Resident information");
  }
}

function addReader() {
  state.residents = [
    {
      id: "reader",
      name: "Reader",
      purpose: "Synthetic notes",
      revision: 1,
      daily_limit: 1_000_000,
      presence: "ready",
      pause_reason: null,
    },
  ];
}

it("opens the gate and switches views without separate state", async () => {
  addReader();
  await login(false);
  expect(screen.queryByLabelText("Operator token")).toBeNull();
  await screen.findByRole("link", { name: /View resident/ });
  fireEvent.click(screen.getByRole("link", { name: /Residents$/ }));
  await waitFor(() =>
    expect(screen.getByRole("heading", { level: 1 }).textContent).toBe(
      "Residents",
    ),
  );
  fireEvent.click(screen.getByRole("link", { name: /View resident/ }));
  await screen.findByLabelText("The assignment");
  expect(screen.getByLabelText("Resident information").textContent).toContain(
    "Synthetic notes",
  );
  fireEvent.click(screen.getByRole("link", { name: /Hamlet$/ }));
  await screen.findByRole("img", { name: /Reader's home/ });
  expect(screen.queryByLabelText("The assignment")).toBeNull();
});

it("freezes a pending command and retries the same request after a lost response", async () => {
  addReader();
  const submit = vi
    .spyOn(Client.prototype, "submit")
    .mockRejectedValueOnce(new TypeError("network failed"))
    .mockResolvedValueOnce({
      command_id: "ignored-by-component",
      task_id: "task",
    });
  const start = vi.spyOn(Client.prototype, "start").mockResolvedValue({});
  await login();
  fireEvent.click(screen.getByRole("button", { name: /Run summary/ }));
  await screen.findByRole("alert");
  expect(
    (screen.getByLabelText("The assignment") as HTMLTextAreaElement).disabled,
  ).toBe(true);
  fireEvent.click(
    screen.getByRole("button", { name: /Retry pending submission/ }),
  );
  await waitFor(() => expect(start).toHaveBeenCalledWith("task"));
  expect(submit.mock.calls[0][0]).toEqual(submit.mock.calls[1][0]);
  expect(
    (screen.getByLabelText("The assignment") as HTMLTextAreaElement).disabled,
  ).toBe(false);
});

it("shows cancellation intent until a later snapshot confirms termination", async () => {
  addReader();
  state.tasks = [
    {
      id: "task",
      resident_id: "reader",
      instruction: "Held synthetic task",
      status: "running",
      created_at: 1,
    },
  ];
  state.runs = [
    {
      id: "run",
      task_id: "task",
      resident_id: "reader",
      status: "running",
      artifact_id: null,
      actual_cost: null,
      usage_known: 0,
      cancellation_requested: 0,
    },
  ];
  vi.spyOn(Client.prototype, "cancel").mockImplementation(async () => {
    state.tasks[0].status = state.runs[0].status = "stopping";
    state.runs[0].cancellation_requested = 1;
    return {};
  });
  await login();
  fireEvent.click(screen.getByRole("button", { name: "Cancel run" }));
  await screen.findByText("Stopping");
  expect(screen.queryByText("Cancelled")).toBeNull();
  state.tasks[0].status = state.runs[0].status = "cancelled";
  await act(async () => publish(structuredClone(state)));
  expect(screen.getByText("Cancelled")).toBeTruthy();
});

it("displays run output and clears it when the operator locks the session", async () => {
  addReader();
  state.tasks = [
    {
      id: "task",
      resident_id: "reader",
      instruction: "A summary",
      status: "succeeded",
      created_at: 1,
    },
  ];
  state.runs = [
    {
      id: "run",
      task_id: "task",
      resident_id: "reader",
      status: "succeeded",
      artifact_id: "artifact",
      actual_cost: 2000,
      usage_known: 1,
      cancellation_requested: 0,
    },
  ];
  vi.spyOn(Client.prototype, "artifact").mockResolvedValue({
    content: "# Synthetic summary\nNo model was called.",
  });
  await login();
  fireEvent.click(screen.getByRole("button", { name: /Read summary/ }));
  const summaryPanel = await screen.findByLabelText("Summary output");
  expect(document.activeElement).toBe(summaryPanel);
  expect(screen.getByText(/No model was called/)).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "Lock" }));
  expect(screen.getByLabelText("Operator token")).toBeTruthy();
  expect(screen.queryByLabelText("Summary output")).toBeNull();
});

it("does not leave a rejected credential signed in", async () => {
  addReader();
  vi.spyOn(Client.prototype, "submit").mockRejectedValue(
    new RequestError(401, "unauthorized"),
  );
  await login();
  fireEvent.click(screen.getByRole("button", { name: /Run summary/ }));
  await screen.findByLabelText("Operator token");
  expect(screen.queryByRole("button", { name: "Lock" })).toBeNull();
});

it("does not restore an artifact response that arrives after locking", async () => {
  addReader();
  state.tasks = [
    {
      id: "task",
      resident_id: "reader",
      instruction: "A summary",
      status: "succeeded",
      created_at: 1,
    },
  ];
  state.runs = [
    {
      id: "run",
      task_id: "task",
      resident_id: "reader",
      status: "succeeded",
      artifact_id: "artifact",
      actual_cost: 2000,
      usage_known: 1,
      cancellation_requested: 0,
    },
  ];
  let complete: (value: { content: string }) => void;
  vi.spyOn(Client.prototype, "artifact").mockImplementation(
    () =>
      new Promise((resolve) => {
        complete = resolve;
      }),
  );
  await login();
  fireEvent.click(screen.getByRole("button", { name: /Read summary/ }));
  fireEvent.click(screen.getByRole("button", { name: "Lock" }));
  await act(async () => complete({ content: "late private output" }));
  fireEvent.change(screen.getByLabelText("Operator token"), {
    target: { value: "synthetic-second-token" },
  });
  fireEvent.click(screen.getByRole("button", { name: /Enter Hearth/ }));
  await screen.findByText("Connected to Codex");
  expect(screen.queryByText("late private output")).toBeNull();
});

it("keeps a newer streamed snapshot when an older snapshot arrives later", async () => {
  addReader();
  await login();
  const newer = structuredClone(state);
  newer.cursor = 5;
  newer.residents[0].presence = "interrupted";
  await act(async () => publish(newer));
  await act(async () => publish(structuredClone(state)));
  expect(screen.getByLabelText("Resident information").textContent).toContain(
    "Outcome unknown",
  );
});

it("keeps a run notification in the inbox until the operator marks it read", async () => {
  addReader();
  state.notifications = [
    {
      id: "notice",
      kind: "run.succeeded",
      resource_id: "older",
      created_at: 1_788_640_000,
      read_at: null,
      payload: { link: "/#run-older" },
    },
  ];
  const mark = vi
    .spyOn(Client.prototype, "markNotification")
    .mockResolvedValue({ ...state.notifications[0], read_at: 1_788_640_060 });
  await login(false);
  fireEvent.click(screen.getByRole("link", { name: /^Inbox/ }));
  const inbox = await screen.findByRole("region", { name: "Inbox" });
  expect(inbox.textContent).toContain("Run succeeded");
  // Each row names the run it acts on, so the repeated controls stay distinguishable.
  expect(
    within(inbox)
      .getByRole("link", { name: "Open the run older and its result" })
      .getAttribute("href"),
  ).toBe("/#run-older");
  fireEvent.click(
    within(inbox).getByRole("button", {
      name: "Mark the run succeeded notice for older read",
    }),
  );
  await waitFor(() => expect(mark).toHaveBeenCalledWith("notice", true));
});

it.each(["/#run-older", "/#runs/older"])(
  "opens %s outside the recent task list after authentication",
  async (link) => {
    window.history.replaceState(null, "", link);
    const readRun = vi.spyOn(Client.prototype, "run").mockResolvedValue({
      id: "older",
      status: "succeeded",
      artifact_id: "older-output",
    });
    vi.spyOn(Client.prototype, "artifact").mockResolvedValue({
      content: "Older linked synthetic result",
    });
    await login();
    await screen.findByText("Older linked synthetic result");
    expect(readRun).toHaveBeenCalledWith("older");
  },
);

it("pauses new runs using the displayed operator revision", async () => {
  addReader();
  state.residents[0].lifecycle = {
    resident_id: "reader",
    state: "ready",
    revision: 3,
    manager: "operator",
  };
  state.residents[0].operator_paused = 0;
  const pause = vi
    .spyOn(Client.prototype, "maintainResident")
    .mockResolvedValue({ command_id: "pause", resident_id: "reader" });
  await login();
  fireEvent.click(screen.getByText("Pause new runs"));
  await waitFor(() =>
    expect(pause).toHaveBeenCalledWith(
      expect.objectContaining({
        resident_id: "reader",
        kind: "lifecycle",
        body: { expected_revision: 3, state: "paused" },
      }),
    ),
  );
  expect(screen.getByText(/Existing work continues/)).toBeTruthy();
});

it("lists every resident and scopes profile work to the selected resident", async () => {
  addReader();
  state.residents.push({
    ...state.residents[0],
    id: "gardener",
    name: "Gardener",
    purpose: "Plan a synthetic garden",
  });
  state.tasks.push({
    id: "reader-task",
    resident_id: "reader",
    instruction: "Reader-only assignment",
    status: "queued",
    created_at: 1,
  });
  const submit = vi
    .spyOn(Client.prototype, "submit")
    .mockResolvedValue({ command_id: "receipt", task_id: "garden-task" });
  vi.spyOn(Client.prototype, "start").mockResolvedValue({});
  await login(false);
  expect(screen.getAllByRole("link", { name: /View resident/ })).toHaveLength(
    2,
  );
  fireEvent.click(
    screen.getByRole("link", { name: /Gardener.*View resident/ }),
  );
  await screen.findByRole("heading", { level: 1, name: "Gardener" });
  expect(screen.getByLabelText("Resident information").textContent).toContain(
    "Plan a synthetic garden",
  );
  expect(screen.queryByText("Reader-only assignment")).toBeNull();
  expect(screen.getByText("A quiet beginning.")).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: /Run summary/ }));
  await waitFor(() => expect(submit).toHaveBeenCalled());
  expect(submit.mock.calls[0][0].body.resident_id).toBe("gardener");
});

it("keeps an ambiguous submission attached to its original resident", async () => {
  addReader();
  state.residents.push({
    ...state.residents[0],
    id: "gardener",
    name: "Gardener",
  });
  const submit = vi
    .spyOn(Client.prototype, "submit")
    .mockRejectedValue(new TypeError("lost response"));
  await login(false);
  fireEvent.click(screen.getByRole("link", { name: /Reader.*View resident/ }));
  await screen.findByLabelText("The assignment");
  fireEvent.click(screen.getByRole("button", { name: /Run summary/ }));
  await screen.findByRole("alert");
  fireEvent.click(screen.getByRole("link", { name: /All residents/ }));
  await screen.findByRole("heading", { level: 1, name: "Residents" });
  fireEvent.click(
    screen.getByRole("link", { name: /Gardener.*View resident/ }),
  );
  await screen.findByRole("heading", { level: 1, name: "Gardener" });
  expect(screen.getByText(/Pending submission belongs to reader/)).toBeTruthy();
  expect(
    (
      screen.getByRole("button", {
        name: /Retry pending submission/,
      }) as HTMLButtonElement
    ).disabled,
  ).toBe(true);
  expect(submit).toHaveBeenCalledTimes(1);
});

it("does not display another resident's late artifact response on a profile", async () => {
  addReader();
  state.residents.push({
    ...state.residents[0],
    id: "gardener",
    name: "Gardener",
  });
  state.tasks.push({
    id: "task",
    resident_id: "reader",
    instruction: "Reader summary",
    status: "succeeded",
    created_at: 1,
  });
  state.runs.push({
    id: "run",
    task_id: "task",
    resident_id: "reader",
    status: "succeeded",
    artifact_id: "artifact",
    actual_cost: 1,
    usage_known: 1,
    cancellation_requested: 0,
  });
  let complete!: (result: { content: string }) => void;
  vi.spyOn(Client.prototype, "artifact").mockImplementation(
    () =>
      new Promise((resolve) => {
        complete = resolve;
      }),
  );
  await login(false);
  fireEvent.click(screen.getByRole("link", { name: /Reader.*View resident/ }));
  await screen.findByLabelText("Resident information");
  fireEvent.click(screen.getByRole("button", { name: /Read summary/ }));
  fireEvent.click(screen.getByRole("link", { name: /All residents/ }));
  await screen.findByRole("heading", { level: 1, name: "Residents" });
  fireEvent.click(
    screen.getByRole("link", { name: /Gardener.*View resident/ }),
  );
  await screen.findByRole("heading", { level: 1, name: "Gardener" });
  await act(async () => complete({ content: "Reader private result" }));
  expect(screen.queryByLabelText("Summary output")).toBeNull();
  fireEvent.click(screen.getByRole("link", { name: /All residents/ }));
  await screen.findByRole("heading", { level: 1, name: "Residents" });
  fireEvent.click(screen.getByRole("link", { name: /Reader.*View resident/ }));
  await screen.findByText("Reader private result");
});

it("keeps an authenticated tab unlocked after refresh and clears it on Lock", async () => {
  await login(false);
  cleanup();
  render(<App />);
  await screen.findByText("Connected to Codex");
  expect(screen.queryByLabelText("Operator token")).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Lock" }));
  cleanup();
  render(<App />);
  expect(screen.getByLabelText("Operator token")).toBeTruthy();
  expect(sessionStorage.length).toBe(0);
});

it("forgets a saved token when the server rejects it after refresh", async () => {
  await login(false);
  cleanup();
  vi.mocked(Client.prototype.state).mockRejectedValue(
    new RequestError(401, "unauthorized"),
  );
  render(<App />);
  await screen.findByRole("alert");
  expect(screen.getByLabelText("Operator token")).toBeTruthy();
  expect(sessionStorage.length).toBe(0);
});

it("reports damaged historical skill provenance without inventing an execution hold", async () => {
  window.location.hash = "#tasks";
  addReader();
  state.tasks = [
    {
      id: "task",
      resident_id: "reader",
      instruction: "Completed summary",
      status: "succeeded",
      created_at: 1,
    },
  ];
  state.runs = [
    {
      id: "run",
      task_id: "task",
      resident_id: "reader",
      status: "succeeded",
      artifact_id: "result",
      actual_cost: 2000,
      usage_known: 1,
      cancellation_requested: 0,
      skills_error: "skill_content_changed",
    },
  ];
  await login(false);
  expect(screen.getByText("Completed")).toBeTruthy();
  expect(screen.getByRole("button", { name: /Read summary/ })).toBeTruthy();
  expect(screen.queryByText(/Execution is held/)).toBeNull();
  expect(screen.getByText(/Skill provenance unavailable/)).toBeTruthy();
});

it.each(["navigation", "lock", "epoch"])(
  "ignores stale provisioning navigation after %s",
  async (mode) => {
    vi.spyOn(Client.prototype, "request").mockResolvedValue({
      execution_profiles: [
        { id: "codex_subscription", name: "Configured Codex subscription" },
      ],
      input_sets: [],
      managers: [{ id: "operator", name: "Operator" }],
    });
    vi.spyOn(Client.prototype, "skills").mockResolvedValue([]);
    vi.spyOn(Client.prototype, "provision").mockImplementation(
      async (id, body) => ({
        command_id: id,
        resident_id: "created",
        status: "ready",
        reason: null,
        creator: "operator",
        manager: "operator",
        originating_run_id: null,
        created_at: 1,
        routine_id: null,
        task_id: null,
        setup: body,
      }),
    );
    await login(false);
    fireEvent.click(screen.getByRole("link", { name: "New resident ＋" }));
    await screen.findByLabelText("Resident name");
    fireEvent.change(screen.getByLabelText("Resident name"), {
      target: { value: "Reporter" },
    });
    fireEvent.change(screen.getByLabelText("Purpose"), {
      target: { value: "Notes" },
    });
    fireEvent.change(screen.getByLabelText("Creation reason"), {
      target: { value: "Synthetic" },
    });
    let release!: (value: Snapshot) => void;
    vi.mocked(Client.prototype.state).mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          release = resolve;
        }),
    );
    fireEvent.click(screen.getByText("Create resident", { exact: true }));
    await waitFor(() => expect(release).toBeDefined());
    if (mode === "navigation") {
      fireEvent.click(screen.getByRole("link", { name: /Skills$/ }));
      await waitFor(() => expect(window.location.hash).toBe("#skills"));
    } else if (mode === "lock")
      fireEvent.click(screen.getByRole("button", { name: "Lock" }));
    const expectedHash = window.location.hash;
    await act(async () =>
      release({
        ...structuredClone(state),
        epoch: mode === "epoch" ? "new-store" : state.epoch,
      }),
    );
    expect(window.location.hash).toBe(expectedHash);
    if (mode === "lock")
      expect(screen.getByLabelText("Operator token")).toBeTruthy();
  },
);

function addRun(overrides: Record<string, unknown>) {
  state.tasks = [
    {
      id: "task",
      resident_id: "reader",
      instruction: "Write today's report",
      status: "succeeded",
      created_at: 1,
    },
  ];
  state.runs = [
    {
      id: "run-two",
      task_id: "task",
      resident_id: "reader",
      status: "succeeded",
      artifact_id: null,
      actual_cost: 700,
      usage_known: 1,
      cancellation_requested: 0,
      ...overrides,
    },
  ];
}

it("says what a run opened with and what it wrote, without hiding the page", async () => {
  addReader();
  addRun({
    memory_revision: 2,
    memory_written: [],
    journal_opened: [1],
    journal_written: 2,
  });
  await login();
  expect(
    screen.getByText(/Memory revision 2 . opened with journal #1/),
  ).toBeTruthy();
  expect(screen.getByLabelText("What the run wrote").textContent).toContain(
    "Wrote no memory",
  );
  expect(screen.getByLabelText("What the run wrote").textContent).toContain(
    "wrote journal entry #2",
  );
  // The panels sit on the page beside the work, not behind a tab.
  expect(screen.getByText(/Reader . Memory history/)).toBeTruthy();
  expect(screen.getByText(/Reader . Journal/)).toBeTruthy();
  expect(screen.getByRole("link", { name: /All residents/ })).toBeTruthy();
  expect(screen.getByText("Pause new runs")).toBeTruthy();
});

it("says plainly when a run opened with no journal and wrote memory", async () => {
  addReader();
  addRun({
    memory_revision: 1,
    memory_written: [2],
    journal_opened: [],
    journal_written: null,
  });
  await login();
  expect(
    screen.getByText(/Memory revision 1 . opened with no journal entries/),
  ).toBeTruthy();
  const wrote = screen.getByLabelText("What the run wrote").textContent;
  expect(wrote).toContain("Wrote memory revision 2");
  expect(wrote).toContain("wrote no journal entry");
});
