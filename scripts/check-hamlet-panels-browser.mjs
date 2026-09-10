// Synthetic responses exercise the full App and real Client/watch and WebGL renderer.
import assert from "node:assert/strict";
import { mkdir, writeFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { createServer } from "../web/node_modules/vite/dist/node/index.js";
const { chromium } = await import(
  process.env.PLAYWRIGHT_MODULE || "playwright"
);
const output = fileURLToPath(
  new URL("../docs/evidence/hamlet-panels-2026-09-09/", import.meta.url),
);
await mkdir(output, { recursive: true });
const fixture = `
import React from 'react'; import {createRoot} from 'react-dom/client';
import * as THREE from 'three'; import {App} from '/src/app/App.tsx';
window.measure={frames:0,renderers:[]};
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
  server: { host: "127.0.0.1", port: 5194, strictPort: true },
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
try {
  await page.goto("http://127.0.0.1:5194/__panels");
  await page.waitForFunction(() => window.stats().camera);
  await page.getByText(/Connected · Synthetic/).waitFor();
  await page.getByRole("button", { name: "Rotate right" }).click();
  await settle();
  const before = await stats();
  await directory("Reader").focus();
  await page.keyboard.press("Enter");
  await page.getByRole("dialog", { name: "Reader at home" }).waitFor();
  await settle();
  const selected = await stats();
  assert.notDeepEqual(selected.position, before.position);
  assert.equal(
    await page.evaluate(() =>
      window.requests.some((u) => u.includes("/api/runs/")),
    ),
    false,
  );
  await page.screenshot({
    path: output + "desktop-reader.png",
    fullPage: true,
  });
  await page.getByRole("link", { name: "Read result →" }).click();
  await page.getByText("Fictional orchard result: three apples.").waitFor();
  await settle();
  const hidden = await stats();
  await page.waitForTimeout(120);
  assert.equal((await stats()).frames, hidden.frames);
  await page.getByRole("link", { name: "← Return to village" }).click();
  await page.getByRole("dialog").waitFor();
  await settle();
  assert.equal((await stats()).renderers, 1);
  assert.deepEqual((await stats()).position, selected.position);
  await page.keyboard.press("Escape");
  await settle();
  assert.deepEqual((await stats()).position, before.position);
  assert(
    await directory("Reader").evaluate((el) => el === document.activeElement),
  );
  await directory("Reader").click();
  await page.evaluate(() => {
    window.snapshot.residents[0].lifecycle.state = "archived";
    window.snapshot.residents[0].unresolved_runs = 1;
    window.publish();
  });
  await page
    .getByRole("dialog")
    .getByText(/Archived with 1 unresolved/)
    .waitFor();
  await page.evaluate(() => window.disconnect());
  await page
    .getByRole("dialog")
    .getByText(/Disconnected/)
    .waitFor();
  await page.screenshot({
    path: output + "archive-disconnected.png",
    fullPage: true,
  });
  await page.evaluate(() => (window.offline = false));
  await page.getByText(/Connected · Synthetic/).waitFor();
  assert.equal(
    await page.getByRole("dialog").getAttribute("aria-label"),
    "Reader at home",
  );
  await page.evaluate(() => (window.failResult = true));
  await page.getByRole("link", { name: "Read result →" }).click();
  await page.getByRole("alert").waitFor();
  assert.match(
    await page.getByRole("alert").textContent(),
    /Synthetic result unavailable/,
  );
  await page.getByRole("link", { name: "← Return to village" }).click();
  await page.getByRole("dialog", { name: "Reader at home" }).waitFor();
  await page.getByRole("link", { name: "Journal →", exact: true }).click();
  await page.locator("#resident-journal details[open]").waitFor();
  await page.getByRole("button", { name: "Read journal", exact: true }).click();
  await page.getByText("No run has written an entry yet.").waitFor();
  await page.getByRole("link", { name: "← Return to village" }).click();
  await page.getByRole("button", { name: "Close village panel" }).click();
  await directory("Townhall").click();
  await page.getByRole("dialog", { name: "Townhall household" }).waitFor();
  await page.screenshot({
    path: output + "desktop-townhall.png",
    fullPage: true,
  });
  await page.getByRole("button", {name:"Close village panel"}).click();
  await page.locator('.scene-label').filter({hasText:'Keeper'}).click();
  await page.evaluate(()=>{window.snapshot.residents[1].name='Renamed keeper';window.publish()});
  await page.getByRole('dialog',{name:'Renamed keeper at home'}).waitFor();
  await page.keyboard.press('Escape');
  assert(await page.locator('.scene-canvas').evaluate(el=>el===document.activeElement));
  await page.evaluate(()=>{window.snapshot.residents[1].name='Keeper';window.publish()});
  await directory('Townhall').click();
  await page.setViewportSize({ width: 390, height: 844 });
  await page.getByRole("button", { name: "Close village panel" }).click();
  await directory("Townhall").click();
  await page.screenshot({
    path: output + "phone-townhall.png",
    fullPage: true,
  });
  const box = await page.getByRole("dialog").boundingBox();
  assert(box.y > 200);
  assert(box.y + box.height <= 845);
  assert(
    await page
      .getByRole("dialog")
      .evaluate((el) => el.scrollHeight > el.clientHeight),
  );
  await page
    .getByRole("dialog")
    .evaluate((el) => (el.scrollTop = el.scrollHeight));
  assert(await page.getByRole("dialog").evaluate((el) => el.scrollTop > 0));
  await page.getByRole("dialog").evaluate((el) => (el.scrollTop = 0));
  await page.getByRole("button", { name: "Close village panel" }).click();
  await directory("Keeper").click();
  await page
    .getByRole("dialog")
    .getByText(/Recorded status: paused/)
    .waitFor();
  await page.screenshot({
    path: output + "phone-resident.png",
    fullPage: true,
  });
  assert.equal((await stats()).renderers, 1);
  assert(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  );
  assert.deepEqual(errors, []);
  await writeFile(
    output + "results.json",
    JSON.stringify(
      {
        browser: browser.version(),
        checks: [
          "home focus and exact close restore",
          "on-demand result outside task window",
          "App record return preserves renderer and camera",
          "hidden scene stops drawing",
          "archive selection preserved",
          "disconnect and reconnect",
          "record failure and return",
          "journal deep link and explicit load",
          "Townhall summary",
          "mobile bottom sheet scroll and paused status",
          "removed label restores focus to canvas",
        ],
        renderers: (await stats()).renderers,
        errors,
      },
      null,
      2,
    ),
  );
  console.log("Full App panel browser journey passed");
} finally {
  await browser.close();
  await server.close();
}
