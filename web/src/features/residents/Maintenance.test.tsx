// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import {
  Client,
  RequestError,
  type Configuration,
  type Resident,
} from "../../shared/client";
import { ResidentMaintenance } from "./Maintenance";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});
const resident: Resident = {
  id: "reader",
  name: "Reader",
  purpose: "Read fictional notes",
  revision: 1,
  daily_limit: 100000,
  presence: "ready",
  pause_reason: null,
  lifecycle: {
    resident_id: "reader",
    state: "ready",
    revision: 0,
    manager: "operator",
  },
};
const configuration: Configuration = {
  resident_id: "reader",
  lifecycle: resident.lifecycle!,
  execution_profile: "inline_mock",
  declaration: {
    expected_revision: 1,
    name: "Reader",
    purpose: "Read fictional notes",
    instructions: "Count carefully",
    daily_limit: 100000,
    budget_timezone: "UTC",
  },
  memory: { expected_revision: 0, text: "Original memory" },
  inputs: { expected_revision: 0, input_sets: [] },
  skills: { expected_revision: 0, skills: [] },
  routines: [],
};
function setup(current = resident, config = configuration) {
  const client = new Client("synthetic-test-token");
  vi.spyOn(client, "configuration").mockResolvedValue(structuredClone(config));
  vi.spyOn(client, "skills").mockResolvedValue([]);
  vi.spyOn(client, "request").mockResolvedValue({
    execution_profiles: [],
    input_sets: [],
    managers: [{ id: "operator", name: "Operator" }],
  });
  const save = vi
    .spyOn(client, "maintainResident")
    .mockRejectedValue(new RequestError(409, "revision_conflict"));
  render(
    <ResidentMaintenance
      client={client}
      resident={current}
      readOnly={false}
      routines={[]}
      onChanged={() => {}}
    />,
  );
  return { client, save };
}
it("sends only changed owning groups and retains a conflicting profile draft", async () => {
  const { save } = setup();
  fireEvent.click(
    await screen.findByRole("button", { name: "Edit configuration" }),
  );
  fireEvent.change(screen.getByRole("textbox", { name: "Purpose" }), {
    target: { value: "Human draft purpose" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Save configuration" }));
  await screen.findByText(/draft is retained/i);
  expect(screen.getByRole("textbox", { name: "Purpose" })).toHaveProperty(
    "value",
    "Human draft purpose",
  );
  expect(save.mock.calls[0][0]).toMatchObject({
    kind: "configuration",
    body: {
      expected_lifecycle_revision: 0,
      declaration: { expected_revision: 1, purpose: "Human draft purpose" },
    },
  });
  expect(save.mock.calls[0][0].body).not.toHaveProperty("memory");
});
it("retries exactly the unconfirmed archive without claiming execution stopped", async () => {
  const { save } = setup({
    ...resident,
    presence: "running",
    unresolved_runs: 1,
  });
  save.mockRejectedValue(new Error("response lost"));
  fireEvent.click(
    await screen.findByRole("button", { name: "Archive resident" }),
  );
  await screen.findByText(/unconfirmed/i);
  fireEvent.click(
    screen.getByRole("button", { name: "Retry pending resident change" }),
  );
  expect(save.mock.calls[1][0]).toEqual(save.mock.calls[0][0]);
  expect(screen.getByText(/unresolved run/i)).toBeTruthy();
});

it("accepts a purpose edit when the existing instructions are empty", async () => {
  setup(resident, {
    ...configuration,
    declaration: { ...configuration.declaration, instructions: "" },
  });
  fireEvent.click(
    await screen.findByRole("button", { name: "Edit configuration" }),
  );
  fireEvent.change(screen.getByRole("textbox", { name: "Purpose" }), {
    target: { value: "Updated purpose" },
  });
  const button = screen.getByRole("button", { name: "Save configuration" });
  expect(button.closest("form")?.checkValidity()).toBe(true);
});

it("allows an unrelated edit for a resident with a zero daily limit", async () => {
  setup(resident, {
    ...configuration,
    declaration: { ...configuration.declaration, daily_limit: 0 },
  });
  fireEvent.click(
    await screen.findByRole("button", { name: "Edit configuration" }),
  );
  fireEvent.change(screen.getByRole("textbox", { name: "Purpose" }), {
    target: { value: "Updated purpose" },
  });
  expect(
    screen
      .getByRole("button", { name: "Save configuration" })
      .closest("form")
      ?.checkValidity(),
  ).toBe(true);
});
it("exports the resident definition through the client", async () => {
  const { client } = setup();
  const exportResident = vi.spyOn(client, "exportResident").mockResolvedValue({
    bundle_version: 1,
    source: {
      resident_id: "reader",
      declaration_revision: 1,
      memory_revision: 0,
      exported_at: 1,
    },
    resident: {
      name: "Reader",
      purpose: "Read fictional notes",
      instructions: "Count carefully",
      memory: "",
      daily_limit: 100000,
      budget_timezone: "UTC",
      execution_profile: "inline_mock",
      creation_reason: "Explicit resident setup",
    },
    skills: [],
    input_sets: [{ name: "Notes", notes: ["a"], sha256: "x" }],
    routine: null,
    management: null,
  });
  fireEvent.click(
    await screen.findByRole("button", { name: "Export resident" }),
  );
  await screen.findByText(
    /Exported Reader with 0 skill\(s\) and 1 input set\(s\)/,
  );
  expect(exportResident).toHaveBeenCalledWith("reader");
});
