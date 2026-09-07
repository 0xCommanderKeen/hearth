// @vitest-environment jsdom
import { afterEach, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { Client, type CatalogSkill } from "../../shared/client";
import { SkillEvidence } from "./SkillEvidence";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});
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
      evaluator_id: "evaluator",
      status: "failed",
      reason: "skill_example_failed",
      assessment: "deterministic_assertions_on_simulated_runs",
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
            simulated: true,
            checks: [
              { assertion: "contains", expected: "12 pears", passed: false },
            ],
          },
        },
      ],
    },
  },
} as CatalogSkill;

it("shows saved example failures and simulated evidence without permitting publication", () => {
  render(
    <SkillEvidence
      client={new Client("token")}
      skill={skill}
      readOnly={false}
      dirty={false}
      onPublished={() => {}}
    />,
  );
  expect(screen.getByText("Output failed contains")).toBeTruthy();
  expect(screen.getByText(/Simulated runs/)).toBeTruthy();
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
