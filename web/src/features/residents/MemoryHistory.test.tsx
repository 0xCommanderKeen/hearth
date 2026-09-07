// @vitest-environment jsdom
import { afterEach, expect, it, vi } from "vitest";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { MemoryHistory } from "./MemoryHistory";
import {
  Client,
  type MemoryHistory as History,
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
  memory_revision: 3,
};
const history: History = {
  resident_id: "reader",
  limit: 20,
  offset: 0,
  total: 3,
  revisions: [
    {
      revision: 3,
      sha256: "c".repeat(64),
      size: 42,
      created_at: 1788640000,
      author: "operator",
      run_id: null,
    },
    {
      revision: 2,
      sha256: "b".repeat(64),
      size: 30,
      created_at: 1788639000,
      author: "run",
      run_id: "run-two",
    },
    {
      revision: 1,
      sha256: "a".repeat(64),
      size: 10,
      created_at: 1788638000,
      author: "operator",
      run_id: null,
    },
  ],
};
const openable = (runId: string) => runId === "run-two";
const act = async (operation: () => Promise<unknown>) => {
  try {
    await operation();
  } catch {
    /* App owns error display. */
  }
};
function setup() {
  const client = new Client("synthetic-test-token");
  vi.spyOn(client, "memoryHistory").mockResolvedValue(history);
  const revision = vi
    .spyOn(client, "memoryRevision")
    .mockImplementation(async (_id, number) => ({
      resident_id: "reader",
      revision: number,
      sha256: "a".repeat(64),
      text: number === 2 ? "Kept line\nA run learned this.\n" : "Kept line\n",
    }));
  render(
    <MemoryHistory
      client={client}
      resident={resident}
      busy={false}
      act={act}
      openable={openable}
    />,
  );
  return { client, revision };
}

it("names the author of every revision and links the run that wrote one", async () => {
  setup();
  fireEvent.click(screen.getByText("Read memory history"));
  const list = await screen.findByLabelText("Memory history for Reader");
  expect(list.textContent).toContain("Revision 3 · current");
  expect(screen.getAllByText("Written by the operator")).toHaveLength(2);
  expect(screen.getByText("Written by a run")).toBeTruthy();
  expect(
    (
      screen.getByRole("link", { name: /Open the run/ }) as HTMLAnchorElement
    ).getAttribute("href"),
  ).toBe("#run-run-two");
});

it("shows what one revision changed against the one before it", async () => {
  const { revision } = setup();
  fireEvent.click(screen.getByText("Read memory history"));
  await screen.findByLabelText("Memory history for Reader");
  fireEvent.click(screen.getAllByText("Show what changed")[1]);
  const diff = await screen.findByLabelText("Changes in memory revision 2");
  expect(revision).toHaveBeenCalledWith("reader", 2);
  expect(revision).toHaveBeenCalledWith("reader", 1);
  expect(diff.textContent).toBe(" Kept line\n+A run learned this.\n \n");
});

it("says plainly when a revision repeats the previous text", async () => {
  const { revision } = setup();
  revision.mockImplementation(async (_id, number) => ({
    resident_id: "reader",
    revision: number,
    sha256: "a".repeat(64),
    text: "Unchanged",
  }));
  fireEvent.click(screen.getByText("Read memory history"));
  await screen.findByLabelText("Memory history for Reader");
  fireEvent.click(screen.getAllByText("Show what changed")[0]);
  expect(
    await screen.findByText("Revision 3 repeats the previous text exactly."),
  ).toBeTruthy();
});

it("reads nothing until the operator asks and reports an empty history", async () => {
  const client = new Client("synthetic-test-token");
  const read = vi
    .spyOn(client, "memoryHistory")
    .mockResolvedValue({ ...history, total: 0, revisions: [] });
  render(
    <MemoryHistory
      client={client}
      resident={resident}
      busy={false}
      act={act}
      openable={openable}
    />,
  );
  expect(read).not.toHaveBeenCalled();
  fireEvent.click(screen.getByText("Read memory history"));
  expect(await screen.findByText("No memory has been saved yet.")).toBeTruthy();
});

it("pages by offset so revisions past the first page stay reachable", async () => {
  const client = new Client("synthetic-test-token");
  const oldest = {
    revision: 0,
    sha256: "d".repeat(64),
    size: 4,
    created_at: 1788637000,
    author: "run" as const,
    run_id: "run-gone",
  };
  const read = vi
    .spyOn(client, "memoryHistory")
    .mockImplementation(async (_id, _limit, offset) =>
      offset
        ? { ...history, total: 4, offset, revisions: [oldest] }
        : { ...history, total: 4 },
    );
  render(
    <MemoryHistory
      client={client}
      resident={resident}
      busy={false}
      act={act}
      openable={openable}
    />,
  );
  fireEvent.click(screen.getByText("Read memory history"));
  await screen.findByLabelText("Memory history for Reader");
  expect(read).toHaveBeenLastCalledWith("reader", 20, 0);
  fireEvent.click(screen.getByText("Show older revisions (1 more)"));
  await waitFor(() => expect(read).toHaveBeenLastCalledWith("reader", 20, 3));
  const items = screen
    .getByLabelText("Memory history for Reader")
    .querySelectorAll("li");
  expect(items).toHaveLength(4);
  // The run that wrote the oldest revision is long gone from the task list.
  expect(items[3].textContent).toContain(
    "Run run-gone · no longer in this resident's recent work",
  );
  expect(screen.queryByText(/Show older revisions/)).toBeNull();
});
