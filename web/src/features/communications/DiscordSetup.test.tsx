// @vitest-environment jsdom
import { afterEach, expect, it, vi } from "vitest";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { DiscordSetup, DiscordDiagnostics } from "./DiscordSetup";
import type { Config, Status } from "./api";
import type { Resident, Runtimes } from "../../shared/client";
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});
const residents: Resident[] = [
  {
    id: "actual-herald",
    name: "Herald",
    purpose: "Selected test work",
    revision: 1,
    daily_limit: 25000,
    presence: "ready",
    pause_reason: null,
  },
];
const runtimes: Runtimes = {
  default: "selected_runtime",
  configured: ["selected_runtime"],
  kinds: {
    selected_runtime: { label: "Actual configured runtime", live: true },
  },
};
const connection: Config = {
  kind: "connection",
  id: "bot",
  value: {
    revision: 2,
    transport: "discord",
    state: "active",
    bot_id: "100",
    secret_ref: "protected_slot",
    label: "Test bot",
  },
};
const destination = {
  connection_id: "bot",
  guild_id: "200",
  channel_id: "300",
};
const other = { connection_id: "bot", guild_id: "200", channel_id: "301" };
const route: Config = {
  kind: "route",
  id: "selected-route",
  value: {
    revision: 3,
    connection_id: "bot",
    resident_id: "actual-herald",
    address: { guild_id: "200", channel_id: "300" },
    state: "active",
    label: "Selected channel",
    mode: "dedicated",
    sender_policy: "guild_channel_humans",
    operator_ids: [],
  },
};
const grant: Config = {
  kind: "grant",
  id: "actual-herald",
  value: { revision: 4, read: [other], listen: [], reply: [], post: [other] },
};
const status: Status = {
  read_only: false,
  configuration: [],
  next_after: null,
  bindings: [],
  schedule: [],
  health: { communications: "running" },
  retention: "Bounded transcripts",
};
function mount(
  configs = [connection],
  over: Partial<Status> = {},
  disabled = false,
) {
  const onSave = vi.fn(async () => true);
  const onEdit = vi.fn();
  render(
    <DiscordSetup
      configs={configs}
      status={{ ...status, configuration: configs, ...over }}
      residents={residents}
      runtimes={runtimes}
      disabled={disabled}
      onEdit={onEdit}
      onSave={onSave}
    />,
  );
  fireEvent.click(screen.getByText("Discord setup", { selector: "summary" }));
  return { onSave, onEdit };
}
function choose() {
  fireEvent.change(screen.getByLabelText("Discord connection"), {
    target: { value: "bot" },
  });
  fireEvent.change(screen.getByLabelText("Resident for Discord"), {
    target: { value: "actual-herald" },
  });
  fireEvent.change(screen.getByLabelText("Discord guild ID"), {
    target: { value: "200" },
  });
  fireEvent.change(screen.getByLabelText("Discord channel ID"), {
    target: { value: "300" },
  });
}
it("uses actual resident and runtime references without choosing a live target", () => {
  mount();
  expect(screen.getByLabelText("Resident for Discord")).toHaveProperty(
    "value",
    "",
  );
  expect(screen.getByLabelText("Discord connection")).toHaveProperty(
    "value",
    "",
  );
  choose();
  expect(screen.getByText(/Actual configured runtime/).textContent).toContain(
    "$0.03",
  );
  expect(
    screen
      .getByRole("link", { name: "Review resident settings" })
      .getAttribute("href"),
  ).toBe("#residents/actual-herald?tab=settings");
  expect(screen.getByText(/may appear offline/)).toBeTruthy();
});
it("prepares a pending Discord bot reference without credential bytes", () => {
  const { onEdit, onSave } = mount();
  fireEvent.click(
    screen.getByRole("button", { name: "Create Discord connection" }),
  );
  expect(onEdit).toHaveBeenCalledWith({
    kind: "connection",
    id: "",
    value: {
      revision: 0,
      transport: "discord",
      bot_id: "",
      secret_ref: "",
      state: "pending",
      label: "",
    },
  });
  expect(onSave).not.toHaveBeenCalled();
});
it("saves explicit dedicated human-mention routing without implicit grants", async () => {
  const { onSave } = mount();
  choose();
  fireEvent.change(screen.getByLabelText("Local route ID"), {
    target: { value: "new-route" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Save Discord route" }));
  await waitFor(() => expect(onSave).toHaveBeenCalledOnce());
  expect(onSave).toHaveBeenCalledWith("route", "new-route", 0, {
    connection_id: "bot",
    resident_id: "actual-herald",
    address: { guild_id: "200", channel_id: "300" },
    label: "",
    state: "pending",
    mode: "dedicated",
    sender_policy: "guild_channel_humans",
    operator_ids: [],
  });
});
it("separately adds four capabilities while preserving unrelated destinations", async () => {
  const { onSave } = mount([connection, grant]);
  choose();
  for (const name of [
    "Read channel history",
    "Receive mentioned requests",
    "Reply to mentioned requests",
    "Publish announcements",
  ])
    fireEvent.click(screen.getByLabelText(new RegExp(name)));
  fireEvent.click(
    screen.getByRole("button", { name: "Save Discord permissions" }),
  );
  await waitFor(() => expect(onSave).toHaveBeenCalledOnce());
  expect(onSave).toHaveBeenCalledWith("grant", "actual-herald", 4, {
    read: [other, destination],
    listen: [destination],
    reply: [destination],
    post: [other, destination],
  });
});
it("revokes only selected-channel permissions at the current grant revision", async () => {
  const { onSave } = mount([
    connection,
    {
      ...grant,
      value: {
        ...grant.value,
        read: [other, destination],
        listen: [destination],
        reply: [destination],
        post: [other, destination],
      },
    },
  ]);
  choose();
  fireEvent.click(
    screen.getByRole("button", { name: "Revoke selected channel permissions" }),
  );
  await waitFor(() => expect(onSave).toHaveBeenCalledOnce());
  expect(onSave).toHaveBeenCalledWith("grant", "actual-herald", 4, {
    read: [other],
    listen: [],
    reply: [],
    post: [other],
  });
});
it("edits and revokes existing routes without changing their immutable binding", async () => {
  const { onSave } = mount([connection, route]);
  fireEvent.change(screen.getByLabelText("Discord route"), {
    target: { value: "selected-route" },
  });
  for (const label of [
    "Local route ID",
    "Discord connection",
    "Resident for Discord",
    "Discord guild ID",
    "Discord channel ID",
  ])
    expect(screen.getByLabelText(label)).toHaveProperty("disabled", true);
  fireEvent.click(screen.getByRole("button", { name: "Revoke Discord route" }));
  await waitFor(() => expect(onSave).toHaveBeenCalledOnce());
  expect(onSave.mock.calls[0]).toEqual(
    [
      "route",
      "selected-route",
      3,
      { ...route.value, revision: undefined, state: "disabled" },
    ].map((item, index) =>
      index === 3
        ? Object.fromEntries(
            Object.entries(item as object).filter(
              ([key]) => key !== "revision",
            ),
          )
        : item,
    ),
  );
});
it("keeps held, incomplete and omitted-grant copies from overwriting scope", () => {
  mount([connection], { next_after: "grant:later" }, true);
  expect(
    screen.getByRole("button", { name: "Create Discord connection" }),
  ).toHaveProperty("disabled", true);
  expect(
    (
      screen.getByRole("button", {
        name: "Save Discord permissions",
      }) as HTMLButtonElement
    ).closest("fieldset")?.disabled,
  ).toBe(true);
  cleanup();
  mount([connection], {
    configuration: [
      connection,
      {
        kind: "grant",
        id: "actual-herald",
        value: null,
        revision: 1,
        omitted: "configuration_too_large",
      },
    ],
  });
  choose();
  expect(screen.getByRole("alert").textContent).toContain("cannot overwrite");
  expect(
    (
      screen.getByRole("button", {
        name: "Save Discord permissions",
      }) as HTMLButtonElement
    ).closest("fieldset")?.disabled,
  ).toBe(true);
});
it("reports cached missing token, content denial, rate limit, revocation and scan progress separately", () => {
  render(
    <DiscordDiagnostics
      configs={[
        connection,
        { ...route, value: { ...route.value, state: "disabled" } },
      ]}
      status={{
        ...status,
        health: {
          communications: "running",
          credentials: {
            bot: { state: "missing", revision: 2, checked_at: 100 },
          },
          connections: {
            bot: { state: "permission_denied", content_access: "unavailable" },
          },
        },
        schedule: [
          {
            kind: "connection",
            id: "bot",
            eligible_at: 200,
            error: "rate_limited",
          },
        ],
        poll_progress: [
          {
            connection_id: "bot",
            guild_id: "200",
            channel_id: "300",
            cursor: "50",
            through_id: "100",
            updated_at: 100,
          },
        ],
      }}
    />,
  );
  expect(screen.getByText(/Protected token: missing/)).toBeTruthy();
  expect(screen.getByText(/Discord denied channel permissions/)).toBeTruthy();
  expect(screen.getByText(/Message Content access: unavailable/)).toBeTruthy();
  expect(screen.getByText(/full recorded deadline/)).toBeTruthy();
  expect(screen.getByText(/Route Selected channel · Revoked/)).toBeTruthy();
  expect(screen.getByText(/Backlog scan in progress/)).toBeTruthy();
});
it("treats old credential checks as unknown and distinguishes invalid authentication", () => {
  render(
    <DiscordDiagnostics
      configs={[connection]}
      status={{
        ...status,
        health: {
          credentials: {
            bot: { state: "configured", revision: 1, checked_at: 100 },
          },
          connections: {
            bot: { state: "authentication_failed", content_access: "unknown" },
          },
        },
      }}
    />,
  );
  expect(screen.getByText(/Protected token: unknown/)).toBeTruthy();
  expect(screen.getByText(/Discord rejected the token/)).toBeTruthy();
  expect(screen.queryByText(/configured and readable/)).toBeNull();
});
it("preserves an existing restricted policy and discards unsaved edits when revoking", async () => {
  const restricted = {
    ...route,
    value: {
      ...route.value,
      mode: "shared",
      sender_policy: "operators_only",
      operator_ids: ["400"],
    },
  };
  const { onSave } = mount([connection, restricted]);
  fireEvent.change(screen.getByLabelText("Discord route"), {
    target: { value: "selected-route" },
  });
  fireEvent.change(screen.getByLabelText("Channel display label"), {
    target: { value: "Unsaved label" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Save Discord route" }));
  await waitFor(() => expect(onSave).toHaveBeenCalledTimes(1));
  expect(onSave.mock.calls[0]).toEqual([
    "route",
    "selected-route",
    3,
    {
      connection_id: "bot",
      resident_id: "actual-herald",
      address: { guild_id: "200", channel_id: "300" },
      state: "active",
      label: "Unsaved label",
      mode: "shared",
      sender_policy: "operators_only",
      operator_ids: ["400"],
    },
  ]);
  fireEvent.click(screen.getByRole("button", { name: "Revoke Discord route" }));
  await waitFor(() => expect(onSave).toHaveBeenCalledTimes(2));
  expect(onSave.mock.calls[1]).toEqual([
    "route",
    "selected-route",
    3,
    {
      connection_id: "bot",
      resident_id: "actual-herald",
      address: { guild_id: "200", channel_id: "300" },
      state: "disabled",
      label: "Selected channel",
      mode: "shared",
      sender_policy: "operators_only",
      operator_ids: ["400"],
    },
  ]);
});
