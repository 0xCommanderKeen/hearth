// @vitest-environment jsdom
import { afterEach, expect, it, vi } from "vitest";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { Skills } from "./Skills";
import {
  Client,
  type Resident,
  type ResidentDeclaration,
} from "../../shared/client";
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});
const resident: Resident = {
  id: "reader",
  name: "Reader",
  purpose: "Synthetic purpose",
  revision: 1,
  daily_limit: 10000,
  presence: "ready",
  pause_reason: null,
};
const saved: ResidentDeclaration = {
  id: "reader",
  revision: 1,
  declaration: {
    name: "Reader",
    purpose: "Synthetic purpose",
    daily_limit: 10000,
    budget_timezone: "Europe/Ljubljana",
    skill_text: "# Original skill",
  },
};
const act = async (operation: () => Promise<unknown>) => {
  try {
    await operation();
  } catch {
    /* Root displays errors; the draft must remain. */
  }
};
function setup(readOnly = false) {
  const client = new Client("synthetic-test-token");
  vi.spyOn(client, "resident").mockResolvedValue(saved);
  const save = vi
    .spyOn(client, "saveResident")
    .mockImplementation(async (_old, skill_text) => ({
      ...saved,
      revision: 2,
      declaration: { ...saved.declaration, skill_text },
    }));
  const props = { client, resident, busy: false, readOnly, act };
  const view = render(<Skills {...props} />);
  return { client, save, props, ...view };
}
async function read() {
  fireEvent.click(screen.getByText("Read resident instructions"));
  await screen.findByLabelText("Resident instructions for Reader");
}
it("saves exact Markdown using the loaded declaration and expected revision", async () => {
  const { save } = setup();
  await read();
  fireEvent.change(screen.getByLabelText("Resident instructions for Reader"), {
    target: { value: "# New\n\nPreserve ž and `code`.\n" },
  });
  fireEvent.click(screen.getByText("Save resident instructions"));
  await waitFor(() =>
    expect(save).toHaveBeenCalledWith(
      saved,
      "# New\n\nPreserve ž and `code`.\n",
    ),
  );
  expect(
    await screen.findByText("Saved resident instructions at revision 2."),
  ).toBeTruthy();
});
it("retains an edited draft when a newer snapshot arrives and requires explicit reload", async () => {
  const { props, rerender, save, client } = setup();
  await read();
  fireEvent.change(screen.getByLabelText("Resident instructions for Reader"), {
    target: { value: "My draft" },
  });
  rerender(<Skills {...props} resident={{ ...resident, revision: 2 }} />);
  expect(
    (
      screen.getByLabelText(
        "Resident instructions for Reader",
      ) as HTMLTextAreaElement
    ).value,
  ).toBe("My draft");
  expect(screen.getByText(/The resident changed/)).toBeTruthy();
  fireEvent.click(screen.getByText("Save resident instructions"));
  expect(save).not.toHaveBeenCalled();
  vi.mocked(client.resident).mockResolvedValue({
    ...saved,
    revision: 2,
    declaration: { ...saved.declaration, skill_text: "Their revision" },
  });
  fireEvent.click(screen.getByText("Load current revision (replace draft)"));
  await waitFor(() =>
    expect(
      (
        screen.getByLabelText(
          "Resident instructions for Reader",
        ) as HTMLTextAreaElement
      ).value,
    ).toBe("Their revision"),
  );
});
it("keeps the draft and original revision after a failed or ambiguous save", async () => {
  const { save } = setup();
  await read();
  save.mockRejectedValue(new Error("Lost response"));
  fireEvent.change(screen.getByLabelText("Resident instructions for Reader"), {
    target: { value: "Unconfirmed draft" },
  });
  fireEvent.click(screen.getByText("Save resident instructions"));
  await waitFor(() => expect(save).toHaveBeenCalledTimes(1));
  expect(
    (
      screen.getByLabelText(
        "Resident instructions for Reader",
      ) as HTMLTextAreaElement
    ).value,
  ).toBe("Unconfirmed draft");
  expect(screen.getByText(/Editing revision 1/)).toBeTruthy();
  expect(
    screen.queryByText(/Saved resident instructions at revision/),
  ).toBeNull();
});
it("allows reading a held copy but prevents editing and saving", async () => {
  const { save } = setup(true);
  await read();
  expect(
    (
      screen.getByLabelText(
        "Resident instructions for Reader",
      ) as HTMLTextAreaElement
    ).disabled,
  ).toBe(true);
  fireEvent.click(screen.getByText("Save resident instructions"));
  expect(save).not.toHaveBeenCalled();
});
it("the client sends all loaded fields and a conditional revision", async () => {
  const fetch = vi
    .spyOn(globalThis, "fetch")
    .mockResolvedValue(new Response(JSON.stringify(saved), { status: 200 }));
  await new Client("synthetic-test-token").saveResident(saved, "Edited");
  const [path, options] = fetch.mock.calls[0];
  expect(path).toBe("/api/residents/reader");
  expect(options?.method).toBe("PUT");
  expect(JSON.parse(options?.body as string)).toEqual({
    ...saved.declaration,
    skill_text: "Edited",
    expected_revision: 1,
  });
});
