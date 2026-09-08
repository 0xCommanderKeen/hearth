// @vitest-environment jsdom
import { afterEach, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
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

it("opens a shut door and says which declaration revision now carries it", async () => {
  const client = new Client("synthetic-test");
  vi.spyOn(client, "letters").mockResolvedValue(post);
  const turn = vi.spyOn(client, "setLettersDoor").mockResolvedValue({
    id: "karen",
    revision: 2,
    declaration: {
      name: "Karen",
      purpose: "Runs the household",
      daily_limit: 5_000_000,
      budget_timezone: "UTC",
      skill_text: "# Karen",
      letters_accept: true,
    },
  });
  mount(karen, client);
  expect(
    screen.getByLabelText("Whether Karen can be written to").textContent,
  ).toContain("Karen accepts no letters");

  fireEvent.click(screen.getByText("Open the door"));

  // The door alone travels, at the revision this page saw: nothing else is restated.
  await screen.findByText(/declaration revision 2/);
  expect(turn.mock.calls[0]).toEqual(["karen", true, 1]);
  expect(
    screen.getByLabelText("Whether Karen can be written to").textContent,
  ).toContain("Karen accepts letters.");
  expect(screen.getByRole("status").textContent).toContain(
    "Karen accepts letters, at declaration revision 2.",
  );
  // The same control now shuts it again.
  expect(screen.getByText("Shut the door")).toBeTruthy();
});

it("turns the door again against the revision the last turn produced", async () => {
  const client = new Client("synthetic-test");
  vi.spyOn(client, "letters").mockResolvedValue(post);
  const turn = vi
    .spyOn(client, "setLettersDoor")
    .mockImplementation(async (id, accept, revision) => ({
      id,
      revision: revision + 1,
      declaration: {
        name: "Karen",
        purpose: "Runs the household",
        daily_limit: 5_000_000,
        budget_timezone: "UTC",
        skill_text: "# Karen",
        letters_accept: accept,
      },
    }));
  // The snapshot never advances here: this is the case where the state refresh after a
  // save failed, so the resident prop still carries the revision the door started at.
  mount(karen, client);
  fireEvent.click(screen.getByText("Open the door"));
  await screen.findByText(/declaration revision 2/);

  fireEvent.click(screen.getByText("Shut the door"));
  await screen.findByText(/declaration revision 3/);
  // The second turn names revision 2, not the stale 1 the snapshot still reports —
  // otherwise it is refused as a conflict while the door is in fact open.
  expect(turn.mock.calls[1]).toEqual(["karen", false, 2]);
  expect(
    screen.getByLabelText("Whether Karen can be written to").textContent,
  ).toContain("Karen accepts no letters");
});

it("shows a refused door where it was written rather than as a door that did not move", async () => {
  const client = new Client("synthetic-test");
  vi.spyOn(client, "letters").mockResolvedValue(post);
  vi.spyOn(client, "setLettersDoor").mockRejectedValue(
    new RequestError(409, "revision conflict"),
  );
  mount(karen, client);
  fireEvent.click(screen.getByText("Open the door"));

  const refusal = await screen.findByRole("alert");
  expect(refusal.textContent).toContain("revision conflict");
  expect(refusal.textContent).toContain("It stands as it did.");
  // The door reads exactly as it did, and no revision is claimed.
  expect(
    screen.getByLabelText("Whether Karen can be written to").textContent,
  ).toContain("Karen accepts no letters");
  expect(screen.getByText("Open the door")).toBeTruthy();
  expect(screen.queryByText(/declaration revision/)).toBeNull();
});

it("cannot turn a door on a restored copy", () => {
  const client = new Client("synthetic-test");
  const turn = vi.spyOn(client, "setLettersDoor");
  render(
    <Letters
      client={client}
      resident={karen}
      residents={[reporter, karen]}
      busy={false}
      readOnly
      act={act}
      openable={openable}
    />,
  );
  const button = screen.getByText("Open the door") as HTMLButtonElement;
  expect(button.disabled).toBe(true);
  fireEvent.click(button);
  expect(turn).not.toHaveBeenCalled();
});

const receipt = {
  command_id: "one",
  resident_id: "reporter",
  task_id: "queued-task",
  sender: "operator",
  root_task_id: "queued-task",
  depth: 1,
  expires_at: 1_788_726_400,
  status: "queued",
};

function write(letter = "Name one fact about the orchard.") {
  fireEvent.change(screen.getByLabelText("What it is about"), {
    target: { value: "One question" },
  });
  fireEvent.change(screen.getByLabelText("The request"), {
    target: { value: letter },
  });
  fireEvent.click(
    screen.getByText(/^(Send the letter|Retry the letter)$/) as HTMLElement,
  );
}

it("releases the command when Hearth refuses, because a refused letter writes nothing", async () => {
  const client = new Client("synthetic-test");
  vi.spyOn(client, "letters").mockResolvedValue(post);
  const send = vi
    .spyOn(client, "sendLetter")
    .mockRejectedValueOnce(new RequestError(409, "letters not accepted"))
    .mockResolvedValueOnce(receipt);
  mount(reporter, client);
  write();
  const refusal = await screen.findByRole("alert");
  expect(refusal.textContent).toContain("letters not accepted");
  expect(refusal.textContent).toContain("Nothing was written.");

  // Hearth answered, so there is no accepted command to replay: the operator may
  // correct the letter and write a new one.
  expect(screen.getByText("Send the letter")).toBeTruthy();
  write("Name one fact about Monday's pears.");
  await screen.findByText(/Letter queued as task queued-task/);
  expect(send.mock.calls[0][1]).not.toBe(send.mock.calls[1][1]);
  expect(send.mock.calls[1][2]).toEqual({
    title: "One question",
    detail: "Name one fact about Monday's pears.",
  });
});

it("freezes the command when no answer arrives, so a retry is never a second letter", async () => {
  const client = new Client("synthetic-test");
  vi.spyOn(client, "letters").mockResolvedValue(post);
  const send = vi
    .spyOn(client, "sendLetter")
    .mockRejectedValueOnce(new TypeError("Failed to fetch"))
    .mockResolvedValueOnce(receipt);
  mount(reporter, client);
  write();
  await screen.findByRole("alert");
  // Hearth may be holding this letter, so the same command is retried and the text it
  // carried cannot be edited underneath it.
  expect(screen.getByLabelText("The request")).toHaveProperty("disabled", true);
  fireEvent.click(screen.getByText("Retry the letter"));
  await screen.findByText(/Letter queued as task queued-task/);
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
