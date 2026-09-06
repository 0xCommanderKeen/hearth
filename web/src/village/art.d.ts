import type { Group } from "three";
export function createArtKit(): {
  building(model: {
    id: string;
    kind: string;
    width?: number;
    depth?: number;
  }): Group;
  agent(model: { id: string }): Group;
  tree(seed: number): Group;
  dispose(): void;
};
