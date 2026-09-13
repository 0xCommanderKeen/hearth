import * as THREE from "three";
import { createArtKit, visualIdentity } from "./art.js";

const cache = new Map<string, string>();
const pending = new Map<string, Set<(url: string | null) => void>>();
let frame: number | null = null;
let renderer: THREE.WebGLRenderer | undefined;
let kit: ReturnType<typeof createArtKit> | undefined;
let scene: THREE.Scene | undefined;
let camera: THREE.OrthographicCamera | undefined;
// Limit synchronous rendering/PNG encoding; share one context until the queue drains.
const PORTRAITS_PER_FRAME = 4;
function release() {
  if (frame !== null) cancelAnimationFrame(frame);
  frame = null;
  kit?.dispose();
  renderer?.dispose();
  renderer?.forceContextLoss();
  renderer = undefined;
  kit = undefined;
  scene = undefined;
  camera = undefined;
}
function schedule() {
  if (frame === null) frame = requestAnimationFrame(renderBatch);
}
export function requestPortrait(
  id: string,
  receive: (url: string | null) => void,
) {
  const cached = cache.get(id);
  if (cached) {
    cache.delete(id);
    cache.set(id, cached);
    receive(cached);
    return () => {};
  }
  const callbacks = pending.get(id) ?? new Set();
  callbacks.add(receive);
  pending.set(id, callbacks);
  schedule();
  return () => {
    callbacks.delete(receive);
    if (!callbacks.size && pending.get(id) === callbacks) pending.delete(id);
    if (!pending.size) release();
  };
}
function renderBatch() {
  frame = null;
  if (!pending.size) {
    release();
    return;
  }
  try {
    if (!renderer) {
      renderer = new THREE.WebGLRenderer({
        antialias: true,
        preserveDrawingBuffer: true,
      });
      renderer.setSize(256, 256);
      kit = createArtKit();
      scene = new THREE.Scene();
      scene.name = "resident-portrait";
      scene.add(new THREE.HemisphereLight("#fff5df", "#748267", 2.8));
      const light = new THREE.DirectionalLight("#fff0cd", 3);
      light.position.set(-2, 4, 5);
      scene.add(light);
      camera = new THREE.OrthographicCamera(-0.49, 0.49, 0.49, -0.49, 0.1, 10);
      camera.position.set(0.65, 0.95, 3);
      camera.lookAt(0, 0.64, 0);
    }
    for (const [id, callbacks] of [...pending.entries()].slice(
      0,
      PORTRAITS_PER_FRAME,
    )) {
      const figure = kit!.agent({ id });
      scene!.background = new THREE.Color(visualIdentity(id).accent).lerp(
        new THREE.Color("#f8ebcd"),
        0.8,
      );
      scene!.add(figure);
      renderer.render(scene!, camera!);
      const url = renderer.domElement.toDataURL("image/png");
      scene!.remove(figure);
      cache.set(id, url);
      while (cache.size > 128) cache.delete(cache.keys().next().value!);
      pending.delete(id);
      callbacks.forEach((receive) => receive(url));
    }
  } catch {
    const failed = [...pending.values()];
    pending.clear();
    failed.forEach((callbacks) =>
      callbacks.forEach((receive) => receive(null)),
    );
  }
  if (pending.size) schedule();
  else release();
}
