// Synthetic responses exercise the full App and real Client/watch and WebGL renderer.
import assert from "node:assert/strict";
import { mkdir, writeFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { createServer } from "../web/node_modules/vite/dist/node/index.js";
const { chromium } = await import(
  process.env.PLAYWRIGHT_MODULE || "playwright"
);
const output = fileURLToPath(
  new URL("../docs/evidence/hamlet-interiors-2026-09-09/", import.meta.url),
);
await mkdir(output, { recursive: true });
const fixture = `
import React from 'react'; import {createRoot} from 'react-dom/client';
import * as THREE from 'three'; import {App} from '/src/app/App.tsx';
window.THREE=THREE;window.measure={frames:0,renderers:[]};
THREE.Object3D.prototype.onBeforeRender=function(renderer,scene,camera){const m=window.measure;if(!m.renderers.includes(renderer))m.renderers.push(renderer);Object.assign(m,{renderer,scene,camera});m.frames++;};
window.stats=()=>{const m=window.measure;return {frames:m.frames,renderers:m.renderers.length,camera:m.camera?.uuid,position:m.camera?.position.toArray(),zoom:m.camera?.zoom};};
const resident=(id,name,presence)=>({id,name,presence,purpose:'Read fictional orchard notes.',revision:1,daily_limit:1000000,pause_reason:null,lifecycle:{resident_id:id,state:'ready',revision:1},skills:[]});
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
  server: { host: "127.0.0.1", port: 5196, strictPort: true },
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
const browser = await chromium.launch({ headless: true });
const page = await browser.newPage({ viewport: { width: 1440, height: 1050 } });
const errors = [];
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
try {
  await page.goto("http://127.0.0.1:5196/__panels");
  await page.waitForFunction(() => window.stats().camera);
  await page.getByText(/Connected · Synthetic/).waitFor();
  await page.getByRole("button", { name: "Rotate right" }).click();
  await settle();
  const overview = await stats();
  await enter("Reader");
  const roomStats = await stats();
  assert.equal(roomStats.renderers, 2);
  await page.screenshot({ path: output + "desktop-home.png", fullPage: true });
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
        fixture:
          "Synthetic full App, real Client/watch; no runtime or live data",
        desktop: [1440, 1050],
        mobile: [390, 844],
        checks: [
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
  console.log("Interior browser checks passed");
} finally {
  await browser.close();
  await server.close();
}
