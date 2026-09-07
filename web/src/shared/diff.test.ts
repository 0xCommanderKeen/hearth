import { expect, it } from "vitest";
import { diffLines } from "./diff";

it("keeps unchanged lines and marks what one revision added and removed", () => {
  const lines = diffLines("one\ntwo\nthree", "one\ntwo changed\nthree");
  expect(lines).toEqual([
    { kind: "same", text: "one" },
    { kind: "removed", text: "two" },
    { kind: "added", text: "two changed" },
    { kind: "same", text: "three" },
  ]);
});

it("reports no lines when a revision repeats the previous text exactly", () => {
  expect(diffLines("same\ntext", "same\ntext")).toEqual([]);
});

it("shows the first revision against an empty note as a replaced empty line", () => {
  expect(diffLines("", "first")).toEqual([
    { kind: "removed", text: "" },
    { kind: "added", text: "first" },
  ]);
});

it("falls back to whole blocks rather than pretending to match huge notes", () => {
  const before = Array.from({ length: 500 }, (_, index) => `before ${index}`);
  const after = Array.from({ length: 500 }, (_, index) => `after ${index}`);
  const lines = diffLines(before.join("\n"), after.join("\n"));
  expect(lines).toHaveLength(1000);
  expect(lines[0]).toEqual({ kind: "removed", text: "before 0" });
  expect(lines[500]).toEqual({ kind: "added", text: "after 0" });
});
