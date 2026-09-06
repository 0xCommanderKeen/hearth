// @vitest-environment jsdom
import { afterEach, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { Client, RequestError } from "../../shared/client";
import { Assignments } from "./Assignments";
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});
it("preserves a reordered draft when another editor changes assignments", async () => {
  const client = new Client("synthetic-token");
  const one = {
    skill_id: "one",
    revision: 1,
    name: "Summary",
    description: "Summarize notes",
    instructions: "Notes",
    sha256: "a",
    status: "active" as const,
    created_by: "operator",
    edited_by: "operator",
    created_at: 1,
    edited_at: 1,
  };
  const two = { ...one, skill_id: "two", name: "Tone" };
  vi.spyOn(client, "assignments").mockResolvedValue({
    resident_id: "reader",
    revision: 1,
    sha256: "set",
    skills: [one, two],
  });
  vi.spyOn(client, "skills").mockResolvedValue([one, two]);
  vi.spyOn(client, "skillHistory").mockImplementation(async (id) => [
    id === "one" ? one : two,
  ]);
  const save = vi
    .spyOn(client, "saveAssignments")
    .mockRejectedValue(new RequestError(409, "revision conflict"));
  render(<Assignments client={client} residentId="reader" readOnly={false} />);
  await screen.findByRole("button", { name: "Move Tone up" });
  fireEvent.click(screen.getByRole("button", { name: "Move Tone up" }));
  fireEvent.click(screen.getByRole("button", { name: "Save assignments" }));
  await screen.findByText(/Your assignment draft is retained/);
  expect(save.mock.calls[0][0].skills.map((s) => s.skill_id)).toEqual([
    "two",
    "one",
  ]);
  expect(screen.getAllByRole("listitem")[0].textContent).toContain("Tone");
});
it("retries an unconfirmed assignment mutation with the original operation identity", async () => {
  const client = new Client("synthetic-token");
  vi.spyOn(client, "assignments").mockResolvedValue({
    resident_id: "reader",
    revision: 0,
    sha256: "empty",
    skills: [],
  });
  vi.spyOn(client, "skills").mockResolvedValue([]);
  const save = vi
    .spyOn(client, "saveAssignments")
    .mockRejectedValueOnce(new Error("lost response"))
    .mockResolvedValue({ command_id: "saved", revision: 1 });
  render(<Assignments client={client} residentId="reader" readOnly={false} />);
  await screen.findByText("No reusable skills assigned.");
  fireEvent.click(screen.getByRole("button", { name: "Save assignments" }));
  await screen.findByText(/Save is unconfirmed/);
  fireEvent.click(
    screen.getByRole("button", { name: "Retry pending assignment save" }),
  );
  await screen.findByText(/Saved assignment set 1/);
  expect(save.mock.calls[1][0]).toEqual(save.mock.calls[0][0]);
});
