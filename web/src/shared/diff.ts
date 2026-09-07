export type DiffLine = { kind: "same" | "added" | "removed"; text: string };

// A memory note may be 128 KiB, so the exact comparison is bounded. Beyond this
// many differing lines the panel shows the whole block as removed then added
// rather than pretending to a line-by-line match it did not compute.
const MAX_COMPARED = 400;

function trimmed(before: string[], after: string[]) {
  let head = 0;
  while (
    head < before.length &&
    head < after.length &&
    before[head] === after[head]
  )
    head += 1;
  let tail = 0;
  while (
    tail < before.length - head &&
    tail < after.length - head &&
    before[before.length - 1 - tail] === after[after.length - 1 - tail]
  )
    tail += 1;
  return { head, tail };
}

function longestCommon(before: string[], after: string[]): DiffLine[] {
  const table: number[][] = Array.from({ length: before.length + 1 }, () =>
    new Array(after.length + 1).fill(0),
  );
  for (let i = before.length - 1; i >= 0; i -= 1)
    for (let j = after.length - 1; j >= 0; j -= 1)
      table[i][j] =
        before[i] === after[j]
          ? table[i + 1][j + 1] + 1
          : Math.max(table[i + 1][j], table[i][j + 1]);
  const result: DiffLine[] = [];
  let i = 0;
  let j = 0;
  while (i < before.length && j < after.length) {
    if (before[i] === after[j]) {
      result.push({ kind: "same", text: before[i] });
      i += 1;
      j += 1;
    } else if (table[i + 1][j] >= table[i][j + 1]) {
      result.push({ kind: "removed", text: before[i] });
      i += 1;
    } else {
      result.push({ kind: "added", text: after[j] });
      j += 1;
    }
  }
  for (; i < before.length; i += 1)
    result.push({ kind: "removed", text: before[i] });
  for (; j < after.length; j += 1)
    result.push({ kind: "added", text: after[j] });
  return result;
}

/** Compare two revisions of one note line by line. Identical text produces no lines. */
export function diffLines(before: string, after: string): DiffLine[] {
  if (before === after) return [];
  const left = before.split("\n");
  const right = after.split("\n");
  const { head, tail } = trimmed(left, right);
  const changedLeft = left.slice(head, left.length - tail);
  const changedRight = right.slice(head, right.length - tail);
  const middle =
    changedLeft.length > MAX_COMPARED || changedRight.length > MAX_COMPARED
      ? [
          ...changedLeft.map((text): DiffLine => ({ kind: "removed", text })),
          ...changedRight.map((text): DiffLine => ({ kind: "added", text })),
        ]
      : longestCommon(changedLeft, changedRight);
  return [
    ...left.slice(0, head).map((text): DiffLine => ({ kind: "same", text })),
    ...middle,
    ...left
      .slice(left.length - tail)
      .map((text): DiffLine => ({ kind: "same", text })),
  ];
}
