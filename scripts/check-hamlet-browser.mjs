// Synthetic component journey against the actual Vite/React/Three scene. No backend.
// PLAYWRIGHT_MODULE may name an installed Playwright ESM entry outside this checkout.
import assert from "node:assert/strict";
import { mkdir, writeFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { createServer } from "../web/node_modules/vite/dist/node/index.js";
const { chromium } = await import(
  process.env.PLAYWRIGHT_MODULE || "playwright"
);
const output = fileURLToPath(
  new URL("../docs/evidence/hamlet-overview-2026-09-09/", import.meta.url),
);
await mkdir(output, { recursive: true });
const fixture = `
import React from 'react';
import { createRoot } from 'react-dom/client';
import * as THREE from 'three';
import { Hamlet } from '/src/features/hamlet/Hamlet.tsx';
import '/src/app/style.css';
window.measure = { frames: 0, renderers: [], disposed: 0 };
// WebGLRenderer methods are instance members. Observe actual render calls through
// WebGL context draws, and get the camera/scene using Three's onBeforeRender hook.
const original = THREE.Object3D.prototype.onBeforeRender;
THREE.Object3D.prototype.onBeforeRender = function(renderer, scene, camera, ...rest) {
  const m = window.measure;
  if (!m.renderers.includes(renderer)) m.renderers.push(renderer);
  m.renderer = renderer; m.camera = camera; m.scene = scene; m.frames++;
  return original.call(this, renderer, scene, camera, ...rest);
};
window.stats = () => {
 const m = window.measure, c = m.camera;
 if (!c) return {frames: m.frames};
 const bounds = new THREE.Box3().setFromObject(m.scene);
 const corners = [];
 for (const x of [bounds.min.x,bounds.max.x]) for (const y of [bounds.min.y,bounds.max.y]) for (const z of [bounds.min.z,bounds.max.z]) corners.push(new THREE.Vector3(x,y,z).project(c).toArray());
 return {frames: m.frames, renderers: m.renderers.length, camera: c.uuid, orthographic: c.isOrthographicCamera, position: c.position.toArray(), zoom: c.zoom, corners, width: m.renderer.domElement.width, height: m.renderer.domElement.height};
};
const root = createRoot(document.getElementById('root'));
window.show = (count, connected = true) => {
 const residents = Array.from({length: count}, (_, i) => ({id:'synthetic-'+i,name:'Resident '+(i+1),presence:i===0?'unknown':'idle'}));
 root.render(React.createElement(Hamlet, {snapshot:{residents, letters:[], cursor:Date.now()}, connected}));
};
window.disposeVillage = () => root.unmount();
window.show(Number(new URLSearchParams(location.search).get('count') ?? 4));
`;
const server = await createServer({
  root: fileURLToPath(new URL("../web", import.meta.url)),
  server: { host: "127.0.0.1", port: 5193, strictPort: true },
  plugins: [
    {
      name: "synthetic-hamlet-check",
      resolveId(id) {
        if (id === "/@hamlet-fixture") return "\0hamlet-fixture";
      },
      load(id) {
        if (id === "\0hamlet-fixture") return fixture;
      },
      configureServer(server) {
        server.middlewares.use("/__hamlet_check", async (_req, res) => {
          res.setHeader("Content-Type", "text/html");
          res.end(
            await server.transformIndexHtml(
              "/__hamlet_check",
              `<!doctype html><html><head><meta charset="utf-8"/><meta name="viewport" content="width=device-width, initial-scale=1"/><title>Hamlet synthetic browser check</title></head><body><main style="margin:0 auto;max-width:1240px"><header class="page-head"><div><div class="eyebrow">SYNTHETIC HOUSEHOLD · BROWSER CHECK</div><h1>Hamlet</h1></div></header><div id="root"></div></main><script type="module" src="/@hamlet-fixture"></script></body></html>`,
            ),
          );
        });
      },
    },
  ],
});
await server.listen();
const browser = await chromium.launch({ headless: true });
const results = { browser: browser.version(), checks: [] };
const page = await browser.newPage({ viewport: { width: 1440, height: 1050 } });
const errors = [];
page.on("pageerror", (error) => errors.push(error.message));
const ready = async () =>
  page.waitForFunction(() => window.stats?.().orthographic);
const stats = () => page.evaluate(() => window.stats());
const settled = () =>
  page.evaluate(
    () =>
      new Promise((resolve) =>
        requestAnimationFrame(() => requestAnimationFrame(resolve)),
      ),
  );
const fit = async (name) => {
  await settled();
  const state = await stats();
  assert(
    state.corners.every(
      ([x, y, z]) =>
        Math.abs(x) <= 0.91 && Math.abs(y) <= 0.91 && Math.abs(z) < 1,
    ),
    name + " fits bounds",
  );
  results.checks.push({ name, ...state });
  await page.screenshot({ path: output + name + ".png", fullPage: true });
};
try {
  await page.goto("http://127.0.0.1:5193/__hamlet_check?count=0");
  await ready();
  await fit("desktop-empty");
  await page.goto("http://127.0.0.1:5193/__hamlet_check");
  await ready();
  await fit("desktop-four");
  const initial = await stats();
  await page.getByRole("button", { name: "Zoom in", exact: true }).focus();
  await page.keyboard.press("Enter");
  await page.getByRole("button", { name: "Rotate right", exact: true }).focus();
  await page.keyboard.press("Space");
  await settled();
  const moved = await stats();
  assert.equal(moved.zoom, 1.25);
  assert.notDeepEqual(moved.position, initial.position);
  await page.evaluate(() => window.show(4, false));
  await settled();
  const refreshed = await stats();
  assert.equal(refreshed.camera, moved.camera);
  assert.equal(refreshed.renderers, 1);
  assert.deepEqual(refreshed.position, moved.position);
  assert.equal(refreshed.zoom, moved.zoom);
  await page.evaluate(() => window.show(24));
  await settled();
  const roster = await stats();
  assert.equal(roster.camera, moved.camera);
  assert.equal(roster.renderers, 1);
  assert.equal(roster.zoom, moved.zoom);
  await page.getByRole("button", { name: "Overview", exact: true }).click();
  await fit("desktop-twenty-four");
  await page.setViewportSize({ width: 390, height: 844 });
  await fit("phone-twenty-four");
  await page.evaluate(() => window.show(0));
  await fit("phone-empty");
  await page.evaluate(() => window.show(4));
  await fit("phone-four");
  await page.setViewportSize({ width: 1440, height: 1050 });
  await fit("desktop-four-resized");
  // Real CDP touch events exercise OrbitControls' single-finger pan and pinch.
  const touch = await page.context().newCDPSession(page);
  const rect = await page.locator("canvas").boundingBox();
  const center = {
    x: Math.round(rect.x + rect.width / 2),
    y: Math.round(rect.y + rect.height / 2),
  };
  const beforeTouch = await stats();
  await touch.send("Input.dispatchTouchEvent", {
    type: "touchStart",
    touchPoints: [{ ...center, id: 1 }],
  });
  await touch.send("Input.dispatchTouchEvent", {
    type: "touchMove",
    touchPoints: [{ x: center.x + 70, y: center.y, id: 1 }],
  });
  await touch.send("Input.dispatchTouchEvent", {
    type: "touchEnd",
    touchPoints: [],
  });
  await settled();
  const afterPan = await stats();
  assert.notDeepEqual(afterPan.position, beforeTouch.position);
  await touch.send("Input.dispatchTouchEvent", {
    type: "touchStart",
    touchPoints: [
      { x: center.x - 30, y: center.y, id: 1 },
      { x: center.x + 30, y: center.y, id: 2 },
    ],
  });
  await touch.send("Input.dispatchTouchEvent", {
    type: "touchMove",
    touchPoints: [
      { x: center.x - 40, y: center.y - 5, id: 1 },
      { x: center.x + 80, y: center.y + 35, id: 2 },
    ],
  });
  await touch.send("Input.dispatchTouchEvent", {
    type: "touchEnd",
    touchPoints: [],
  });
  await settled();
  assert((await stats()).zoom > beforeTouch.zoom);
  assert.notDeepEqual((await stats()).position, afterPan.position);
  assert.equal(new URL(page.url()).hash, "");
  await page.getByRole("button", { name: "Overview", exact: true }).click();
  await settled();
  results.checks.push({ name: "touch-pan-pinch-rotation", passed: true });
  // Drag away and back over a building: the endpoint alone must not select it.
  const target = await page.evaluate(() => {
    const m = window.measure,
      building = m.scene.children
        .find((c) => c.type === "Group")
        .children.find((c) => c.userData.href === "#residents/synthetic-0");
    const p = building.position.clone();
    p.y += 1;
    p.project(m.camera);
    const rect = m.renderer.domElement.getBoundingClientRect();
    return {
      x: rect.left + ((p.x + 1) * rect.width) / 2,
      y: rect.top + ((1 - p.y) * rect.height) / 2,
    };
  });
  await page.mouse.move(target.x, target.y);
  await page.mouse.down();
  await page.mouse.move(target.x + 80, target.y + 30, { steps: 10 });
  await page.mouse.move(target.x, target.y, { steps: 10 });
  await page.mouse.up();
  assert.equal(new URL(page.url()).hash, "");
  await page.getByRole("button", { name: "Overview", exact: true }).click();
  await page.mouse.move(target.x, target.y);
  await page.mouse.wheel(0, -100);
  await settled();
  assert((await stats()).zoom > 1);
  await page.getByRole("button", { name: "Overview", exact: true }).click();
  await settled();
  await page.mouse.click(target.x, target.y);
  await page.waitForURL((url) => url.hash === "#residents/synthetic-0");
  results.checks.push({
    name: "keyboard-refresh-roster-drag-wheel-select",
    passed: true,
  });
  await page.emulateMedia({ reducedMotion: "reduce" });
  await page.getByRole("button", { name: "Rotate left", exact: true }).click();
  await settled();
  const still = await stats();
  await page.waitForTimeout(150);
  assert.deepEqual((await stats()).position, still.position);
  // Observe renderer's actual dispose event and stopped draws after React unmount.
  await page.evaluate(() => {
    const renderer = window.measure.renderer,
      dispose = renderer.dispose;
    renderer.dispose = () => {
      window.measure.disposed++;
      dispose.call(renderer);
    };
    window.disposeVillage();
  });
  const stopped = await stats();
  await page.waitForTimeout(150);
  assert.equal((await stats()).frames, stopped.frames);
  assert.equal(await page.locator("canvas").count(), 0);
  assert.equal(await page.evaluate(() => window.measure.disposed), 1);
  results.checks.push({
    name: "reduced-motion-and-unmount-disposal",
    passed: true,
  });
  await page.reload();
  await ready();
  await page.evaluate(() =>
    window.measure.renderer
      .getContext()
      .getExtension("WEBGL_lose_context")
      .loseContext(),
  );
  await page.getByText(/3D is unavailable/).waitFor();
  assert.equal(
    await page.getByRole("link", { name: /Resident 1 · unknown/ }).count(),
    1,
  );
  assert.equal(await page.locator("canvas").count(), 0);
  await page.screenshot({ path: output + "context-loss.png", fullPage: true });
  results.checks.push({
    name: "context-loss-retains-record-navigation",
    passed: true,
  });
  const failed = await browser.newPage();
  await failed.addInitScript(() => {
    HTMLCanvasElement.prototype.getContext = function () {
      return null;
    };
  });
  await failed.goto("http://127.0.0.1:5193/__hamlet_check");
  await failed.getByText(/3D is unavailable/).waitFor();
  assert.equal(
    await failed.getByRole("link", { name: /Resident 1 · unknown/ }).count(),
    1,
  );
  results.checks.push({
    name: "initial-graphics-failure-retains-record-navigation",
    passed: true,
  });
  await failed.close();
  assert.deepEqual(errors, []);
  await writeFile(
    output + "results.json",
    JSON.stringify(results, null, 2) + "\n",
  );
  console.log(
    JSON.stringify(
      { browser: results.browser, checks: results.checks.map((c) => c.name) },
      null,
      2,
    ),
  );
} finally {
  await browser.close();
  await server.close();
}
