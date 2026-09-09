import { expect, it } from "vitest";
import type { Runtimes } from "./client";
import {
  providerName,
  resultEyebrow,
  runtimeLabel,
  runtimeNames,
} from "./runtimes";

const table: Runtimes = {
  default: "codex_subscription",
  configured: ["codex_subscription", "claude_subscription"],
  kinds: {
    codex_subscription: { label: "Codex subscription", live: true },
    claude_subscription: { label: "Claude subscription", live: true },
    codex_mock: { label: "retired Codex mock", live: false },
  },
};

it("names a runtime the way Hearth's own registry names it", () => {
  expect(runtimeLabel(table, "codex_subscription")).toBe("Codex subscription");
  expect(runtimeLabel(table, "claude_subscription")).toBe(
    "Claude subscription",
  );
  expect(providerName(table, "claude_subscription")).toBe("Claude");
});

it("falls back to the kind itself rather than to a provider it guessed", () => {
  expect(runtimeLabel(table, "nothing_hearth_ships")).toBe(
    "nothing_hearth_ships",
  );
  expect(runtimeLabel(table, undefined)).toBe("Codex subscription");
  expect(providerName(table, "nothing_hearth_ships")).toBe(
    "nothing_hearth_ships",
  );
});

it("says which provider produced a result, and says when none did", () => {
  expect(resultEyebrow(table, "codex_subscription")).toBe("CODEX RESULT");
  expect(resultEyebrow(table, "claude_subscription")).toBe("CLAUDE RESULT");
  // A run from a runtime that only ever pretended is not a provider's result.
  expect(resultEyebrow(table, "codex_mock")).toBe("SIMULATED ARTIFACT");
  expect(resultEyebrow(table, "nothing_hearth_ships")).toBe(
    "SIMULATED ARTIFACT",
  );
  expect(resultEyebrow(table, undefined)).toBe("CODEX RESULT");
});

it("lists the household's brains with its own default first", () => {
  expect(runtimeNames(table)).toEqual(["Codex", "Claude"]);
  expect(runtimeNames({ ...table, default: "claude_subscription" })).toEqual([
    "Claude",
    "Codex",
  ]);
});
