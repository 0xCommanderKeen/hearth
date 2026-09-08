// @vitest-environment jsdom
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import {
  Client,
  RequestError,
  type ImportReceipt,
  type ResidentBundle,
} from "../../shared/client";
import { ImportResident } from "./ImportResident";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  window.location.hash = "";
});

const bundle: ResidentBundle = {
  bundle_version: 1,
  source: {
    resident_id: "karen",
    declaration_revision: 1,
    memory_revision: 1,
    exported_at: 1,
  },
  resident: {
    name: "Karen",
    purpose: "Create and manage useful residents.",
    instructions: "Verify receipts.",
    memory: "New residents receive no management capabilities.",
    daily_limit: 2_000_000,
    budget_timezone: "Europe/Ljubljana",
    execution_profile: "codex_subscription",
    creation_reason: "Operator setup",
  },
  skills: [
    {
      name: "Create residents",
      description: "",
      instructions: "#",
      sha256: "a",
    },
  ],
  input_sets: [],
  routine: null,
  management: { enabled: true, capabilities: ["create_residents"] },
};

function receipt(overrides: Partial<ImportReceipt> = {}): ImportReceipt {
  return {
    command_id: "x",
    resident_id: "new",
    status: "ready",
    reason: null,
    creator: "operator",
    manager: "operator",
    originating_run_id: null,
    created_at: 1,
    routine_id: null,
    task_id: null,
    setup: {} as ImportReceipt["setup"],
    resolution: {
      skills: [
        {
          name: "Create residents",
          skill_id: "s",
          revision: 1,
          outcome: "created",
        },
      ],
      input_sets: [],
      execution_profile: {
        requested: "a-retired-profile",
        used: "codex_subscription",
      },
      management_ignored: true,
    },
    ...overrides,
  };
}

async function chooseFile(
  content: string,
  name = "karen.hearth-resident.json",
) {
  const input = screen.getByLabelText("Bundle file") as HTMLInputElement;
  const file = new File([content], name, { type: "application/json" });
  Object.defineProperty(file, "text", {
    value: () => Promise.resolve(content),
  });
  fireEvent.change(input, { target: { files: [file] } });
  await waitFor(() => expect(input.disabled).toBe(false));
}

it("summarises the bundle, warns about carried authority and imports with the same id on retry", async () => {
  const client = new Client("synthetic-test-token");
  const importResident = vi
    .spyOn(client, "importResident")
    .mockRejectedValueOnce(new Error("network"))
    .mockImplementation(async (id) => receipt({ command_id: id }));
  render(<ImportResident client={client} readOnly={false} />);
  await chooseFile(JSON.stringify(bundle));
  await screen.findByRole("heading", { name: "Karen" });
  expect(screen.getByLabelText("Bundle summary").textContent).toContain(
    "Create residents",
  );
  expect(screen.getByText(/carried a management grant/)).toBeTruthy();
  fireEvent.change(screen.getByLabelText("Resident name"), {
    target: { value: "Karen copy" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Import resident" }));
  await screen.findByRole("button", { name: "Retry import" });
  expect(screen.getByRole("alert").textContent).toContain("unconfirmed");
  fireEvent.click(screen.getByRole("button", { name: "Retry import" }));
  await waitFor(() => expect(importResident).toHaveBeenCalledTimes(2));
  expect(importResident.mock.calls[0][0]).toBe(importResident.mock.calls[1][0]);
  expect(importResident.mock.calls[1][1]).toEqual({
    bundle,
    overrides: { name: "Karen copy" },
  });
  await waitFor(() =>
    expect(window.location.hash).toBe(
      `#new-resident/${importResident.mock.calls[1][0]}`,
    ),
  );
});

it("rejects files that are not bundles and clears a definite refusal", async () => {
  const client = new Client("synthetic-test-token");
  const importResident = vi
    .spyOn(client, "importResident")
    .mockRejectedValue(new RequestError(409, "bundle_digest_mismatch"));
  render(<ImportResident client={client} readOnly={false} />);
  await chooseFile("not json");
  expect(screen.getByRole("alert").textContent).toContain("not JSON");
  await chooseFile(JSON.stringify({ bundle_version: 2 }));
  expect(screen.getByRole("alert").textContent).toContain("version 1");
  await chooseFile(JSON.stringify(bundle));
  fireEvent.click(
    await screen.findByRole("button", { name: "Import resident" }),
  );
  await waitFor(() =>
    expect(screen.getByRole("alert").textContent).toContain(
      "bundle_digest_mismatch",
    ),
  );
  expect(screen.getByRole("button", { name: "Import resident" })).toBeTruthy();
  expect(importResident).toHaveBeenCalledTimes(1);
  expect(window.location.hash).toBe("");
});

it("disables import on a restored copy", async () => {
  const client = new Client("synthetic-test-token");
  render(<ImportResident client={client} readOnly={true} />);
  expect(
    (screen.getByLabelText("Bundle file") as HTMLInputElement).disabled,
  ).toBe(true);
  expect(screen.getByText(/read-only/)).toBeTruthy();
});
