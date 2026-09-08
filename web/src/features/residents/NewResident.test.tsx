// @vitest-environment jsdom
import { afterEach, expect, it, vi } from "vitest";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { NewResident } from "./NewResident";
import { Client, RequestError } from "../../shared/client";
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});
const options = {
  execution_profiles: [
    { id: "codex_subscription", name: "Configured Codex subscription" },
  ],
  input_sets: [
    {
      input_set_id: "synthetic-reader-notes",
      name: "Synthetic Reader example notes",
      synthetic: true,
    },
  ],
  managers: [{ id: "operator", name: "Operator" }],
};
it("retains an unconfirmed setup and retries the same operation identity", async () => {
  const client = new Client("synthetic-provision-token");
  vi.spyOn(client, "request").mockResolvedValue(options);
  vi.spyOn(client, "skills").mockResolvedValue([]);
  const provision = vi
    .spyOn(client, "provision")
    .mockRejectedValueOnce(new Error("connection lost"))
    .mockImplementation(async (id, body) => ({
      command_id: id,
      resident_id: "reporter",
      status: "ready",
      reason: null,
      creator: "operator",
      manager: "operator",
      originating_run_id: null,
      created_at: 1,
      routine_id: null,
      task_id: null,
      setup: body,
    }));
  const onCreated = vi.fn().mockResolvedValue(undefined);
  render(
    <NewResident
      client={client}
      readOnly={false}
      commandId=""
      onCreated={onCreated}
    />,
  );
  await screen.findByLabelText("Resident name");
  fireEvent.change(screen.getByLabelText("Resident name"), {
    target: { value: "Reporter" },
  });
  fireEvent.change(screen.getByLabelText("Purpose"), {
    target: { value: "Summarize notes" },
  });
  fireEvent.change(screen.getByLabelText("Creation reason"), {
    target: { value: "Useful summary" },
  });
  fireEvent.click(screen.getByText("Create resident", { exact: true }));
  await screen.findByText("Retry same setup");
  expect(
    (
      screen
        .getByLabelText("Resident name")
        .closest("fieldset") as HTMLFieldSetElement
    ).disabled,
  ).toBe(true);
  fireEvent.click(screen.getByText("Retry same setup"));
  await waitFor(() => expect(onCreated).toHaveBeenCalledOnce());
  expect(provision.mock.calls[0]).toEqual(provision.mock.calls[1]);
  expect(provision.mock.calls[0][1].input_sets).toEqual([]);
});
it("displays a failed saved setup with its original configuration and retry", async () => {
  const client = new Client("synthetic-provision-token");
  const setup = {
    name: "Failed reporter",
    purpose: "Notes",
    instructions: "",
    initial_memory: "remember",
    skills: [],
    execution_profile: "codex_subscription",
    input_sets: [],
    daily_limit: 10000,
    budget_timezone: "UTC",
    creation_reason: "Synthetic",
    manager: "operator",
    routine: null,
    first_assignment: null,
  };
  vi.spyOn(client, "request").mockImplementation(async (path) =>
    path === "/api/resident-options"
      ? options
      : {
          command_id: "old",
          resident_id: "same",
          status: "failed",
          reason: "household_resident_limit",
          setup,
        },
  );
  vi.spyOn(client, "skills").mockResolvedValue([]);
  render(
    <NewResident
      client={client}
      readOnly={false}
      commandId="old"
      onCreated={async () => {}}
    />,
  );
  await screen.findByText("Setup failed");
  expect(
    (screen.getByLabelText("Initial memory") as HTMLTextAreaElement).value,
  ).toBe("remember");
  expect(screen.getByText("household resident limit")).toBeTruthy();
  expect(screen.getByText("Retry same setup")).toBeTruthy();
});

it.each([413, 422])(
  "uses a new operation for a corrected draft after definite rejection %i",
  async (status) => {
    const client = new Client("synthetic-provision-token");
    vi.spyOn(client, "request").mockResolvedValue(options);
    vi.spyOn(client, "skills").mockResolvedValue([]);
    const provision = vi
      .spyOn(client, "provision")
      .mockRejectedValue(new RequestError(status, "invalid setup"));
    render(
      <NewResident
        client={client}
        readOnly={false}
        commandId=""
        onCreated={async () => {}}
      />,
    );
    await screen.findByLabelText("Resident name");
    fireEvent.change(screen.getByLabelText("Resident name"), {
      target: { value: "Original" },
    });
    fireEvent.change(screen.getByLabelText("Purpose"), {
      target: { value: "Notes" },
    });
    fireEvent.change(screen.getByLabelText("Creation reason"), {
      target: { value: "Synthetic" },
    });
    fireEvent.click(screen.getByText("Create resident", { exact: true }));
    await screen.findByText("invalid setup");
    fireEvent.change(screen.getByLabelText("Resident name"), {
      target: { value: "Corrected" },
    });
    fireEvent.click(screen.getByText("Create resident", { exact: true }));
    await waitFor(() => expect(provision).toHaveBeenCalledTimes(2));
    expect(provision.mock.calls[0][0]).not.toBe(provision.mock.calls[1][0]);
    expect(provision.mock.calls[1][1].name).toBe("Corrected");
  },
);
it("keeps the confirmed ready receipt if opening its profile fails", async () => {
  const client = new Client("synthetic-provision-token");
  vi.spyOn(client, "request").mockResolvedValue(options);
  vi.spyOn(client, "skills").mockResolvedValue([]);
  vi.spyOn(client, "provision").mockImplementation(async (id, body) => ({
    command_id: id,
    resident_id: "known",
    status: "ready",
    reason: null,
    creator: "operator",
    manager: "operator",
    originating_run_id: null,
    created_at: 1,
    routine_id: null,
    task_id: null,
    setup: body,
  }));
  render(
    <NewResident
      client={client}
      readOnly={false}
      commandId=""
      onCreated={async () => {
        throw new Error("refresh failed");
      }}
    />,
  );
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
  fireEvent.click(screen.getByText("Create resident", { exact: true }));
  await screen.findByText(/Resident is ready. Refreshing/);
  expect(screen.getByText("Open resident →").getAttribute("href")).toBe(
    "#residents/known",
  );
  expect(screen.queryByText("Retry same setup")).toBeNull();
});
function provisioningClient() {
  const client = new Client("synthetic-provision-token");
  vi.spyOn(client, "request").mockResolvedValue(options);
  vi.spyOn(client, "skills").mockResolvedValue([]);
  return {
    client,
    provision: vi
      .spyOn(client, "provision")
      .mockImplementation(async (id, body) => ({
        command_id: id,
        resident_id: "reporter",
        status: "ready",
        reason: null,
        creator: "operator",
        manager: "operator",
        originating_run_id: null,
        created_at: 1,
        routine_id: null,
        task_id: null,
        setup: body,
      })),
  };
}
function fillRequiredFields() {
  fireEvent.change(screen.getByLabelText("Resident name"), {
    target: { value: "Reporter" },
  });
  fireEvent.change(screen.getByLabelText("Purpose"), {
    target: { value: "Notes" },
  });
  fireEvent.change(screen.getByLabelText("Creation reason"), {
    target: { value: "Synthetic" },
  });
}
it("proposes one dollar a day and provisions that when left alone", async () => {
  const { client, provision } = provisioningClient();
  render(
    <NewResident
      client={client}
      readOnly={false}
      commandId=""
      onCreated={async () => {}}
    />,
  );
  await screen.findByLabelText("Resident name");
  expect(
    (screen.getByLabelText("Resident daily allowance ($)") as HTMLInputElement)
      .value,
  ).toBe("1");
  fillRequiredFields();
  fireEvent.click(screen.getByText("Create resident", { exact: true }));
  await waitFor(() => expect(provision).toHaveBeenCalledOnce());
  expect(provision.mock.calls[0][1].daily_limit).toBe(1_000_000);
});
it("provisions a smaller allowance the operator types over the proposal", async () => {
  const { client, provision } = provisioningClient();
  render(
    <NewResident
      client={client}
      readOnly={false}
      commandId=""
      onCreated={async () => {}}
    />,
  );
  await screen.findByLabelText("Resident name");
  fillRequiredFields();
  fireEvent.change(screen.getByLabelText("Resident daily allowance ($)"), {
    target: { value: "0.25" },
  });
  fireEvent.click(screen.getByText("Create resident", { exact: true }));
  await waitFor(() => expect(provision).toHaveBeenCalledOnce());
  expect(provision.mock.calls[0][1].daily_limit).toBe(250_000);
});
