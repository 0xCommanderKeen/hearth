// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { Client, RequestError } from "../../shared/client";
import { InputLibrary } from "./Inputs";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  window.location.hash = "";
});

it("retains an edited synthetic note draft on conflict and never renders source HTML", async () => {
  window.location.hash = "#inputs/orchard";
  const client = new Client("synthetic-input-token");
  vi.spyOn(client, "inputSets").mockResolvedValue([]);
  vi.spyOn(client, "inputSet").mockResolvedValue({
    input_set_id: "orchard",
    revision: 1,
    name: "Orchard",
    notes: ["<script>run()</script>"],
    sha256: "digest",
    synthetic: true,
    created_at: 1,
    created_by: "operator",
    edited_at: 1,
    edited_by: "operator",
  });
  const save = vi
    .spyOn(client, "saveInput")
    .mockRejectedValue(new RequestError(409, "revision conflict"));
  render(<InputLibrary client={client} readOnly={false} />);
  await screen.findByLabelText("Notes");
  expect(document.querySelector("script")).toBeNull();
  fireEvent.change(screen.getByLabelText("Notes"), {
    target: { value: "Fictional pears: 20." },
  });
  fireEvent.click(screen.getByRole("button", { name: "Save input revision" }));
  await screen.findByText(/Your draft is retained/);
  expect((screen.getByLabelText("Notes") as HTMLTextAreaElement).value).toBe(
    "Fictional pears: 20.",
  );
  expect(save).toHaveBeenCalledOnce();
});

it("shows the exact historical input revision without permitting edits", async () => {
  window.location.hash = "#inputs/orchard/1";
  const client = new Client("synthetic-input-token");
  vi.spyOn(client, "inputSets").mockResolvedValue([]);
  const read = vi.spyOn(client, "inputSet").mockResolvedValue({
    input_set_id: "orchard",
    revision: 1,
    name: "Orchard",
    notes: ["Original fictional pears: 12."],
    sha256: "digest",
    synthetic: true,
    created_at: 1,
    created_by: "operator",
    edited_at: 1,
    edited_by: "operator",
  });
  render(<InputLibrary client={client} readOnly={false} />);
  await screen.findByDisplayValue("Original fictional pears: 12.");
  expect(read).toHaveBeenCalledWith("orchard", 1);
  expect(
    (screen.getByLabelText("Notes") as HTMLTextAreaElement).closest("fieldset")
      ?.disabled,
  ).toBe(true);
  expect(
    screen.queryByRole("button", { name: "Save input revision" }),
  ).toBeNull();
});
