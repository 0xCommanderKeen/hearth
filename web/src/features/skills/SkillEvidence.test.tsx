// @vitest-environment jsdom
import { afterEach, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { Client, type CatalogSkill, type Resident } from "../../shared/client";
import { SkillEvidence } from "./SkillEvidence";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});
const RESIDENTS = [
  {
    id: "karen",
    name: "Karen",
    purpose: "Runs the household",
    revision: 1,
    daily_limit: 1000000,
    presence: "idle",
    pause_reason: null,
    lifecycle: { resident_id: "karen", state: "ready" as const },
  },
] as Resident[];
const skill = {
  skill_id: "reports",
  revision: 1,
  name: "Concise reporting",
  description: "Fictional reports",
  instructions: "A narrow reporting procedure",
  status: "draft",
  created_by: "karen",
  created_at: 1,
  edited_by: "karen",
  edited_at: 1,
  sha256: "candidate-hash",
  authoring: {
    examples: [],
    manifest_sha256: "examples-hash",
    structure: { checker: "structure-v1", passed: true, reasons: [] },
    publication: null,
    validation: {
      validation_id: "checks",
      skill_id: "reports",
      candidate_revision: 1,
      candidate_sha256: "candidate-hash",
      resident_id: "karen",
      memory_revision: 4,
      status: "failed",
      reason: "skill_example_failed",
      assessment: "deterministic_assertions_on_model_runs",
      cases: [
        {
          kind: "normal",
          position: 0,
          task_id: "task",
          run_id: "run",
          input_set_id: "notes",
          input_revision: 1,
          input_sha256: "notes-hash",
          result: {
            passed: false,
            reasons: ["Output failed contains"],
            artifact_id: "artifact",
            artifact_sha256: "output-hash",
            actual_cost: 2000,
            checks: [
              { assertion: "contains", expected: "12 pears", passed: false },
            ],
          },
        },
      ],
    },
  },
} as CatalogSkill;

it("shows saved example failures and evidence without permitting publication", () => {
  render(
    <SkillEvidence
      client={new Client("token")}
      skill={skill}
      residents={RESIDENTS}
      readOnly={false}
      dirty={false}
      onPublished={() => {}}
    />,
  );
  expect(screen.getByText("Output failed contains")).toBeTruthy();
  expect(screen.getByText(/Model runs/)).toBeTruthy();
  expect(
    screen.getByRole("link", { name: /Inspect run/ }).getAttribute("href"),
  ).toBe("#run-run");
  expect(
    screen.queryByRole("button", { name: "Publish validated revision" }),
  ).toBeNull();
});

it("retains the exact publication request after an unconfirmed reply", async () => {
  const client = new Client("token");
  const passed = structuredClone(skill);
  passed.authoring!.validation!.status = "passed";
  passed.authoring!.validation!.reason = null;
  passed.authoring!.validation!.cases[0].result!.passed = true;
  const publish = vi
    .spyOn(client, "publishSkill")
    .mockRejectedValue(new Error("lost reply"));
  render(
    <SkillEvidence
      client={client}
      skill={passed}
      residents={RESIDENTS}
      readOnly={false}
      dirty={false}
      onPublished={() => {}}
    />,
  );
  fireEvent.click(
    screen.getByRole("button", { name: "Publish validated revision" }),
  );
  await screen.findByText(/Publication is unconfirmed/);
  fireEvent.click(
    screen.getByRole("button", { name: "Retry pending publication" }),
  );
  await screen.findByText(/Publication is unconfirmed/);
  expect(publish.mock.calls[1]).toEqual(publish.mock.calls[0]);
});

it("asks which resident runs the examples and sends the one chosen", async () => {
  const client = new Client("token");
  const unevaluated = structuredClone(skill);
  unevaluated.authoring!.validation = null;
  const validate = vi
    .spyOn(client, "validateSkill")
    .mockResolvedValue(skill.authoring!.validation!);
  render(
    <SkillEvidence
      client={client}
      skill={unevaluated}
      residents={[
        ...RESIDENTS,
        { ...RESIDENTS[0], id: "reporter", name: "Reporter" },
      ]}
      readOnly={false}
      dirty={false}
      onPublished={() => {}}
    />,
  );
  fireEvent.change(screen.getByLabelText("Resident that runs the examples"), {
    target: { value: "reporter" },
  });
  fireEvent.click(
    screen.getByRole("button", { name: "Run two example checks" }),
  );
  await vi.waitFor(() => expect(validate).toHaveBeenCalled());
  expect(validate.mock.calls[0]).toEqual(["reports", 1, 100000, "reporter"]);
});
