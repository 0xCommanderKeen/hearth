// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { Client, RequestError } from "../../shared/client";
import { ManagementPanel } from "./Management";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  window.location.hash = "";
});
const grant = {
  resident_id: "karen",
  revision: 1,
  enabled: true,
  profiles: ["codex_subscription"],
  input_set_ids: [],
  capabilities: ["create_residents", "assign_work"] as (
    "create_residents" | "assign_work" | "routines"
  )[],
  mounts: [{ name: "notes", host_path: "/srv/notes", mode: "ro" as const }],
  max_residents: 5,
  max_daily_limit: 1000000,
  max_reserve: 500000,
  max_calls: 64,
};
function fixture() {
  const client = new Client("synthetic-management");
  vi.spyOn(client, "managementCatalog").mockResolvedValue({
    residents: [{ id: "karen", name: "Karen", grant }],
    profiles: [
      { id: "codex_subscription", name: "Codex subscription" },
      { id: "claude_subscription", name: "Claude subscription" },
    ],
    input_sets: [],
    operations: [],
  });
  return client;
}
it("preserves a conflicting policy draft and exposes operator scope explicitly", async () => {
  const client = fixture();
  const save = vi
    .spyOn(client, "saveManagement")
    .mockRejectedValue(new RequestError(409, "revision conflict"));
  render(
    <ManagementPanel client={client} readOnly={false} onChanged={() => {}} />,
  );
  await screen.findByLabelText("Maximum managed residents");
  fireEvent.change(screen.getByLabelText("Maximum managed residents"), {
    target: { value: "3" },
  });
  fireEvent.click(
    screen.getByRole("button", { name: "Save management grant" }),
  );
  await screen.findByText(/Your draft is preserved/);
  expect(
    (screen.getByLabelText("Maximum managed residents") as HTMLInputElement)
      .value,
  ).toBe("3");
  expect(save.mock.calls[0][1].max_residents).toBe(3);
  expect(
    screen.getByText(/New residents receive no management grant/),
  ).toBeTruthy();
});
it("held copies display grants while disabling policy writes and bootstrap", async () => {
  const client = fixture();
  render(
    <ManagementPanel client={client} readOnly={true} onChanged={() => {}} />,
  );
  await screen.findByLabelText("Maximum managed residents");
  expect(
    (
      screen.getByRole("button", {
        name: "Save management grant",
      }) as HTMLButtonElement
    ).disabled,
  ).toBe(true);
  expect(screen.queryByRole("button", { name: "Set up Karen" })).toBeNull();
});

it("edits the folders a resident reaches and sends them with the grant", async () => {
  const client = fixture();
  const save = vi.spyOn(client, "saveManagement").mockResolvedValue(grant);
  render(
    <ManagementPanel client={client} readOnly={false} onChanged={() => {}} />,
  );
  await screen.findByLabelText("Folder 1 name");
  expect(
    (screen.getByLabelText("Folder 1 path") as HTMLInputElement).value,
  ).toBe("/srv/notes");
  // The granted folder becomes writable, and a second one is added beside it.
  fireEvent.change(screen.getByLabelText("Folder 1 access"), {
    target: { value: "rw" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Add folder" }));
  fireEvent.change(screen.getByLabelText("Folder 2 name"), {
    target: { value: "drafts" },
  });
  fireEvent.change(screen.getByLabelText("Folder 2 path"), {
    target: { value: "/srv/drafts" },
  });
  fireEvent.click(
    screen.getByRole("button", { name: "Save management grant" }),
  );
  await vi.waitFor(() => expect(save.mock.calls.length).toBe(1));
  expect(save.mock.calls[0][1].mounts).toEqual([
    { name: "notes", host_path: "/srv/notes", mode: "rw" },
    { name: "drafts", host_path: "/srv/drafts", mode: "ro" },
  ]);
});
it("removes a folder without touching the rest of the grant", async () => {
  const client = fixture();
  const save = vi.spyOn(client, "saveManagement").mockResolvedValue(grant);
  render(
    <ManagementPanel client={client} readOnly={false} onChanged={() => {}} />,
  );
  await screen.findByLabelText("Folder 1 name");
  fireEvent.click(screen.getByRole("button", { name: "Remove folder 1" }));
  expect(screen.queryByLabelText("Folder 1 name")).toBeNull();
  fireEvent.click(
    screen.getByRole("button", { name: "Save management grant" }),
  );
  await vi.waitFor(() => expect(save.mock.calls.length).toBe(1));
  expect(save.mock.calls[0][1].mounts).toEqual([]);
  expect(save.mock.calls[0][1].capabilities).toEqual([
    "create_residents",
    "assign_work",
  ]);
});
