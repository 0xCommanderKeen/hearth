// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { App } from "./App";
import { Client, RequestError, type Snapshot } from "./client";

let state: Snapshot;
let publish: (snapshot: Snapshot) => void;

beforeEach(() => {
  window.history.replaceState(null, "", "/");
  state = {
    schema_version: 1,
    simulated: true,
    epoch: "demo",
    cursor: 0,
    residents: [],
    tasks: [],
    runs: [],
    activity: [],
  };
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

async function login() {
  render(<App />);
  fireEvent.change(screen.getByLabelText("Operator token"), {
    target: { value: "synthetic-operator-token-for-tests" },
  });
  fireEvent.click(screen.getByRole("button", { name: /Enter Hearth/ }));
  await screen.findByText("Connected to the simulation");
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

it("opens the gate, seeds the reader, and switches views without separate state", async () => {
  const seed = vi
    .spyOn(Client.prototype, "seed")
    .mockImplementation(async () => {
      addReader();
      return {};
    });
  await login();
  expect(screen.queryByLabelText("Operator token")).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: /Set up mock Reader/ }));
  await screen.findByLabelText("The assignment");
  expect(seed).toHaveBeenCalledTimes(1);
  expect(screen.getByRole("img", { name: /Reader's home/ })).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "Townhall" }));
  expect(
    screen
      .getByRole("button", { name: "Townhall" })
      .getAttribute("aria-pressed"),
  ).toBe("true");
  expect(screen.queryByRole("img", { name: /Reader's home/ })).toBeNull();
  expect(screen.getByLabelText("The assignment")).toBeTruthy();
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
  fireEvent.click(screen.getByRole("button", { name: /Run a mock summary/ }));
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

it("displays simulated output and clears it when the operator locks the session", async () => {
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
  await screen.findByLabelText("Summary output");
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
  fireEvent.click(screen.getByRole("button", { name: /Run a mock summary/ }));
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
  await screen.findByText("Connected to the simulation");
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
  expect(screen.getByRole("img").getAttribute("aria-label")).toContain(
    "Outcome unknown",
  );
});

it("shows mock delivery uncertainty and opens Townhall without deciding", async () => {
  addReader();
  state.notifications = [
    {
      id: "delivery",
      kind: "approval.requested",
      resource_id: "review",
      status: "retry",
      attempts: 2,
      next_at: 2_000_000_000,
      reason: "mock_delivery_unconfirmed",
      payload: { simulated: true, link: "/#approval-review" },
    },
  ];
  const decide = vi.spyOn(Client.prototype, "decide");
  await login();
  expect(
    screen.getByText(/Delivery unconfirmed; retry scheduled/),
  ).toBeTruthy();
  const link = screen.getByRole("link", { name: "Open approval review" });
  expect(link.getAttribute("href")).toBe("/#approval-review");
  fireEvent.click(link);
  await screen.findByRole("region", { name: "Mock approvals" });
  expect(decide).not.toHaveBeenCalled();
});

it("opens a linked result outside the recent task list after authentication", async () => {
  window.history.replaceState(null, "", "/#run-older");
  vi.spyOn(Client.prototype, "run").mockResolvedValue({
    id: "older",
    status: "succeeded",
    artifact_id: "older-output",
  });
  vi.spyOn(Client.prototype, "artifact").mockResolvedValue({
    content: "Older linked synthetic result",
  });
  await login();
  await screen.findByText("Older linked synthetic result");
});
