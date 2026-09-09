// Synthetic responses exercise the full App and real Client/watch and WebGL renderer.
import assert from "node:assert/strict";
import { mkdir, writeFile, mkdtemp, readFile, rm } from "node:fs/promises";
import { spawn } from "node:child_process";
import { tmpdir, cpus, totalmem, platform, release } from "node:os";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import { createServer } from "../web/node_modules/vite/dist/node/index.js";
const { chromium } = await import(
  process.env.PLAYWRIGHT_MODULE || "playwright"
);
const output = fileURLToPath(
  new URL("../docs/evidence/hamlet-journeys-2026-09-09/", import.meta.url),
);
await mkdir(output, { recursive: true });
const fixture = `
import React from 'react'; import {createRoot} from 'react-dom/client';
import * as THREE from 'three'; import {App} from '/src/app/App.tsx';
window.THREE=THREE;window.measure={frames:0,renderers:[],samples:[],disposed:0};
const watched = new Map();
for(const method of ['addEventListener','removeEventListener']) { const original=EventTarget.prototype[method]; EventTarget.prototype[method]=function(type,fn,...rest){if(this===document || this===window || this instanceof HTMLCanvasElement){let events=watched.get(this);if(!events)watched.set(this,events=new Map());let listeners=events.get(type);if(!listeners)events.set(type,listeners=new Set());if(method==='addEventListener')listeners.add(fn);else listeners.delete(fn);}return original.call(this,type,fn,...rest);};}
window.handlerCount=()=>[...watched.values()].reduce((n,events)=>n+[...events.values()].reduce((n,set)=>n+set.size,0),0);

THREE.Object3D.prototype.onBeforeRender=function(renderer,scene,camera){const m=window.measure;if(!m.renderers.includes(renderer)){m.renderers.push(renderer);const render=renderer.render.bind(renderer),dispose=renderer.dispose.bind(renderer),loop=renderer.setAnimationLoop.bind(renderer);renderer.loopActive=true;renderer.setAnimationLoop=(fn)=>{renderer.loopActive=!!fn;loop(fn);};renderer.render=(scene,camera)=>{const at=performance.now();render(scene,camera);if(m.record)m.samples.push({at,ms:performance.now()-at,calls:renderer.info.render.calls,triangles:renderer.info.render.triangles});};renderer.dispose=()=>{dispose();m.disposed++;};}Object.assign(m,{renderer,scene,camera});m.frames++;};
window.stats=()=>{const m=window.measure;return {frames:m.frames,renderers:m.renderers.length,camera:m.camera?.uuid,position:m.camera?.position.toArray(),zoom:m.camera?.zoom};};
const resident=window.resident=(id,name,presence)=>({id,name,presence,purpose:'Read fictional orchard notes.',revision:1,daily_limit:1000000,pause_reason:null,lifecycle:{resident_id:id,state:'ready',revision:1},skills:[]});
window.snapshot={schema_version:1,epoch:'panels-synthetic',cursor:1,runtimes:{default:'fixture',configured:['fixture'],kinds:{fixture:{label:'Synthetic runtime',live:false}}},residents:[resident('reader','Reader','interrupted'),resident('keeper','Keeper','paused')],tasks:[{id:'work',resident_id:'reader',instruction:'Read the fictional orchard',status:'interrupted',created_at:100}],runs:[{id:'uncertain',task_id:'work',resident_id:'reader',status:'interrupted',artifact_id:null,usage_known:0,actual_cost:null},{id:'result',task_id:'old-outside-window',resident_id:'reader',status:'succeeded',artifact_id:'report',usage_known:1,actual_cost:3,finished_at:1788955200}],activity:[],letters:[],notifications:[],limits:{tasks:100,runs:100,letters:30,activity:30,notifications:100},household:{revision:1,daily_limit:10000000,remaining:9000000,resident_count:2,active_runs:1,concurrency_limit:2,budget_day:'2026-09-09',timezone:'UTC',spent:1000000,reserved:0,unknown:100000}};
let stream; window.requests=[];window.failResult=false;
window.publish=()=>{window.snapshot.cursor++;stream?.enqueue(new TextEncoder().encode('data: '+JSON.stringify(window.snapshot)+'\\n\\n'));};
window.disconnect=()=>{window.offline=true;stream?.close();};
window.fetch=async(url,options={})=>{window.requests.push(String(url));if(window.offline)throw Error('Synthetic disconnect');const path=String(url).split('?')[0];
 if(path==='/api/state')return Response.json(window.snapshot);
 if(path==='/api/events')return new Response(new ReadableStream({start(controller){stream=controller;options.signal?.addEventListener('abort',()=>{try{controller.close()}catch{}})}}));
 if(path==='/api/runs/result')return window.failResult?Response.json({error:'Synthetic result unavailable'},{status:503}):Response.json({...window.snapshot.runs[1],runtime_kind:'fixture'});
 if(path==='/api/artifacts/report')return Response.json({content:'Fictional orchard result: three apples.'});
 if(path.endsWith('/configuration'))return Response.json({resident_id:'reader',lifecycle:{state:'ready',revision:1},declaration:{name:'Reader',purpose:'Fictional notes',instructions:'Read',expected_revision:1,daily_limit:1000000,budget_timezone:'UTC'},memory:{expected_revision:1,text:''},inputs:{expected_revision:0,input_sets:[]},skills:{expected_revision:0,skills:[]},routines:[]});
 if(path.endsWith('/journal'))return Response.json({entries:[],total:0});
 if(path.endsWith('/memory/history'))return Response.json({revisions:[],total:0});
 return Response.json({origins:[],limit:100,offset:0,truncated:false,skills:[],input_sets:[],revision:0,managers:[],execution_profiles:[]});
};
sessionStorage.setItem('hearth.operator-token','synthetic-only');location.hash='#hamlet';createRoot(document.getElementById('root')).render(React.createElement(App));`;
const server = await createServer({
  root: fileURLToPath(new URL("../web", import.meta.url)),
  server: { host: "127.0.0.1", port: 5198, strictPort: true },
  plugins: [
    {
      name: "panel-fixture",
      resolveId(id) {
        if (id === "/@panel-fixture") return "\0panel-fixture";
      },
      load(id) {
        if (id === "\0panel-fixture") return fixture;
      },
      configureServer(server) {
        server.middlewares.use("/__panels", async (req, res) => {
          res.setHeader("Content-Type", "text/html");
          res.end(
            await server.transformIndexHtml(
              "/__panels",
              '<!doctype html><html><head><meta name="viewport" content="width=device-width,initial-scale=1"></head><body><div id="root"></div><script type="module" src="/@panel-fixture"></script></body></html>',
            ),
          );
        });
      },
    },
  ],
});
await server.listen();
// Ordinary Playwright pages force focus emulation, making background tabs report
// visible even after a separate CDP client disables it. Connect without those
// defaults to an isolated Chromium profile so this is a real visibility transition.
const profile = await mkdtemp(join(tmpdir(), "hearth-journeys-browser-"));
const processBrowser = spawn(
  chromium.executablePath(),
  [
    "--remote-debugging-port=0",
    `--user-data-dir=${profile}`,
    "--no-first-run",
    "--no-default-browser-check",
    "--disable-background-networking",
    "--disable-component-update",
    "--use-mock-keychain",
    "about:blank",
  ],
  { stdio: "ignore" },
);
let endpoint;
for (let attempt = 0; attempt < 100; attempt++) {
  try {
    const [port] = (
      await readFile(join(profile, "DevToolsActivePort"), "utf8")
    ).split("\n");
    endpoint = `http://127.0.0.1:${port}`;
    break;
  } catch {
    await new Promise((r) => setTimeout(r, 100));
  }
}
if (!endpoint) {
  processBrowser.kill();
  throw Error("Isolated Chromium did not start");
}
const browser = await chromium.connectOverCDP(endpoint, { noDefaults: true });
const context = browser.contexts()[0];
const page = context.pages()[0];
await page.setViewportSize({ width: 1440, height: 1050 });
const errors = [];
const measurements = [];
const matrix = [];
let hardware;
page.on("pageerror", (e) => errors.push(e.message));
const stats = () => page.evaluate(() => window.stats());
const settle = () =>
  page.evaluate(
    () =>
      new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r))),
  );
const directory = (name) =>
  page
    .getByRole("group", { name: "Building directory", exact: true })
    .getByRole("button", { name: new RegExp("Select " + name) });

const enter = async (name) => {
  await directory(name).click();
  await page
    .getByRole("button", {
      name: name === "Townhall" ? /Enter Townhall/ : /Enter home/,
    })
    .click();
  await page.locator(".room-canvas canvas").waitFor();
  await settle();
};
const back = async () => {
  await page.getByRole("button", { name: "Back to village" }).click();
  await settle();
};
const returnRoom = async () => {
  await page.getByRole("link", { name: "← Return to village" }).click();
  await page.getByRole("region", { name: "Building interior" }).waitFor();
  await settle();
};
const furniture = async (label, expected) => {
  await page.locator(".room-canvas canvas").scrollIntoViewIfNeeded();
  const point = await page.evaluate((label) => {
    const { scene, camera } = window.measure,
      THREE = window.THREE;
    const canvas = document.querySelector(".room-canvas canvas"),
      b = canvas.getBoundingClientRect();
    const meshes = scene.children.filter((o) =>
      o.userData.label?.startsWith(label),
    );
    const targets = scene.children.filter((o) => o.userData.href);
    for (const mesh of meshes.reverse()) {
      const p = mesh.getWorldPosition(new THREE.Vector3()).project(camera);
      const ray = new THREE.Raycaster();
      ray.setFromCamera(new THREE.Vector2(p.x, p.y), camera);
      if (
        ray
          .intersectObjects(targets, false)[0]
          ?.object.userData.label?.startsWith(label)
      )
        return {
          x: b.left + ((p.x + 1) * b.width) / 2,
          y: b.top + ((1 - p.y) * b.height) / 2,
        };
    }
    throw Error("No reachable furniture " + label);
  }, label);
  await page.mouse.click(point.x, point.y);
  await page.waitForFunction(
    (expected) => location.hash === expected,
    expected,
  );
  if (expected.endsWith("panel=work")) {
    await page.locator("#resident-work").waitFor();
    assert(
      await page
        .locator("#resident-work")
        .evaluate((el) => el === document.activeElement),
    );
  }
  await returnRoom();
};
const buildingPoint = async (p, identity) =>
  p.evaluate((identity) => {
    const { scene, camera } = window.measure,
      THREE = window.THREE;
    const canvas = document.querySelector(".scene-canvas canvas"),
      b = canvas.getBoundingClientRect();
    const targets = [];
    scene.traverse((o) => {
      if (o.userData.identity) targets.push(o);
    });
    const root = targets.find((o) => o.userData.identity === identity);
    const meshes = [];
    root.traverse((o) => {
      if (o.isMesh) meshes.push(o);
    });
    for (const mesh of meshes) {
      const point = mesh.getWorldPosition(new THREE.Vector3()).project(camera);
      const ray = new THREE.Raycaster();
      ray.setFromCamera(new THREE.Vector2(point.x, point.y), camera);
      let hit = ray.intersectObjects(targets, true)[0]?.object;
      while (hit && !hit.userData.identity) hit = hit.parent;
      if (hit === root) {
        const x = b.left + ((point.x + 1) * b.width) / 2,
          y = b.top + ((1 - point.y) * b.height) / 2;
        if (document.elementFromPoint(x, y) === canvas) return { x, y };
      }
    }
    throw Error("No uncovered building point " + identity);
  }, identity);
const keyboardActivate = async (p, locator) => {
  for (let i = 0; i < 400; i++) {
    if (await locator.evaluate((el) => el === document.activeElement)) {
      await p.keyboard.press("Enter");
      return;
    }
    await p.keyboard.press("Tab");
  }
  throw Error("Keyboard cannot reach " + (await locator.textContent()));
};
try {
  await page.goto("http://127.0.0.1:5198/__panels");
  await page.waitForFunction(() => window.stats().camera);
  await page.getByText(/Connected · Synthetic/).waitFor();
  hardware = await page.evaluate(() => {
    const gl = window.measure.renderer.getContext();
    const debug = gl.getExtension("WEBGL_debug_renderer_info");
    return {
      userAgent: navigator.userAgent,
      vendor: gl.getParameter(debug.UNMASKED_VENDOR_WEBGL),
      renderer: gl.getParameter(debug.UNMASKED_RENDERER_WEBGL),
      webgl: gl.getParameter(gl.VERSION),
      devicePixelRatio,
    };
  });
  for (const count of [0, 5, 25, 100]) {
    await page.getByRole("checkbox", { name: /Lighter graphics/ }).uncheck();
    await page.evaluate((count) => {
      window.snapshot.residents = Array.from({ length: count }, (_, i) =>
        window.resident(
          "r" + i,
          "Resident " + i,
          i % 3 ? "idle" : "interrupted",
        ),
      );
      window.publish();
    }, count);
    await page.waitForFunction(
      (count) =>
        document.querySelectorAll(".scene-directory button").length ===
        count + 1,
      count,
    );
    for (const width of [320, 390, 768, 1440]) {
      await page.setViewportSize({ width, height: 1050 });
      await page.getByRole("button", { name: "Overview", exact: true }).click();
      await settle();
      assert(
        await page.evaluate(
          () => document.documentElement.scrollWidth <= innerWidth,
        ),
        "overflow " + count + "/" + width,
      );
      assert.equal(
        await page.locator(".scene-directory button").count(),
        count + 1,
      );
      const pixels = await page.screenshot({
        path: output + `village-${count}-${width}.png`,
        fullPage: true,
      });
      // Projected labels alone must never count as a rendered village. Check the
      // actual captured ground/tree pixels, including the first frame after sizing.
      const groundPixels = await page.evaluate(async (data) => {
        const image = new Image();
        image.src = "data:image/png;base64," + data;
        await image.decode();
        const canvas = document.createElement("canvas");
        canvas.width = image.width;
        canvas.height = image.height;
        const ctx = canvas.getContext("2d");
        ctx.drawImage(image, 0, 0);
        const b = document
          .querySelector(".scene-canvas")
          .getBoundingClientRect();
        const pixels = ctx.getImageData(
          Math.floor(b.left + scrollX),
          Math.floor(b.top + scrollY),
          Math.floor(b.width),
          Math.floor(b.height),
        ).data;
        let green = 0;
        for (let i = 0; i < pixels.length; i += 4) {
          const [r, g, b] = pixels.slice(i, i + 3);
          if (g > r * 1.02 && r > b * 1.2) green++;
        }
        return green;
      }, pixels.toString("base64"));
      assert(groundPixels > 500, `missing village pixels at ${count}/${width}`);
      matrix.push({ count, width, overflow: false, groundPixels });
    }
    if (count === 100) {
      const buttons = page.locator(".scene-directory button");
      await keyboardActivate(page, buttons.first());
      await page.keyboard.press("Escape");
      for (let i = 1; i < 101; i++) {
        await page.keyboard.press("Tab");
        assert(
          await buttons.nth(i).evaluate((el) => el === document.activeElement),
        );
        await page.keyboard.press("Enter");
        await page
          .getByRole("dialog", { name: `Resident ${i - 1} at home` })
          .waitFor();
        await page.keyboard.press("Escape");
        assert(
          await buttons.nth(i).evaluate((el) => el === document.activeElement),
        );
      }
    }
    for (const lighter of [false, true]) {
      await page
        .getByRole("checkbox", { name: /Lighter graphics/ })
        .setChecked(lighter);
      await page.getByRole("button", { name: "Overview", exact: true }).click();
      await settle();
      const samples = await page.evaluate(async () => {
        const m = window.measure;
        m.samples = [];
        m.record = true;
        for (let i = 0; i < 70; i++) {
          document.querySelector('[aria-label="Rotate right"]').click();
          await new Promise(requestAnimationFrame);
        }
        m.record = false;
        return m.samples.slice(10);
      });
      const sorted = (values) => values.sort((a, b) => a - b);
      const summary = (values) => {
        const v = sorted(values);
        return {
          median: v[Math.floor(v.length / 2)],
          p95: v[Math.min(v.length - 1, Math.floor(v.length * 0.95))],
        };
      };
      measurements.push({
        count,
        lighter,
        samples: samples.length,
        cpuRenderMs: summary(samples.map((x) => x.ms)),
        frameIntervalMs: summary(
          samples.slice(1).map((x, i) => x.at - samples[i].at),
        ),
        drawCalls: summary(samples.map((x) => x.calls)),
        triangles: summary(samples.map((x) => x.triangles)),
      });
    }
  }
  await page.getByRole("checkbox", { name: /Lighter graphics/ }).uncheck();
  await page.reload();
  await page.waitForFunction(() => window.stats().camera);
  await page.getByText(/Connected · Synthetic/).waitFor();
  // Rendering is static at rest, with genuine visible motion measured above.
  const idle = (await stats()).frames;
  await page.waitForTimeout(150);
  assert.equal((await stats()).frames, idle);
  await page.getByRole("button", { name: "Rotate right" }).click();
  await settle();
  const otherTab = await page.context().newPage();
  await otherTab.goto("about:blank");
  await otherTab.bringToFront();
  await page.waitForFunction(
    () => document.hidden,
    {},
    { polling: 100, timeout: 3000 },
  );
  const hidden = await page.evaluate(() => document.hidden);
  console.log("Actual background tab hidden:", hidden);
  if (!hidden)
    throw Error("Isolated Chromium did not background the first tab");
  assert.equal(
    await page.evaluate(() => window.measure.renderers[0].loopActive),
    false,
  );
  const hiddenFrames = (await stats()).frames;
  await page.evaluate(() => {
    document.querySelector('[aria-label="Rotate right"]').click();
    window.publish();
  });
  await page.waitForTimeout(150);
  assert.equal((await stats()).frames, hiddenFrames);
  await page.bringToFront();
  await otherTab.close();
  await settle();
  assert.equal(
    await page.evaluate(() => window.measure.renderers[0].loopActive),
    true,
  );
  // Restore the recorded overview after the hidden-tab invalidation probe.
  await page.getByRole("button", { name: "Rotate left" }).click();
  await settle();
  const overview = await stats();
  await page.evaluate(() => {
    window.snapshot.letters = [
      {
        kind: "letter_sent",
        task_id: "motion-change",
        at: 100,
        from_resident_id: null,
        to_resident_id: "reader",
        title: "Synthetic motion change",
        state: "pending",
        root_task_id: "motion-change",
        depth: 0,
      },
    ];
    window.publish();
  });
  await page.waitForFunction(() =>
    window.measure.scene.children.some((o) => o.userData.letter),
  );
  const moving = (await stats()).frames;
  await page.emulateMedia({ reducedMotion: "reduce" });
  await page.waitForFunction(
    () => !window.measure.scene.children.some((o) => o.userData.letter),
  );
  assert(
    (await stats()).frames > moving,
    "removing couriers must redraw their last pixels",
  );
  await page.emulateMedia({ reducedMotion: "no-preference" });

  await page.locator(".scene-canvas canvas").scrollIntoViewIfNeeded();
  const homePoint = await buildingPoint(page, "#residents/reader");
  await page.mouse.click(homePoint.x, homePoint.y);
  await page.getByRole("dialog", { name: "Reader at home" }).waitFor();
  const selectedCamera = await stats();
  await page.getByRole("checkbox", { name: /Lighter graphics/ }).check();
  await settle();
  assert.deepEqual((await stats()).position, selectedCamera.position);
  assert.equal((await stats()).camera, selectedCamera.camera);
  await keyboardActivate(
    page,
    page.getByRole("button", { name: /Enter home/ }),
  );
  await page.locator(".room-canvas canvas").waitFor();
  await settle();
  const roomStats = await stats();
  const roomTab = await context.newPage();
  await roomTab.bringToFront();
  await page.waitForFunction(() => document.hidden, {}, { polling: 100 });
  const hiddenRoom = (await stats()).frames;
  await page.evaluate(() => {
    document.querySelector(".scene-rendering input").click();
    window.publish();
  });
  await page.waitForTimeout(150);
  assert.equal((await stats()).frames, hiddenRoom);
  await page.bringToFront();
  await roomTab.close();
  await settle();
  assert((await stats()).frames > hiddenRoom);
  assert.equal(roomStats.renderers, 2);
  await page.screenshot({ path: output + "desktop-home.png", fullPage: true });
  await keyboardActivate(
    page,
    page.getByRole("link", { name: "Desk · recorded work" }),
  );
  await page.locator("#resident-work").waitFor();
  await keyboardActivate(
    page,
    page.getByRole("link", { name: "← Return to village" }),
  );
  await page.getByRole("region", { name: "Building interior" }).waitFor();
  await settle();
  await furniture("Desk", "#residents/reader?panel=work");
  await furniture("Journal", "#residents/reader?panel=journal");
  await furniture("Letter", "#residents/reader?panel=letters");
  assert.equal((await stats()).camera, roomStats.camera);
  await page.evaluate(() => {
    window.snapshot.residents[0].name = "Renamed reader";
    window.publish();
  });
  await page.getByRole("heading", { name: "Renamed reader’s home" }).waitFor();
  assert.equal((await stats()).camera, roomStats.camera);
  await page.evaluate(() => window.disconnect());
  await page
    .locator(".room-facts")
    .filter({ hasText: "Disconnected" })
    .waitFor();
  await page.evaluate(() => (window.offline = false));
  await page.getByText(/Connected · Synthetic/).waitFor();
  await back();
  const selected = await stats();
  assert.equal(selected.camera, overview.camera);
  await page.keyboard.press("Escape");
  await settle();
  assert.deepEqual((await stats()).position, overview.position);
  await enter("Townhall");
  await page.screenshot({
    path: output + "desktop-townhall.png",
    fullPage: true,
  });
  await furniture("Work table", "#tasks");
  await furniture("Ledger", "#townhall");
  await furniture("Letter", "#inbox");
  await page.evaluate(() => {
    window.snapshot.household.active_runs = 0;
    window.publish();
  });
  await page
    .locator(".room-facts")
    .filter({ hasText: "0 active runs" })
    .waitFor();
  await page.getByText(/\$0.10 held for unknown usage/).waitFor();
  await back();
  await page.keyboard.press("Escape");
  await page.setViewportSize({ width: 390, height: 844 });
  await enter("Renamed reader");
  await page.screenshot({ path: output + "phone-home.png", fullPage: true });
  await furniture("Desk", "#residents/reader?panel=work");
  await page.evaluate(() => {
    window.snapshot.residents[0].lifecycle.state = "archived";
    window.snapshot.residents[0].unresolved_runs = 1;
    window.publish();
  });
  await page.getByText(/Their room is closed/).waitFor();
  assert.equal(await page.locator(".room-canvas canvas").count(), 0);
  await page.getByRole("link", { name: "Open resident history →" }).click();
  await returnRoom();
  await page.screenshot({
    path: output + "archive-history.png",
    fullPage: true,
  });
  await back();
  await page.keyboard.press("Escape");
  await enter("Townhall");
  await page.screenshot({
    path: output + "phone-townhall.png",
    fullPage: true,
  });
  await furniture("Letter", "#inbox");
  assert(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  );
  await page.evaluate(() =>
    window.measure.renderer
      .getContext()
      .getExtension("WEBGL_lose_context")
      .loseContext(),
  );
  await page.getByText(/Room graphics are unavailable/).waitFor();
  await page
    .getByRole("link", { name: "Work table · household tasks & results" })
    .click();
  await returnRoom();
  await page.screenshot({ path: output + "graphics-loss.png", fullPage: true });
  await back();
  await page.keyboard.press("Escape");
  assert.equal((await stats()).camera, overview.camera);
  // Repeated rooms own exactly one extra renderer and remove their listeners.
  const handlers = await page.evaluate(() => window.handlerCount());
  const disposed = await page.evaluate(() => window.measure.disposed);
  for (let i = 0; i < 8; i++) {
    await enter("Townhall");
    assert.equal(await page.locator("canvas").count(), 2);
    await page.getByRole("link", { name: "Letter cabinet · inbox" }).click();
    const frozen = (await stats()).frames;
    await page.waitForTimeout(60);
    assert.equal((await stats()).frames, frozen);
    await returnRoom();
    await back();
    await page.keyboard.press("Escape");
    assert.equal(await page.locator("canvas").count(), 1);
  }
  assert.equal(
    await page.evaluate(() => window.measure.disposed),
    disposed + 8,
  );
  assert.equal(await page.evaluate(() => window.handlerCount()), handlers);
  assert(
    await page.evaluate(() =>
      window.measure.renderers
        .slice(1)
        .every((renderer) => renderer.getContext().isContextLost()),
    ),
    "disposed rooms release their WebGL contexts",
  );
  // Exterior context loss also leaves the contextual panel and record routes live.
  await page.evaluate(() =>
    window.measure.renderers[0]
      .getContext()
      .getExtension("WEBGL_lose_context")
      .loseContext(),
  );
  await page.getByText(/3D is unavailable/).waitFor();
  await directory("Townhall").click();
  await page.getByRole("button", { name: /Enter Townhall/ }).click();
  await page
    .getByRole("link", { name: "Ledger shelf · household & allowances" })
    .click();
  await returnRoom();
  await back();
  await page.keyboard.press("Escape");
  // Touch input uses genuine Playwright touch events and a 2x backing scale.
  for (const width of [320, 390]) {
    const touch = await browser.newContext({
      viewport: { width, height: 844 },
      hasTouch: true,
      isMobile: true,
      deviceScaleFactor: 2,
      reducedMotion: "reduce",
    });
    const tp = await touch.newPage();
    await tp.goto("http://127.0.0.1:5198/__panels");
    await tp.waitForFunction(() => window.stats().camera);
    await tp.locator(".scene-canvas canvas").scrollIntoViewIfNeeded();
    const before = await tp.evaluate(() => window.stats());
    const point = await buildingPoint(tp, "#residents/reader");
    await tp.touchscreen.tap(point.x, point.y);
    await tp.getByRole("button", { name: /Enter home/ }).tap();
    await tp.getByRole("link", { name: "Journal shelf · journal" }).tap();
    await tp.getByRole("link", { name: "← Return to village" }).tap();
    await tp.getByRole("button", { name: "Back to village" }).tap();
    await tp.getByRole("button", { name: "Close village panel" }).tap();
    await tp.waitForTimeout(50);
    assert.deepEqual(
      (await tp.evaluate(() => window.stats())).position,
      before.position,
    );
    await tp.getByRole("checkbox", { name: /Lighter graphics/ }).tap();
    assert.equal(
      await tp.evaluate(() => window.measure.renderer.getPixelRatio()),
      1,
    );
    await tp.screenshot({
      path: output + `touch-reduced-${width}.png`,
      fullPage: true,
    });
    assert(
      await tp.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth,
      ),
    );
    await touch.close();
  }
  // A separate no-WebGL startup still enters rooms through accessible controls.
  await page.addInitScript(() => {
    const original = HTMLCanvasElement.prototype.getContext;
    HTMLCanvasElement.prototype.getContext = function (kind, ...args) {
      if (String(kind).startsWith("webgl")) return null;
      return original.call(this, kind, ...args);
    };
  });
  await page.reload();
  await directory("Reader").waitFor();
  await directory("Reader").click();
  await page.getByRole("button", { name: /Enter home/ }).click();
  await page.getByText(/Room graphics are unavailable/).waitFor();
  await page.getByRole("link", { name: "Journal shelf · journal" }).click();
  await returnRoom();
  await back();
  await page.keyboard.press("Escape");
  await directory("Townhall").click();
  await page.getByRole("button", { name: /Enter Townhall/ }).click();
  await page.getByText(/Room graphics are unavailable/).waitFor();
  await back();
  assert.deepEqual(errors, []);
  await writeFile(
    output + "results.json",
    JSON.stringify(
      {
        hardware: {
          ...hardware,
          browserVersion: await browser.version(),
          host: {
            platform: platform(),
            release: release(),
            cpu: cpus()[0]?.model,
            memoryBytes: totalmem(),
          },
        },
        renderingSetup:
          "Isolated headed Chromium, default context via connectOverCDP(noDefaults:true); actual tab visibility without forced focus or CDP freezing",
        performanceMethod:
          "1440x1050 viewport, DPR1; 70 visible camera rotations per population/mode, first 10 excluded; 60 rendered samples. CPU time wraps renderer.render, intervals are consecutive actual render start timestamps; neither is GPU execution time. Draw calls include shadows. No idle samples.",
        measurements,
        matrix,
        fixture:
          "Synthetic full App, real Client/watch; no runtime or live data",
        desktop: [1440, 1050],
        mobile: [390, 844],
        checks: [
          "16 population/viewport combinations: 0/5/25/100 by 320/390/768/1440",
          "101 buildings reachable by Tab/Enter, Escape restores each opener",
          "actual exterior canvas picking and mode/camera preservation",
          "keyboard home to work records to room journey",
          "real touch at 320/390 DPR2 with reduced motion, records and overview return",
          "actual background tabs stop exterior loop and room redraws, foreground resumes",
          "static exterior draws no idle frames",
          "mid-journey reduced-motion change clears courier pixels",
          "eight room/record/view cycles dispose renderers, contexts and handlers without canvas accumulation",
          "exterior WebGL context loss retains contextual records and navigation",
          "home and Townhall real furniture picking",
          "six furniture record routes",
          "room identity/camera across records and snapshot rename",
          "disconnect/reconnect retains room",
          "household total update",
          "exterior camera and selection restored",
          "mobile furniture picking",
          "archive history and return",
          "WebGL context loss with records and return",
          "initial WebGL failure both rooms",
          "no horizontal mobile overflow",
          "no page errors",
        ],
        limits:
          "No real-host, deployment, daily-observation or native accessibility acceptance.",
      },
      null,
      2,
    ) + "\n",
  );
  console.log("Integrated Hamlet browser checks passed");
} finally {
  await browser.close();
  processBrowser.kill();
  await rm(profile, { recursive: true, force: true, maxRetries: 3 });
  await server.close();
}
