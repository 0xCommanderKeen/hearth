import * as THREE from "three";
import { createArtKit, visualIdentity } from "./art.js";

// Only still images survive a batch; no portrait keeps a WebGL context alive.
const cache = new Map<string, string>();
const pending = new Map<string, Set<(url: string | null) => void>>();
let scheduled = false;
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
  if (!scheduled) {
    scheduled = true;
    requestAnimationFrame(renderBatch);
  }
  return () => {
    callbacks.delete(receive);
    if (!callbacks.size) pending.delete(id);
  };
}
function renderBatch() {
  scheduled = false;
  if (!pending.size) return;
  const batch = [...pending.entries()];
  pending.clear();
  let renderer: THREE.WebGLRenderer | undefined;
  let kit: ReturnType<typeof createArtKit> | undefined;
  try {
    renderer = new THREE.WebGLRenderer({
      antialias: true,
      preserveDrawingBuffer: true,
    });
    renderer.setSize(256, 256);
    kit = createArtKit();
    const scene = new THREE.Scene();
    scene.name = "resident-portrait";
    scene.add(new THREE.HemisphereLight("#fff5df", "#748267", 2.8));
    const light = new THREE.DirectionalLight("#fff0cd", 3);
    light.position.set(-2, 4, 5);
    scene.add(light);
    const camera = new THREE.OrthographicCamera(
      -0.49,
      0.49,
      0.49,
      -0.49,
      0.1,
      10,
    );
    camera.position.set(0.65, 0.95, 3);
    camera.lookAt(0, 0.64, 0);
    for (const [id, callbacks] of batch) {
      const figure = kit.agent({ id });
      scene.background = new THREE.Color(visualIdentity(id).accent).lerp(
        new THREE.Color("#f8ebcd"),
        0.8,
      );
      scene.add(figure);
      renderer.render(scene, camera);
      const url = renderer.domElement.toDataURL("image/png");
      scene.remove(figure);
      cache.set(id, url);
      while (cache.size > 128) cache.delete(cache.keys().next().value!);
      callbacks.forEach((receive) => receive(url));
      callbacks.clear();
    }
  } catch {
    batch.forEach(([, callbacks]) =>
      callbacks.forEach((receive) => receive(null)),
    );
  } finally {
    kit?.dispose();
    renderer?.dispose();
    renderer?.forceContextLoss();
  }
}
