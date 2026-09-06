// @vitest-environment jsdom
import { afterEach, expect, it, vi } from "vitest";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { Memory } from "./Memory";
import { Client, type Resident, type ResidentMemory } from "./client";
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});
const resident: Resident = {
  id: "reader",
  name: "Reader",
  purpose: "Synthetic",
  revision: 1,
  daily_limit: 10000,
  presence: "ready",
  pause_reason: null,
  memory_revision: 1,
};
const saved: ResidentMemory = {
  resident_id: "reader",
  revision: 1,
  sha256: "a".repeat(64),
  text: "# Original memory",
};
const act = async (operation: () => Promise<unknown>) => {
  try {
    await operation();
  } catch {
    /* App owns error display. */
  }
};
function setup(readOnly = false) {
  const client = new Client("synthetic-test-token");
  vi.spyOn(client, "memory").mockResolvedValue(saved);
  const save = vi
    .spyOn(client, "saveMemory")
    .mockImplementation(async (_id, text) => ({ ...saved, revision: 2, text }));
  const props = { client, resident, busy: false, readOnly, act };
  return { client, save, props, ...render(<Memory {...props} />) };
}
async function read() {
  fireEvent.click(screen.getByText("Read memory"));
  await screen.findByLabelText("Memory for Reader");
}
it("saves exact memory using its independent revision", async () => {
  const { save } = setup();
  await read();
  fireEvent.change(screen.getByLabelText("Memory for Reader"), {
    target: { value: "# New\n\nRemember ž.\n" },
  });
  fireEvent.click(screen.getByText("Save memory"));
  await waitFor(() =>
    expect(save).toHaveBeenCalledWith("reader", "# New\n\nRemember ž.\n", 1),
  );
  expect(await screen.findByText("Saved memory revision 2.")).toBeTruthy();
});
it("retains a draft across concurrent memory updates and requires explicit reload", async () => {
  const { props, rerender, save, client } = setup();
  await read();
  fireEvent.change(screen.getByLabelText("Memory for Reader"), {
    target: { value: "My memory draft" },
  });
  rerender(
    <Memory {...props} resident={{ ...resident, memory_revision: 2 }} />,
  );
  expect(
    (screen.getByLabelText("Memory for Reader") as HTMLTextAreaElement).value,
  ).toBe("My memory draft");
  expect(screen.getByText(/Memory changed while/)).toBeTruthy();
  fireEvent.click(screen.getByText("Save memory"));
  expect(save).not.toHaveBeenCalled();
  vi.mocked(client.memory).mockResolvedValue({
    ...saved,
    revision: 2,
    text: "Concurrent memory",
  });
  fireEvent.click(screen.getByText("Load current memory (replace draft)"));
  await waitFor(() =>
    expect(
      (screen.getByLabelText("Memory for Reader") as HTMLTextAreaElement).value,
    ).toBe("Concurrent memory"),
  );
});
it("preserves a draft after an ambiguous save and never displays success", async () => {
  const { save } = setup();
  await read();
  save.mockRejectedValue(new Error("Lost response"));
  fireEvent.change(screen.getByLabelText("Memory for Reader"), {
    target: { value: "Unconfirmed memory" },
  });
  fireEvent.click(screen.getByText("Save memory"));
  await waitFor(() => expect(save).toHaveBeenCalledTimes(1));
  expect(
    (screen.getByLabelText("Memory for Reader") as HTMLTextAreaElement).value,
  ).toBe("Unconfirmed memory");
  expect(screen.queryByText(/Saved memory revision/)).toBeNull();
});
it("reads held memory without allowing edits", async () => {
  const { save } = setup(true);
  await read();
  expect(
    (screen.getByLabelText("Memory for Reader") as HTMLTextAreaElement)
      .disabled,
  ).toBe(true);
  fireEvent.click(screen.getByText("Save memory"));
  expect(save).not.toHaveBeenCalled();
});
it("does not treat an unrelated declaration revision as a memory conflict", async () => {
  const { props, rerender } = setup();
  await read();
  fireEvent.change(screen.getByLabelText("Memory for Reader"), {
    target: { value: "Draft" },
  });
  rerender(<Memory {...props} resident={{ ...resident, revision: 2 }} />);
  expect((screen.getByText("Save memory") as HTMLButtonElement).disabled).toBe(
    false,
  );
});
