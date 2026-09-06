// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { Client } from "../../shared/client";
import { InputSelection } from "./Selection";
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});
it("selects synthetic inputs explicitly and retries the same uncertain operation", async () => {
  const client = new Client("synthetic-input-token");
  vi.spyOn(client, "inputSets").mockResolvedValue([
    {
      input_set_id: "orchard",
      name: "Orchard",
      notes: ["Fictional pears"],
      revision: 1,
      sha256: "digest",
      synthetic: true,
      created_by: "operator",
      created_at: 1,
      edited_by: "operator",
      edited_at: 1,
    },
  ]);
  vi.spyOn(client, "inputSelection").mockResolvedValue({
    resident_id: "reader",
    revision: 0,
    input_sets: [],
  });
  const save = vi
    .spyOn(client, "selectInputs")
    .mockRejectedValue(new Error("response lost"));
  render(
    <InputSelection client={client} residentId="reader" readOnly={false} />,
  );
  await screen.findByText(
    "No inputs selected. This resident receives no notes.",
  );
  fireEvent.click(screen.getByRole("checkbox", { name: /Orchard/ }));
  fireEvent.click(screen.getByRole("button", { name: "Save input selection" }));
  await screen.findByText(/unconfirmed/);
  fireEvent.click(
    screen.getByRole("button", { name: "Retry pending selection" }),
  );
  expect(save.mock.calls[1][0]).toEqual(save.mock.calls[0][0]);
  expect(save.mock.calls[0][0].input_sets).toEqual([
    { input_set_id: "orchard" },
  ]);
});
