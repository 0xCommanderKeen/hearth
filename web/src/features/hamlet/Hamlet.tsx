import { useEffect, useRef, useState } from "react";
import * as THREE from "three";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";
import { createArtKit } from "./village/art.js";
import type { Snapshot } from "../../shared/client";

// Original Warren miniature models, shared here without its operational layer.
export function Hamlet({
  snapshot,
  connected,
}: {
  snapshot: Snapshot;
  connected: boolean;
}) {
  const host = useRef<HTMLDivElement>(null);
  const [unavailable, setUnavailable] = useState(false);
  const identities = snapshot.residents.map((r) => r.id).join("\0");
  useEffect(() => {
    if (!host.current) return;
    let renderer: THREE.WebGLRenderer;
    try {
      renderer = new THREE.WebGLRenderer({ antialias: true });
    } catch {
      setUnavailable(true);
      return;
    }
    setUnavailable(false);
    const element = host.current;
    const scene = new THREE.Scene();
    scene.background = new THREE.Color("#cbd5c1");
    const rows = Math.max(1, Math.ceil((snapshot.residents.length + 1) / 4));
    const depth = Math.max(19, rows * 5 + 10);
    const centerZ = -(rows - 1) * 2.5;
    const distance = Math.max(19, depth * 1.25);
    const camera = new THREE.PerspectiveCamera(38, 1, 0.1, distance * 8);
    camera.position.set(distance * 0.74, distance * 0.8, centerZ + distance);
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    renderer.shadowMap.enabled = true;
    renderer.shadowMap.type = THREE.PCFSoftShadowMap;
    element.appendChild(renderer.domElement);
    const controls = new OrbitControls(camera, renderer.domElement);
    controls.target.set(0, 0, centerZ);
    controls.minDistance = 10;
    controls.maxDistance = distance * 3;
    controls.maxPolarAngle = Math.PI / 2.4;
    controls.enablePan = true;
    scene.add(new THREE.HemisphereLight("#fff5df", "#748267", 2.4));
    const sun = new THREE.DirectionalLight("#fff0cd", 3);
    sun.position.set(-8, 16, 8);
    sun.castShadow = true;
    sun.shadow.mapSize.set(2048, 2048);
    Object.assign(sun.shadow.camera, {
      left: -15,
      right: 15,
      top: 15,
      bottom: -15,
    });
    scene.add(sun);
    const kit = createArtKit();
    const groundGeometry = new THREE.BoxGeometry(26, 0.5, depth);
    const groundMaterial = new THREE.MeshStandardMaterial({
      color: "#98ad83",
      roughness: 1,
    });
    const ground = new THREE.Mesh(groundGeometry, groundMaterial);
    ground.position.set(0, -0.3, centerZ);
    ground.receiveShadow = true;
    scene.add(ground);
    const pathGeometry = new THREE.BoxGeometry(19, 0.04, 1.5);
    const pathMaterial = new THREE.MeshStandardMaterial({ color: "#dbc8a3" });
    const path = new THREE.Mesh(pathGeometry, pathMaterial);
    path.position.z = 1.8;
    path.receiveShadow = true;
    scene.add(path);
    const targets: THREE.Object3D[] = [];
    function building(
      id: string,
      kind: string,
      x: number,
      z: number,
      href: string,
    ) {
      const object = kit.building({ id, kind, width: 3.5, depth: 3.3 });
      object.position.set(x, 0, z);
      object.userData.href = href;
      targets.push(object);
      scene.add(object);
    }
    building("townhall", "lodge", 7.5, -2, "#townhall");
    const square = kit.building({
      id: "square",
      kind: "square",
      width: 3,
      depth: 3,
    });
    square.position.set(0, 0, 4);
    scene.add(square);
    snapshot.residents.forEach((r, i) => {
      // The first row reserves its last plot for Townhall.
      const slot = i < 3 ? i : i + 1;
      const x = -7.5 + (slot % 4) * 5;
      const z = -2 - Math.floor(slot / 4) * 5;
      building(r.id, "home", x, z, `#residents/${encodeURIComponent(r.id)}`);
      const person = kit.agent({ id: r.id });
      person.position.set(x, 0, z + 2.2);
      person.userData.href = `#residents/${encodeURIComponent(r.id)}`;
      targets.push(person);
      scene.add(person);
    });
    [
      [-11, -6],
      [-11, 5],
      [-7, 7],
      [8, 6],
      [11, -5],
      [11, centerZ * 2 - 6],
      [-11, centerZ * 2 - 6],
    ].forEach(([x, z], i) => {
      const tree = kit.tree(i);
      tree.position.set(x, 0, z);
      scene.add(tree);
    });
    const ray = new THREE.Raycaster();
    let down = { x: 0, y: 0 };
    const pointerDown = (e: PointerEvent) => {
      down = { x: e.clientX, y: e.clientY };
    };
    const pick = (e: PointerEvent) => {
      if (Math.hypot(e.clientX - down.x, e.clientY - down.y) > 5) return;
      const rect = renderer.domElement.getBoundingClientRect();
      ray.setFromCamera(
        new THREE.Vector2(
          ((e.clientX - rect.left) / rect.width) * 2 - 1,
          (-(e.clientY - rect.top) / rect.height) * 2 + 1,
        ),
        camera,
      );
      let hit: THREE.Object3D | null =
        ray.intersectObjects(targets, true)[0]?.object ?? null;
      while (hit && !hit.userData.href) hit = hit.parent;
      if (hit) window.location.hash = hit.userData.href;
    };
    renderer.domElement.addEventListener("pointerdown", pointerDown);
    renderer.domElement.addEventListener("pointerup", pick);
    const resize = new ResizeObserver(() => {
      camera.aspect = element.clientWidth / element.clientHeight;
      camera.updateProjectionMatrix();
      renderer.setSize(element.clientWidth, element.clientHeight);
    });
    resize.observe(element);
    renderer.setAnimationLoop(() => {
      controls.update();
      renderer.render(scene, camera);
    });
    return () => {
      renderer.setAnimationLoop(null);
      resize.disconnect();
      controls.dispose();
      renderer.domElement.removeEventListener("pointerdown", pointerDown);
      renderer.domElement.removeEventListener("pointerup", pick);
      kit.dispose();
      groundGeometry.dispose();
      groundMaterial.dispose();
      pathGeometry.dispose();
      pathMaterial.dispose();
      renderer.dispose();
      renderer.domElement.remove();
    };
  }, [identities]);
  return (
    <section className="hamlet-scene" aria-label="Hamlet village">
      <div className="scene-toolbar">
        <span>HAMLET · 3D VILLAGE</span>
        <span>Drag to orbit · Scroll to zoom · Select a home</span>
      </div>
      <div
        ref={host}
        className="scene-canvas"
        role="img"
        aria-label={`${snapshot.residents.map((r) => `${r.name}'s home`).join(", ") || "Empty village"}. Select a resident using the links below.`}
      />
      {unavailable && (
        <p className="notice">
          3D is unavailable in this browser. Resident profiles remain available
          below.
        </p>
      )}
      <div className="scene-residents">
        <a href="#townhall">Townhall →</a>
        {snapshot.residents.map((r) => (
          <a key={r.id} href={`#residents/${encodeURIComponent(r.id)}`}>
            {r.name} · {connected ? r.presence : "disconnected"} →
          </a>
        ))}
      </div>
    </section>
  );
}
