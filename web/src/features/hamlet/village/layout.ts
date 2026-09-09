// Disposable placement preferences. See ADR 0017; this module never receives records.
export const PLOT_KEY = "hearth.hamlet.plots.v1";
const MAX_ENTRIES = 2048;
const MAX_SLOT = 8192;
type Saved = { version: 1; epoch: string; plots: [string, number][] };
type Storage = Pick<globalThis.Storage, "getItem" | "setItem">;
export type Plot = { id: string; x: number; z: number };
export function plotPosition(slot: number): { x: number; z: number } {
  // Square spiral, skipping the square (0,0) and Townhall (0,-1).
  let index = -1;
  for (let ring = 1; ; ring++) {
    for (let side = 0; side < 4; side++) {
      for (let step = 0; step < ring * 2; step++) {
        const [x, z] =
          side === 0
            ? [-ring + step, -ring]
            : side === 1
              ? [ring, -ring + step]
              : side === 2
                ? [ring - step, ring]
                : [-ring, ring - step];
        if (x === 0 && z === -1) continue;
        if (++index === slot) return { x: x * 6, z: z * 6 };
      }
    }
  }
}
export function createPlotAllocator(epoch: string, storage?: Storage) {
  let slots = new Map<string, number>();
  try {
    const raw = storage?.getItem(PLOT_KEY);
    if (raw && new TextEncoder().encode(raw).byteLength <= 262144) {
      const saved: Saved = JSON.parse(raw);
      if (
        saved.version === 1 &&
        saved.epoch === epoch &&
        epoch.length <= 256 &&
        Array.isArray(saved.plots) &&
        saved.plots.length <= MAX_ENTRIES
      ) {
        const used = new Set<number>();
        for (const pair of saved.plots) {
          if (!Array.isArray(pair) || pair.length !== 2)
            throw Error("invalid plot");
          const [id, slot] = pair;
          if (
            typeof id !== "string" ||
            !id.length ||
            id.length > 256 ||
            slots.has(id) ||
            !Number.isInteger(slot) ||
            slot < 0 ||
            slot >= MAX_SLOT ||
            used.has(slot)
          )
            throw Error("invalid plot");
          slots.set(id, slot);
          used.add(slot);
        }
      }
    }
  } catch {
    slots = new Map();
  }
  return (ids: string[]): Plot[] => {
    const used = new Set(slots.values());
    let next = 0;
    for (const id of [...ids].sort()) {
      if (slots.has(id)) continue;
      while (used.has(next)) next++;
      slots.set(id, next);
      used.add(next);
    }
    if (
      epoch.length > 0 &&
      epoch.length <= 256 &&
      slots.size <= MAX_ENTRIES &&
      [...slots].every(
        ([id, slot]) => id.length > 0 && id.length <= 256 && slot < MAX_SLOT,
      )
    ) {
      try {
        const raw = JSON.stringify({ version: 1, epoch, plots: [...slots] });
        if (new TextEncoder().encode(raw).byteLength <= 262144)
          storage?.setItem(PLOT_KEY, raw);
      } catch {
        /* Preferences are optional. */
      }
    }
    return ids.map((id) => ({ id, ...plotPosition(slots.get(id)!) }));
  };
}
