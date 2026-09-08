// @vitest-environment jsdom
import { afterEach, expect, it, vi } from "vitest";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { Letters } from "./Letters";
import { Lineage } from "./Lineage";
import {
  Client,
  RequestError,
  type Letter,
  type Resident,
  type ResidentLetters,
} from "../../shared/client";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

const reporter: Resident = {
  id: "reporter",
  name: "Reporter",
  purpose: "Answers one question about the orchard",
  revision: 1,
  daily_limit: 50_000,
  presence: "ready",
  pause_reason: null,
  letters_accept: 1,
};
const karen: Resident = {
  id: "karen",
  name: "Karen",
  purpose: "Runs the household",
  revision: 1,
  daily_limit: 5_000_000,
  presence: "ready",
  pause_reason: null,
  letters_accept: 0,
};

function letter(over: Partial<Letter> = {}): Letter {
  return {
    task_id: "letter-task",
    title: "One question",
    sender: "karen",
    sender_resident_id: "karen",
    sender_run_id: "run-karen",
    recipient_resident_id: "reporter",
    parent_task_id: "karen-task",
    root_task_id: "karen-task",
    depth: 1,
    created_at: 1_788_640_000,
    expires_at: 1_788_726_400,
    status: "succeeded",
    state: "replied",
    settled_at: 1_788_640_043,
    instruction: "Name one fact about the orchard.",
    instruction_truncated: false,
    reply: {
      resident_id: "reporter",
      run_id: "run-reporter",
      written_at: 1_788_640_040,
      text: "The pear harvest was logged under OR-C9IX-BBTI.",
    },
    ...over,
  };
}

const post: ResidentLetters = {
  resident_id: "reporter",
  limit: 20,
  offset: 0,
  inbox: [
    letter(),
    letter({
      task_id: "stale-task",
      title: "A question nobody started",
      state: "expired",
      settled_at: 1_788_726_400,
      reply: null,
    }),
  ],
  sent: [
    letter({
      task_id: "sent-task",
      title: "A question the reporter asked",
      sender: "reporter",
      sender_resident_id: "reporter",
      recipient_resident_id: "karen",
      state: "unanswered",
      reply: null,
    }),
  ],
};

const act = async (operation: () => Promise<unknown>) => {
  try {
    await operation();
  } catch {
    /* App owns error display. */
  }
};
const openable = (runId: string) => runId === "run-reporter";

function mount(resident = reporter, client = new Client("synthetic-test")) {
  render(
    <Letters
      client={client}
      resident={resident}
      residents={[reporter, karen]}
      busy={false}
      readOnly={false}
      act={act}
      openable={openable}
    />,
  );
  return client;
}

it("lists what reached this resident and what it wrote, each in one honest state", async () => {
  const client = new Client("synthetic-test");
  const read = vi.spyOn(client, "letters").mockResolvedValue(post);
  mount(reporter, client);
  expect(read).not.toHaveBeenCalled();
  fireEvent.click(screen.getByText("Read letters"));

  const inbox = await screen.findByLabelText("Letters to Reporter");
  const received = inbox.querySelectorAll("li");
  expect(received[0].textContent).toContain("One question");
  expect(received[0].textContent).toContain("Answered");
  expect(received[0].textContent).toContain("From Karen");
  expect(received[0].textContent).toContain(
    "The pear harvest was logged under OR-C9IX-BBTI.",
  );
  // The answer links to the run that wrote it, which is the reply link.
  expect(
    (received[0].querySelector("a") as HTMLAnchorElement).getAttribute("href"),
  ).toBe("#run-run-reporter");
  // A letter nobody started says so rather than going quiet.
  expect(received[1].textContent).toContain(
    "Went stale before anyone started it",
  );
  expect(received[1].textContent).toContain("No answer was ever written.");

  const sent = screen.getByLabelText("Letters from Reporter");
  expect(sent.querySelectorAll("li")[0].textContent).toContain("To Karen");
  expect(sent.querySelectorAll("li")[0].textContent).toContain(
    "Worked, never answered",
  );
});

it("names the two states that quietly stop an answer: a shut door and a thin day", () => {
  vi.spyOn(Client.prototype, "letters").mockResolvedValue(post);
  mount(karen);
  const note = screen.getByLabelText("Whether Karen can be written to");
  expect(note.textContent).toContain("Karen accepts no letters");
  expect(note.textContent).toContain("letters not accepted");
  expect(note.textContent).toContain("$5.00");
  cleanup();
  mount(reporter);
  const open = screen.getByLabelText("Whether Reporter can be written to");
  expect(open.textContent).toContain("Reporter accepts letters.");
  // The reporter's whole day is below what one answering run cost on the real journey,
  // which is exactly the refusal the operator cannot otherwise see.
  expect(open.textContent).toContain("$0.05");
  expect(open.textContent).toContain(
    "refuses the answer at allocation and leaves the letter unanswered",
  );
});

it("writes one letter with the operator's own hand and keeps its command on a refusal", async () => {
  const client = new Client("synthetic-test");
  vi.spyOn(client, "letters").mockResolvedValue(post);
  const send = vi
    .spyOn(client, "sendLetter")
    .mockRejectedValueOnce(new RequestError(409, "letters not accepted"))
    .mockResolvedValueOnce({
      command_id: "one",
      resident_id: "reporter",
      task_id: "queued-task",
      sender: "operator",
      root_task_id: "queued-task",
      depth: 1,
      expires_at: 1_788_726_400,
      status: "queued",
    });
  mount(reporter, client);
  fireEvent.change(screen.getByLabelText("What it is about"), {
    target: { value: "One question" },
  });
  fireEvent.change(screen.getByLabelText("The request"), {
    target: { value: "Name one fact about the orchard." },
  });
  fireEvent.click(screen.getByText("Send the letter"));
  const refusal = await screen.findByRole("alert");
  expect(refusal.textContent).toContain("letters not accepted");
  expect(refusal.textContent).toContain("Nothing was written.");

  // The retry is the same letter, under the same command, never a second one.
  fireEvent.click(screen.getByText("Retry the letter"));
  await waitFor(() =>
    expect(screen.getByRole("status").textContent).toContain("queued-task"),
  );
  expect(send.mock.calls[0][1]).toBe(send.mock.calls[1][1]);
  expect(send.mock.calls[1][2]).toEqual({
    title: "One question",
    detail: "Name one fact about the orchard.",
  });
});

it("draws the chain root first, ending at the task being read", () => {
  render(
    <Lineage
      hops={[
        {
          task_id: "karen-task",
          resident_id: "karen",
          resident_name: "Karen",
          title: "Answer the orchard question",
          state: null,
          depth: null,
          sender: null,
          sender_name: null,
        },
        {
          task_id: "letter-task",
          resident_id: "reporter",
          resident_name: "Reporter",
          title: "One question",
          state: "replied",
          depth: 1,
          sender: "karen",
          sender_name: "Karen",
        },
      ]}
    />,
  );
  const chain = screen.getByLabelText("Lineage · Karen → Reporter");
  const hops = chain.querySelectorAll("li");
  expect(hops[0].textContent).toContain("Answer the orchard question");
  expect(hops[0].textContent).toContain("where the chain started");
  expect(hops[0].getAttribute("aria-current")).toBeNull();
  expect(hops[1].textContent).toContain("written by Karen");
  expect(hops[1].textContent).toContain("Answered");
  expect(hops[1].getAttribute("aria-current")).toBe("step");
  expect(
    (hops[1].querySelector("a") as HTMLAnchorElement).getAttribute("href"),
  ).toBe("#residents/reporter");
});

it("draws nothing for a task that is the start of its own chain", () => {
  const { container } = render(<Lineage hops={[]} />);
  expect(container.innerHTML).toBe("");
});
