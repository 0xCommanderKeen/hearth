import type { Group } from "three";
export function visualIdentity(id: string): {
  accent: string;
  roof: string;
  detail: number;
  silhouette: number;
  skin: string;
  trim: string;
};
export function createArtKit(): {
  building(model: {
    id: string;
    kind: string;
    width?: number;
    depth?: number;
  }): Group;
  agent(model: { id: string; letter?: boolean }): Group;
  garden(): Group;
  tree(seed: number): Group;
  dispose(): void;
};
