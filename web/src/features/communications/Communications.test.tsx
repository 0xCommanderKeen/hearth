// @vitest-environment jsdom
import { afterEach, expect, it, vi } from "vitest";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { Client, RequestError } from "../../shared/client";
import { Communications, DeliveryDetail } from "./Communications";
import { CommunicationSettings } from "./Settings";
import type { Delivery, Status } from "./api";
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});
const delivery: Delivery = {
  id: "op-1",
  kind: "announcement",
  state: "unknown",
  revision: 2,
  source_id: "source",
  run_id: "run-1",
  task_id: "task-1",
  created_at: 100,
  eligible_at: 110,
  parent_id: null,
  text: "Synthetic announcement",
  sha256: "digest",
  direction: "outbound",
  destination: {
    connection_id: "local",
    guild_id: "guild",
    channel_id: "channel",
  },
  routine_id: "daily",
  actions: ["sent", "not_sent", "abandon", "reissue"],
  read_only: false,
  run: { status: "succeeded", usage_known: 0, actual_cost: null },
  attempts: [
    {
      id: "attempt",
      state: "unknown",
      dispatched_at: 100,
      completed_at: null,
      external_id: null,
      evidence: null,
    },
  ],
  resolutions: [],
  resolution_count: 0,
};
const status: Status = {
  read_only: false,
  configuration: [
    {
      kind: "connection",
      id: "local",
      value: {
        revision: 1,
        label: "Synthetic connection",
        state: "pending",
        transport: "discord",
        bot_id: "bot",
        secret_ref: "synthetic_ref",
      },
    },
    {
      kind: "route",
      id: "route",
      value: {
        revision: 1,
        connection_id: "local",
        resident_id: "herald",
        address: { guild_id: "guild", channel_id: "channel" },
        label: "Synthetic channel",
        state: "pending",
        mode: "dedicated",
        sender_policy: "operators_only",
        operator_ids: [],
      },
    },
  ],
  next_after: null,
  bindings: [],
  schedule: [],
  health: { communications: "pending" },
  retention: "Transcript retention is separate from task and run evidence.",
};
it("shows three separate sections and bounded empty pages without probing", async () => {
  const client = new Client("synthetic");
  const request = vi
    .spyOn(client, "request")
    .mockResolvedValue({ items: [], next_after: null });
  render(
    <Communications client={client} readOnly={false} residentId="herald" />,
  );
  await screen.findByText("No conversations recorded.");
  expect(request.mock.calls[0][0]).toContain("limit=20&resident_id=herald");
  fireEvent.click(screen.getByRole("button", { name: "Announcements" }));
  await screen.findByText("No announcements recorded.");
  fireEvent.click(
    screen.getByRole("button", {
      name: "Notification deliveries",
    }),
  );
  await screen.findByText("No notification deliveries recorded.");
  expect(
    request.mock.calls.every(
      ([path, options]) => !path.includes("probe") && !options?.method,
    ),
  ).toBe(true);
  expect(screen.getByText(/Resident Letters remain/)).toBeTruthy();
});
it("keeps successful execution distinct from unknown delivery and links source run", async () => {
  const client = new Client("synthetic");
  vi.spyOn(client, "request").mockResolvedValue(delivery);
  render(<DeliveryDetail client={client} id="op-1" readOnly={false} />);
  await screen.findByText(/Run: succeeded/);
  expect(screen.getByText(/Usage unknown/)).toBeTruthy();
  expect(screen.getByText(/The external effect is unknown/)).toBeTruthy();
  expect(
    screen
      .getByRole("link", { name: "Open source run run-1" })
      .getAttribute("href"),
  ).toBe("#runs/run-1");
  expect(screen.queryByRole("button", { name: /retry/i })).toBeNull();
});
it("requires duplicate risk acknowledgement and sends the inspected revision", async () => {
  const client = new Client("synthetic");
  const request = vi
    .spyOn(client, "request")
    .mockImplementation(async (_path, options) =>
      options?.method ? { reissued_operation_id: "op-2" } : delivery,
    );
  render(<DeliveryDetail client={client} id="op-1" readOnly={false} />);
  await screen.findByLabelText("Resolution");
  fireEvent.change(screen.getByLabelText("Resolution"), {
    target: { value: "reissue" },
  });
  fireEvent.change(screen.getByLabelText("Reason"), {
    target: { value: "Operator accepts duplicate risk" },
  });
  expect(
    screen.getByRole("button", { name: "Record resolution" }),
  ).toHaveProperty("disabled", true);
  fireEvent.click(screen.getByLabelText(/I acknowledge/));
  fireEvent.click(screen.getByRole("button", { name: "Record resolution" }));
  await screen.findByText(/Linked operation: op-2/);
  const mutation = request.mock.calls.find(([, options]) => options?.method);
  expect(JSON.parse(String(mutation?.[1]?.body))).toEqual({
    expected_revision: 2,
    action: "reissue",
    reason: "Operator accepts duplicate risk",
    duplicate_risk_acknowledged: true,
  });
});
it("uses explicit late-conflict attempt evidence even after a terminal receipt", async () => {
  const client = new Client("synthetic");
  const late = {
    ...delivery,
    attempts: [{ ...delivery.attempts[0], state: "confirmed" }],
    resolutions: [
      {
        revision: 2,
        action: "late_receipt",
        at: 110,
        reason: "conflict",
        evidence: { attempt_id: "attempt" },
      },
    ],
  };
  const request = vi
    .spyOn(client, "request")
    .mockImplementation(async (_path, options) =>
      options?.method ? {} : late,
    );
  render(<DeliveryDetail client={client} id="op-1" readOnly={false} />);
  await screen.findByLabelText("Resolution");
  fireEvent.change(screen.getByLabelText("Resolution"), {
    target: { value: "not_sent" },
  });
  fireEvent.change(screen.getByLabelText("Reason"), {
    target: { value: "Verified absence" },
  });
  fireEvent.change(screen.getByLabelText("Evidence code"), {
    target: { value: "operator_verified_absence" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Record resolution" }));
  await screen.findByText("Resolution recorded.");
  const mutation = request.mock.calls.find(([, o]) => o?.method);
  expect(JSON.parse(String(mutation?.[1]?.body)).evidence).toEqual([
    {
      attempt_id: "attempt",
      intent_sha256: "digest",
      outcome: "safe_failure",
      evidence: "operator_verified_absence",
      external_id: null,
    },
  ]);
});
it("keeps conflict evidence visible and offers an explicit reload", async () => {
  const client = new Client("synthetic");
  vi.spyOn(client, "request").mockImplementation(async (_path, options) => {
    if (options?.method) throw new RequestError(409, "revision conflict");
    return delivery;
  });
  render(<DeliveryDetail client={client} id="op-1" readOnly={false} />);
  await screen.findByLabelText("Resolution");
  fireEvent.change(screen.getByLabelText("Resolution"), {
    target: { value: "abandon" },
  });
  fireEvent.change(screen.getByLabelText("Reason"), {
    target: { value: "End local work" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Record resolution" }));
  expect((await screen.findByRole("alert")).textContent).toContain(
    "Reload delivery",
  );
  expect(screen.getByLabelText("Reason")).toHaveProperty(
    "value",
    "End local work",
  );
});
it("held detail exposes evidence with no resolution control", async () => {
  const client = new Client("synthetic");
  const request = vi.spyOn(client, "request").mockResolvedValue(delivery);
  render(<DeliveryDetail client={client} id="op-1" readOnly />);
  await screen.findByText(/Run: succeeded/);
  expect(screen.queryByLabelText("Resolution")).toBeNull();
  expect(request).toHaveBeenCalledTimes(1);
});
const activeStatus: Status = {
  ...status,
  bindings: [{ connection_id: "local", revision: 1 }],
  health: { communications: "running" },
  configuration: [
    ...status.configuration.map((item) =>
      item.value
        ? { ...item, value: { ...item.value, state: "active" } }
        : item,
    ),
    {
      kind: "grant",
      id: "herald",
      value: {
        revision: 1,
        read: [
          { connection_id: "local", guild_id: "guild", channel_id: "channel" },
        ],
        listen: [],
        reply: [],
        post: [],
      },
    },
  ],
};
it("settings reads cached health and requires explicit activation and probe", async () => {
  const client = new Client("synthetic");
  const request = vi
    .spyOn(client, "request")
    .mockImplementation(async (path) =>
      path.includes("forwarding")
        ? { items: [], next_after: null }
        : activeStatus,
    );
  render(<CommunicationSettings client={client} readOnly={false} />);
  await screen.findByText(/connection · Synthetic connection/);
  expect(request.mock.calls).toHaveLength(2);
  expect(screen.getByRole("button", { name: "Activate local" })).toHaveProperty(
    "disabled",
    true,
  );
  fireEvent.click(screen.getByLabelText(/I have stopped/));
  fireEvent.click(screen.getByRole("button", { name: "Activate local" }));
  await waitFor(() =>
    expect(
      request.mock.calls.some(([path]) => path.endsWith("/activate")),
    ).toBe(true),
  );
  await screen.findByText(/connection · Synthetic connection/);
  fireEvent.change(screen.getByLabelText("Probe route for local"), {
    target: { value: "route" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Probe local" }));
  await waitFor(() =>
    expect(request.mock.calls.some(([path]) => path.endsWith("/probe"))).toBe(
      true,
    ),
  );
});
it("held settings disable all writes including probes and activation", async () => {
  const client = new Client("synthetic");
  vi.spyOn(client, "request").mockImplementation(async (path) =>
    path.includes("forwarding") ? { items: [], next_after: null } : status,
  );
  render(<CommunicationSettings client={client} readOnly />);
  await screen.findByText(/connection · Synthetic connection/);
  for (const label of [
    "Activate local",
    "Probe local",
    "New connection",
    "Edit route route",
    "New forwarding binding",
  ]) {
    expect(screen.getByRole("button", { name: label })).toHaveProperty(
      "disabled",
      true,
    );
  }
});
it("omits oversized legacy configurations without losing the settings page", async () => {
  const client = new Client("synthetic");
  vi.spyOn(client, "request").mockImplementation(async (path) =>
    path.includes("forwarding")
      ? { items: [], next_after: null }
      : {
          ...status,
          configuration: [
            ...status.configuration,
            {
              kind: "grant",
              id: "legacy",
              value: null,
              revision: 1,
              omitted: "configuration_too_large",
            },
          ],
        },
  );
  render(<CommunicationSettings client={client} readOnly={false} />);
  expect((await screen.findByRole("alert")).textContent).toContain(
    "legacy configuration is too large",
  );
  expect(
    screen.queryByRole("button", { name: "Edit grant legacy" }),
  ).toBeNull();
  expect(
    screen.getByRole("button", { name: "Edit connection local" }),
  ).toBeTruthy();
});
it("accepts distinct confirmed and not-sent evidence for separate uncertain attempts", async () => {
  const client = new Client("synthetic");
  const request = vi
    .spyOn(client, "request")
    .mockImplementation(async (_path, options) =>
      options?.method
        ? {}
        : {
            ...delivery,
            attempts: [
              delivery.attempts[0],
              { ...delivery.attempts[0], id: "attempt-2" },
            ],
          },
    );
  render(<DeliveryDetail client={client} id="op-1" readOnly={false} />);
  await screen.findByLabelText("Resolution");
  fireEvent.change(screen.getByLabelText("Resolution"), {
    target: { value: "sent" },
  });
  fireEvent.change(screen.getByLabelText("Reason"), {
    target: { value: "Reviewed both attempts" },
  });
  fireEvent.change(screen.getAllByLabelText("Attempt outcome")[1], {
    target: { value: "safe_failure" },
  });
  fireEvent.change(screen.getAllByLabelText("Evidence code")[0], {
    target: { value: "operator_receipt" },
  });
  fireEvent.change(screen.getAllByLabelText("Evidence code")[1], {
    target: { value: "operator_absence" },
  });
  fireEvent.change(screen.getByLabelText("External receipt ID"), {
    target: { value: "external-1" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Record resolution" }));
  await screen.findByText("Resolution recorded.");
  const mutation = request.mock.calls.find(([, o]) => o?.method);
  expect(
    JSON.parse(String(mutation?.[1]?.body)).evidence.map(
      (e: { outcome: string }) => e.outcome,
    ),
  ).toEqual(["confirmed", "safe_failure"]);
});
it("uses the owner's exact uncertain attempt IDs when old conflicts leave the history window", async () => {
  const client = new Client("synthetic");
  vi.spyOn(client, "request").mockResolvedValue({
    ...delivery,
    uncertain_attempt_ids: ["attempt"],
    attempts: [{ ...delivery.attempts[0], state: "confirmed" }],
    resolutions: [
      {
        revision: 2,
        action: "reissue",
        at: 110,
        reason: "New linked operation",
        evidence: null,
      },
    ],
  });
  render(<DeliveryDetail client={client} id="op-1" readOnly={false} />);
  await screen.findByLabelText("Resolution");
  fireEvent.change(screen.getByLabelText("Resolution"), {
    target: { value: "not_sent" },
  });
  expect(screen.getByText("Evidence for attempt attempt")).toBeTruthy();
});

it.each(["pending", "disabled"])(
  "disables activation and probing for a %s connection",
  async (connectionState) => {
    const client = new Client("synthetic");
    const request = vi
      .spyOn(client, "request")
      .mockImplementation(async (path) =>
        path.includes("forwarding")
          ? { items: [], next_after: null }
          : {
              ...activeStatus,
              configuration: activeStatus.configuration.map((item) =>
                item.kind === "connection" && item.value
                  ? {
                      ...item,
                      value: { ...item.value, state: connectionState },
                    }
                  : item,
              ),
            },
      );
    render(<CommunicationSettings client={client} readOnly={false} />);
    await screen.findByText(/connection · Synthetic connection/);
    fireEvent.click(screen.getByLabelText(/I have stopped/));
    expect(
      screen.getByRole("button", { name: "Activate local" }),
    ).toHaveProperty("disabled", true);
    expect(screen.getByRole("button", { name: "Probe local" })).toHaveProperty(
      "disabled",
      true,
    );
    expect(screen.getByLabelText("Probe route for local")).toHaveProperty(
      "disabled",
      true,
    );
    expect(request.mock.calls.every(([, options]) => !options?.method)).toBe(
      true,
    );
  },
);
it("disables probing with a stopped worker and omits inactive or ungranted routes", async () => {
  const client = new Client("synthetic");
  vi.spyOn(client, "request").mockImplementation(async (path) =>
    path.includes("forwarding")
      ? { items: [], next_after: null }
      : {
          ...activeStatus,
          health: { communications: "stopped" },
          configuration: activeStatus.configuration.map((item) =>
            item.kind === "route" && item.value
              ? { ...item, value: { ...item.value, state: "disabled" } }
              : item,
          ),
        },
  );
  render(<CommunicationSettings client={client} readOnly={false} />);
  await screen.findByText(/connection · Synthetic connection/);
  expect(screen.getByRole("button", { name: "Probe local" })).toHaveProperty(
    "disabled",
    true,
  );
  expect(
    within(screen.getByLabelText("Probe route for local")).queryByRole(
      "option",
      { name: "Synthetic channel" },
    ),
  ).toBeNull();
  expect(
    screen.getByText("Probing requires a running communications worker."),
  ).toBeTruthy();
});
it("shows active usage and reserved estimates alongside finished unknown costs", async () => {
  const client = new Client("synthetic");
  vi.spyOn(client, "request").mockImplementation(async (path) =>
    path.includes("/usage")
      ? {
          origins: [
            {
              root_task_id: "active-task",
              origin: "conversation",
              known_cost: 0,
              unknown_runs: 0,
              runs: 1,
              active_runs: 1,
              reserved: 10000,
            },
          ],
          truncated: false,
        }
      : { items: [], next_after: null },
  );
  render(<Communications client={client} readOnly={false} />);
  fireEvent.click(screen.getByRole("button", { name: "Usage" }));
  expect((await screen.findByText(/active-task/)).textContent).toContain(
    "1 active · $0.0100 reserved estimate",
  );
});
