// @vitest-environment jsdom
import { afterEach, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { Client, RequestError, type CatalogSkill } from "../../shared/client";
import { SkillCatalog } from "./SkillCatalog";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  window.location.hash = "";
});
const skill: CatalogSkill = {
  skill_id: "summary",
  revision: 1,
  name: "Daily summary",
  description: "Use for notes",
  instructions: "# Summary\n<script>alert(1)</script>",
  status: "active",
  created_by: "operator",
  edited_by: "operator",
  created_at: 1,
  edited_at: 1,
  sha256: "digest",
};
function setup() {
  const client = new Client("synthetic-token");
  vi.spyOn(client, "skills").mockResolvedValue([skill]);
  vi.spyOn(client, "skill").mockResolvedValue(skill);
  vi.spyOn(client, "skillHistory").mockResolvedValue([skill]);
  render(<SkillCatalog client={client} readOnly={false} />);
  return client;
}
it("opens persisted instructions, safely previews Markdown and retains a stale draft", async () => {
  window.location.hash = "#skills/summary";
  const client = setup();
  const save = vi
    .spyOn(client, "changeSkill")
    .mockRejectedValue(new RequestError(409, "revision conflict"));
  await screen.findByLabelText("Markdown instructions");
  expect(screen.getByRole("heading", { name: "Summary" })).toBeTruthy();
  expect(document.querySelector("script")).toBeNull();
  fireEvent.change(screen.getByLabelText("Markdown instructions"), {
    target: { value: "My draft" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Save revision" }));
  await screen.findByText(/Your draft is retained/);
  expect(
    (screen.getByLabelText("Markdown instructions") as HTMLTextAreaElement)
      .value,
  ).toBe("My draft");
  expect(save).toHaveBeenCalledTimes(1);
});

it("retries the exact unconfirmed operation without accepting a changed draft", async () => {
  window.location.hash = "#skills/summary";
  const client = setup();
  const save = vi
    .spyOn(client, "changeSkill")
    .mockRejectedValueOnce(new Error("connection lost"))
    .mockResolvedValue({
      command_id: "unused",
      skill_id: "summary",
      revision: 2,
      operation: "save",
      recorded_at: 2,
      actor: "operator",
    });
  await screen.findByLabelText("Markdown instructions");
  fireEvent.change(screen.getByLabelText("Markdown instructions"), {
    target: { value: "Exact new instructions" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Save revision" }));
  await screen.findByText(/Save is unconfirmed/);
  expect(
    (
      screen.getByLabelText("Markdown instructions") as HTMLTextAreaElement
    ).closest("fieldset")?.disabled,
  ).toBe(true);
  fireEvent.click(screen.getByRole("button", { name: "Retry pending save" }));
  await screen.findByText(/Saved revision 2/);
  expect(save).toHaveBeenCalledTimes(2);
  expect(save.mock.calls[1][0]).toEqual(save.mock.calls[0][0]);
});

it("loads history without replacing the editor and archives using the displayed revision", async () => {
  window.location.hash = "#skills/summary";
  const client = setup();
  const save = vi.spyOn(client, "changeSkill").mockResolvedValue({
    command_id: "archive",
    skill_id: "summary",
    revision: 2,
    operation: "archive",
    recorded_at: 2,
    actor: "operator",
  });
  await screen.findByLabelText("Markdown instructions");
  fireEvent.change(screen.getByLabelText("Markdown instructions"), {
    target: { value: "Unsaved draft" },
  });
  fireEvent.click(screen.getByRole("button", { name: /Revision 1 · active/ }));
  expect(screen.getByLabelText("Revision 1 content")).toBeTruthy();
  expect(
    (screen.getByLabelText("Markdown instructions") as HTMLTextAreaElement)
      .value,
  ).toBe("Unsaved draft");
  vi.mocked(client.skill).mockResolvedValue({
    ...skill,
    status: "archived",
    revision: 2,
  });
  fireEvent.click(screen.getByRole("button", { name: "Archive skill" }));
  await screen.findByText(/Archived skills remain available/);
  expect(save.mock.calls[0][0]).toEqual({
    command_id: expect.any(String),
    skill_id: "summary",
    expected_revision: 1,
  });
});

it("shows loading failures, retry and an empty searchable catalog", async () => {
  const client = new Client("synthetic-token");
  vi.spyOn(client, "skills")
    .mockRejectedValueOnce(new Error("Unavailable"))
    .mockResolvedValue([]);
  render(<SkillCatalog client={client} readOnly={false} />);
  expect(screen.getByText("Loading skills…")).toBeTruthy();
  await screen.findByText("Unavailable");
  fireEvent.click(screen.getByRole("button", { name: "Retry loading skills" }));
  await screen.findByText("Your shared library starts here.");
  fireEvent.change(screen.getByLabelText("Search skills"), {
    target: { value: "missing" },
  });
  await screen.findByText("No matching skills.");
});
