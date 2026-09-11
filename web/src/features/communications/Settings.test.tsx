// @vitest-environment jsdom
import { afterEach, expect, it, vi } from "vitest";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  within,
} from "@testing-library/react";
import { Client } from "../../shared/client";
import { CommunicationSettings } from "./Settings";
import type { Status, Forwarding, Config } from "./api";
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});
function mount(configuration: Config[] = []) {
  const state: Status = {
    read_only: false,
    configuration,
    next_after: null,
    bindings: [],
    schedule: [],
    health: { communications: "stopped" },
    retention: "Bounded transcripts.",
  };
  const forwards: Forwarding[] = [];
  const writes: { path: string; body: Record<string, unknown> }[] = [];
  const client = new Client("synthetic");
  vi.spyOn(client, "request").mockImplementation(async (path, options) => {
    if (options?.method) {
      const body = JSON.parse(String(options.body));
      writes.push({ path, body });
      const id = path.split("/").at(-1)!;
      if (path.includes("/configuration/")) {
        const kind = path.split("/").at(-2)!;
        state.configuration = state.configuration.filter(
          (item) => !(item.kind === kind && item.id === id),
        );
        state.configuration.push({
          kind,
          id,
          value: { ...body.value, revision: body.expected_revision + 1 },
        });
      } else {
        const old = forwards.findIndex((item) => item.id === id);
        if (old >= 0) forwards.splice(old, 1);
        forwards.push({ id, ...body, revision: body.expected_revision + 1 });
      }
      return { id, revision: body.expected_revision + 1 };
    }
    return path.includes("/forwarding")
      ? { items: [...forwards], next_after: null }
      : { ...state, configuration: [...state.configuration] };
  });
  render(<CommunicationSettings client={client} readOnly={false} />);
  return { writes, state };
}
const fill = (name: string, value: string) =>
  fireEvent.change(screen.getByLabelText(name), { target: { value } });
const select = fill;
async function newForm(kind: string) {
  await screen.findByRole("button", { name: `New ${kind}` });
  fireEvent.click(screen.getByRole("button", { name: `New ${kind}` }));
}
async function save(kind: string, id: string) {
  fireEvent.click(screen.getByRole("button", { name: `Save ${kind}` }));
  await screen.findByRole("button", { name: `Edit ${kind} ${id}` });
}
it.each(["discord", "telegram", "ntfy"])(
  "creates, reloads and updates a %s connection with immutable identity",
  async (transport) => {
    const { writes } = mount();
    await newForm("connection");
    fill("Stable local ID", "connection");
    fill("Display label", "Synthetic");
    select("Transport", transport);
    fill("Protected credential reference (never a token)", "synthetic_slot");
    const identity = screen.getByLabelText(
      "Verified external identity (empty for notification-only transports)",
    );
    if (transport === "ntfy") {
      expect(identity).toHaveProperty("disabled", true);
    } else {
      expect(identity).toHaveProperty("required", true);
      fireEvent.change(identity, { target: { value: "synthetic-bot" } });
    }
    await save("connection", "connection");
    expect(writes[0].body).toEqual({
      expected_revision: 0,
      value: {
        transport,
        bot_id: transport === "ntfy" ? null : "synthetic-bot",
        secret_ref: "synthetic_slot",
        label: "Synthetic",
        state: "pending",
      },
    });
    fireEvent.click(
      screen.getByRole("button", { name: "Edit connection connection" }),
    );
    expect(screen.getByLabelText("Transport")).toHaveProperty("disabled", true);
    expect(
      screen.getByLabelText(
        "Verified external identity (empty for notification-only transports)",
      ),
    ).toHaveProperty("disabled", true);
    fill("Display label", "Updated");
    select("State", "disabled");
    await save("connection", "connection");
    expect(writes[1].body.expected_revision).toBe(1);
    expect((writes[1].body.value as Record<string, unknown>).bot_id).toBe(
      transport === "ntfy" ? null : "synthetic-bot",
    );
    fireEvent.click(
      screen.getByRole("button", { name: "Edit connection connection" }),
    );
    expect(screen.getByLabelText("Display label")).toHaveProperty(
      "value",
      "Updated",
    );
  },
);
it("creates and edits route policy while fixing its original source binding", async () => {
  const { writes } = mount();
  await newForm("route");
  fill("Stable local ID", "route");
  fill("Connection ID", "chat");
  fill("Resident ID", "herald");
  fill("guild id", "guild");
  fill("channel id", "channel");
  fill("Operator sender IDs (one per line)", "one\ntwo");
  await save("route", "route");
  expect(writes[0].body).toEqual({
    expected_revision: 0,
    value: {
      connection_id: "chat",
      resident_id: "herald",
      address: { guild_id: "guild", channel_id: "channel" },
      label: "",
      state: "pending",
      mode: "dedicated",
      sender_policy: "operators_only",
      operator_ids: ["one", "two"],
    },
  });
  fireEvent.click(screen.getByRole("button", { name: "Edit route route" }));
  expect(screen.getByLabelText("Connection ID")).toHaveProperty(
    "disabled",
    true,
  );
  expect(screen.getByLabelText("Resident ID")).toHaveProperty("disabled", true);
  expect(
    (screen.getByLabelText("guild id") as HTMLInputElement).closest("fieldset")
      ?.disabled,
  ).toBe(true);
  select("Sender policy", "guild_channel_humans");
  await save("route", "route");
  expect(writes[1].body.expected_revision).toBe(1);
  expect((writes[1].body.value as Record<string, unknown>).sender_policy).toBe(
    "guild_channel_humans",
  );
});
it("creates, reloads and updates all four resident grant scopes", async () => {
  const { writes } = mount();
  await newForm("grant");
  fill("Resident ID", "herald");
  for (const key of ["read", "listen", "reply", "post"]) {
    fireEvent.click(screen.getByRole("button", { name: `Add ${key} scope` }));
    const group = screen.getByRole("group", { name: `${key} scope 1` });
    for (const [name, value] of [
      ["connection id", "chat"],
      ["guild id", "guild"],
      ["channel id", "channel"],
    ])
      fireEvent.change(within(group).getByLabelText(name), {
        target: { value },
      });
  }
  await save("grant", "herald");
  expect(writes[0].body.expected_revision).toBe(0);
  for (const group of Object.values(
    writes[0].body.value as Record<string, unknown>,
  ))
    expect(group).toEqual([
      { connection_id: "chat", guild_id: "guild", channel_id: "channel" },
    ]);
  fireEvent.click(screen.getByRole("button", { name: "Edit grant herald" }));
  expect(screen.getByLabelText("Resident ID")).toHaveProperty("disabled", true);
  fireEvent.click(screen.getByRole("button", { name: "Remove post scope 1" }));
  await save("grant", "herald");
  expect(writes[1].body.expected_revision).toBe(1);
  expect((writes[1].body.value as Record<string, unknown>).post).toEqual([]);
});
it.each(["channel", "target"])(
  "creates and updates forwarding with a %s destination",
  async (kind) => {
    const { writes } = mount();
    await newForm("forwarding binding");
    fill("Forwarding ID", "forward");
    select("Destination kind", kind);
    fill("connection id", kind === "target" ? "ntfy" : "chat");
    if (kind === "target") fill("target id", "protected_slot");
    else {
      fill("guild id", "guild");
      fill("channel id", "channel");
    }
    fireEvent.click(screen.getByLabelText("run.succeeded"));
    fireEvent.click(screen.getByLabelText("Forward new notifications"));
    await save("forwarding", "forward");
    const destination =
      kind === "target"
        ? { connection_id: "ntfy", target_id: "protected_slot" }
        : { connection_id: "chat", guild_id: "guild", channel_id: "channel" };
    expect(writes[0].body).toEqual({
      expected_revision: 0,
      destination,
      kinds: ["run.succeeded"],
      enabled: true,
      operator_url: null,
    });
    fireEvent.click(
      screen.getByRole("button", { name: "Edit forwarding forward" }),
    );
    expect(screen.getByLabelText("Destination kind")).toHaveProperty(
      "disabled",
      true,
    );
    expect(
      (screen.getByLabelText("connection id") as HTMLInputElement).closest(
        "fieldset",
      )?.disabled,
    ).toBe(true);
    fireEvent.click(screen.getByLabelText("run.failed"));
    fill("Operator home origin (optional)", "https://hearth.example");
    await save("forwarding", "forward");
    expect(writes[1].body).toEqual({
      expected_revision: 1,
      destination,
      kinds: ["run.succeeded", "run.failed"],
      enabled: true,
      operator_url: "https://hearth.example",
    });
  },
);
it("bounds each editable grant group at 32 scopes", async () => {
  mount([
    {
      kind: "grant",
      id: "herald",
      value: {
        revision: 1,
        read: Array.from({ length: 32 }, (_, i) => ({
          connection_id: "chat",
          guild_id: "guild",
          channel_id: `channel-${i}`,
        })),
        listen: [],
        reply: [],
        post: [],
      },
    },
  ]);
  fireEvent.click(
    await screen.findByRole("button", { name: "Edit grant herald" }),
  );
  expect(screen.getByRole("button", { name: "Add read scope" })).toHaveProperty(
    "disabled",
    true,
  );
});
