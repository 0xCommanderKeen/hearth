import { expect, it } from "vitest";
import { createPlotAllocator, PLOT_KEY } from "./layout";
function memory(raw: string | null = null) {
  return {
    getItem: () => raw,
    setItem: (_key: string, value: string) => {
      raw = value;
    },
  };
}
it("keeps surviving and returning homes through arrival, reorder, archive and reload", () => {
  const storage = memory();
  const allocate = createPlotAllocator("one", storage);
  const initial = allocate(["a", "b", "c"]);
  expect(allocate(["c", "a", "new"]).filter((p) => p.id !== "new")).toEqual([
    initial[2],
    initial[0],
  ]);
  expect(createPlotAllocator("one", storage)(["b", "a", "c"])).toEqual([
    initial[1],
    initial[0],
    initial[2],
  ]);
  expect(createPlotAllocator("two", storage)(["b"])[0]).toEqual({
    ...initial[0],
    id: "b",
  });
  expect(JSON.parse(storage.getItem()!).epoch).toBe("two");
});
it.each([0, 5, 25, 100])(
  "allocates %i homes without overlap or occupying Townhall/square",
  (count) => {
    const plots = createPlotAllocator("test")(
      Array.from({ length: count }, (_, i) => `r${i}`),
    );
    const all = [
      ...plots,
      { id: "townhall", x: 0, z: -6 },
      { id: "square", x: 0, z: 0 },
    ];
    for (let i = 0; i < all.length; i++)
      for (let j = i + 1; j < all.length; j++) {
        expect(
          Math.abs(all[i].x - all[j].x) >= 6 ||
            Math.abs(all[i].z - all[j].z) >= 6,
        ).toBe(true);
      }
  },
);
it.each([
  "{",
  "null",
  JSON.stringify({ version: 2, epoch: "one", plots: [] }),
  ...[
    [
      ["a", 0],
      ["b", 0],
    ],
    [
      ["a", 0],
      ["a", 1],
    ],
    [["a", -1]],
    [["a", 8192]],
    [["a", 0.5]],
    [["a", "1"]],
    [["a", 1], null],
  ].map((plots) => JSON.stringify({ version: 1, epoch: "one", plots })),
  " ".repeat(262145),
])("rejects malformed preferences as a whole", (raw) => {
  expect(createPlotAllocator("one", memory(raw))(["a", "b"])).toEqual(
    createPlotAllocator("one")(["a", "b"]),
  );
});
it("works with denied storage and writes only bounded identity preferences", () => {
  const denied = {
    getItem: () => {
      throw Error("denied");
    },
    setItem: () => {
      throw Error("quota");
    },
  };
  expect(createPlotAllocator("one", denied)(["a"])).toHaveLength(1);
  const storage = memory();
  const ids = Array.from({ length: 2048 }, (_, i) => `${i}${"é".repeat(120)}`);
  expect(createPlotAllocator("one", storage)(ids)).toHaveLength(2048);
  expect(storage.getItem()).toBeNull();
  createPlotAllocator("one", storage)(["a"]);
  expect(JSON.parse(storage.getItem()!)).toEqual({
    version: 1,
    epoch: "one",
    plots: [["a", 0]],
  });
  expect(PLOT_KEY).toBe("hearth.hamlet.plots.v1");
});
