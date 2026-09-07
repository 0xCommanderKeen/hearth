// @vitest-environment jsdom
import { afterEach, expect, it, vi } from "vitest";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { Journal } from "./Journal";
import {
  Client,
  type ResidentJournal,
  type Resident,
} from "../../shared/client";

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
};
const journal: ResidentJournal = {
  resident_id: "reader",
  limit: 20,
  offset: 0,
  total: 2,
  entries: [
    {
      resident_id: "reader",
      sequence: 2,
      run_id: "run-two",
      at: 1788640000,
      text: "Day 2: reported the same synthetic pears.",
    },
    {
      resident_id: "reader",
      sequence: 1,
      run_id: "run-one",
      at: 1788550000,
      text: "Day 1: reported 12 pears Monday.",
    },
  ],
};
const openable = (runId: string) => runId !== "run-one";
const act = async (operation: () => Promise<unknown>) => {
  try {
    await operation();
  } catch {
    /* App owns error display. */
  }
};

it("lists entries newest first, each linked to the run that wrote it", async () => {
  const client = new Client("synthetic-test-token");
  const read = vi.spyOn(client, "journal").mockResolvedValue(journal);
  render(
    <Journal
      client={client}
      resident={resident}
      busy={false}
      act={act}
      openable={openable}
    />,
  );
  expect(read).not.toHaveBeenCalled();
  fireEvent.click(screen.getByText("Read journal"));
  const list = await screen.findByLabelText("Journal for Reader");
  const entries = list.querySelectorAll("li");
  expect(entries[0].textContent).toContain("Entry 2");
  expect(entries[0].textContent).toContain(
    "Day 2: reported the same synthetic pears.",
  );
  expect(entries[1].textContent).toContain("Entry 1");
  // Only the run still on the page is linked; the older one is named, not offered.
  expect(
    [...list.querySelectorAll("a")].map((link) => link.getAttribute("href")),
  ).toEqual(["#run-run-two"]);
  expect(entries[1].textContent).toContain(
    "Run run-one · no longer in this resident's recent work",
  );
});

it("says a journal is empty rather than inventing an entry", async () => {
  const client = new Client("synthetic-test-token");
  vi.spyOn(client, "journal").mockResolvedValue({
    ...journal,
    total: 0,
    entries: [],
  });
  render(
    <Journal
      client={client}
      resident={resident}
      busy={false}
      act={act}
      openable={openable}
    />,
  );
  fireEvent.click(screen.getByText("Read journal"));
  expect(
    await screen.findByText("No run has written an entry yet."),
  ).toBeTruthy();
  expect(screen.queryByLabelText("Journal for Reader")).toBeNull();
});

it("pages by offset so entries past the first page stay reachable", async () => {
  const client = new Client("synthetic-test-token");
  const older = {
    resident_id: "reader",
    sequence: 0,
    run_id: "run-zero",
    at: 1788400000,
    text: "Day 0: the first report.",
  };
  const read = vi
    .spyOn(client, "journal")
    .mockImplementation(async (_id, _limit, offset) =>
      offset
        ? { ...journal, total: 3, offset, entries: [older] }
        : { ...journal, total: 3 },
    );
  render(
    <Journal
      client={client}
      resident={resident}
      busy={false}
      act={act}
      openable={openable}
    />,
  );
  fireEvent.click(screen.getByText("Read journal"));
  await screen.findByLabelText("Journal for Reader");
  expect(read).toHaveBeenLastCalledWith("reader", 20, 0);
  fireEvent.click(screen.getByText("Show older entries (1 more)"));
  await waitFor(() => expect(read).toHaveBeenLastCalledWith("reader", 20, 2));
  const entries = screen
    .getByLabelText("Journal for Reader")
    .querySelectorAll("li");
  expect(entries).toHaveLength(3);
  expect(entries[2].textContent).toContain("Day 0: the first report.");
  expect(screen.queryByText(/Show older entries/)).toBeNull();
});
