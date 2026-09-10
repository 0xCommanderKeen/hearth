// Synthetic responses exercise the full App and real Client/watch and WebGL renderer.
import assert from "node:assert/strict";
import { mkdir, writeFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { createServer } from "../web/node_modules/vite/dist/node/index.js";
const { chromium } = await import(
  process.env.PLAYWRIGHT_MODULE || "playwright"
);
const output = fileURLToPath(
  new URL("../docs/evidence/hamlet-activity-2026-09-09/", import.meta.url),
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
window.letter=(id,from=null,to='reader',at=100)=>({kind:'letter_sent',task_id:id,at,from_resident_id:from,to_resident_id:to,title:id,state:'pending',root_task_id:id,depth:0});window.snapshot.letters=[window.letter('retained')];
let stream; window.requests=[];window.failResult=false;
window.publish=(reset=false)=>{window.snapshot.cursor++;stream?.enqueue(new TextEncoder().encode((reset?'event: reset\\n':'')+'data: '+JSON.stringify(window.snapshot)+'\\n\\n'));};
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
  server: { host: "127.0.0.1", port: 5197, strictPort: true },
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

const journeys = () =>
  page.evaluate(() =>
    window.measure.scene.children
      .filter((o) => o.userData.letter)
      .map((o) => ({
        id: o.userData.letter,
        x: o.position.x,
        z: o.position.z,
      })),
  );
const sample = () =>
  page.evaluate(
    () =>
      new Promise((resolve) => {
        const samples = [];
        let frames = 0;
        function frame() {
          samples.push(
            window.measure.scene.children
              .filter((o) => o.userData.letter)
              .map((o) => ({
                id: o.userData.letter,
                x: o.position.x,
                z: o.position.z,
              })),
          );
          if (++frames < 20) requestAnimationFrame(frame);
          else resolve(samples);
        }
        requestAnimationFrame(frame);
      }),
  );
const noTravel = async () =>
  assert((await sample()).every((frame) => frame.length === 0));
try {
  await page.goto("http://127.0.0.1:5197/__panels");
  await page.getByText(/Connected · Synthetic/).waitFor();
  await page.waitForFunction(() => window.stats().camera);
  await noTravel();
  assert.equal(await page.locator(".scene-post time").count(), 1);
  await page.evaluate(() => window.publish());
  await noTravel();
  await page.evaluate(() => {
    window.snapshot.residents[0].presence = "running";
    window.snapshot.letters.unshift(window.letter("fresh"));
    window.publish();
  });
  await page
    .locator(".scene-label")
    .filter({ hasText: "Reader" })
    .filter({ hasText: "running" })
    .waitFor();
  await page.waitForFunction(() =>
    window.measure.scene.children.some(
      (o) => o.userData.letter === "letter_sent:fresh",
    ),
  );
  await page.screenshot({ path: output + "fresh-street.png", fullPage: true });
  const samples = await page.evaluate(
    () =>
      new Promise((resolve) => {
        const points = [];
        function frame() {
          const person = window.measure.scene.children.find(
            (o) => o.userData.letter === "letter_sent:fresh",
          );
          if (!person) {
            resolve(points);
            return;
          }
          const { x, z } = person.position;
          const roads = [];
          window.measure.scene.traverse((o) => {
            if (o.isMesh && o.material.color?.getHexString() === "dbc8a3")
              roads.push(o);
          });
          const onStreet = roads.some(
            (o) =>
              Math.abs(x - o.position.x) <=
                o.geometry.parameters.width / 2 + 0.001 &&
              Math.abs(z - o.position.z) <=
                o.geometry.parameters.depth / 2 + 0.001,
          );
          points.push({ x, z, onStreet });
          requestAnimationFrame(frame);
        }
        frame();
      }),
  );
  assert(samples.length > 10);
  assert(samples.every((p) => p.onStreet));
  assert(new Set(samples.map((p) => p.x.toFixed(1))).size > 2);
  assert(new Set(samples.map((p) => p.z.toFixed(1))).size > 2);
  await page.screenshot({
    path: output + "recorded-status.png",
    fullPage: true,
  });
  await page.evaluate(() => window.publish());
  await noTravel();
  await page.evaluate(() => {
    window.disconnect();
    window.snapshot.letters.unshift(
      window.letter("offline", null, "reader", 101),
    );
  });
  await page.getByText(/Recent post · disconnected/).waitFor();
  await page
    .locator(".scene-label")
    .filter({ hasText: "Reader" })
    .filter({ hasText: "disconnected" })
    .waitFor();
  await page.screenshot({
    path: output + "disconnected-history.png",
    fullPage: true,
  });
  await page.evaluate(() => (window.offline = false));
  await page.getByText(/Connected · Synthetic/).waitFor();
  await noTravel();
  await page.evaluate(() => {
    window.snapshot.cursor = 0;
    window.snapshot.letters.unshift(
      window.letter("reset", null, "reader", 102),
    );
    window.publish(true);
  });
  await page.locator(".scene-post").filter({ hasText: "reset" }).waitFor();
  await noTravel();
  await page.evaluate(() => {
    window.snapshot.epoch = "reset-epoch";
    window.snapshot.letters.unshift(
      window.letter("epoch", null, "reader", 103),
    );
    window.publish(true);
  });
  await noTravel();
  await page.emulateMedia({ reducedMotion: "reduce" });
  await page.evaluate(() => {
    window.snapshot.letters.unshift(
      window.letter("reduced", null, "reader", 104),
    );
    window.publish();
  });
  await noTravel();
  await page.emulateMedia({ reducedMotion: "no-preference" });
  await noTravel();
  await directory("Reader").click();
  await page.getByRole("button", { name: /Enter home/ }).click();
  await page.evaluate(() => {
    window.snapshot.letters.unshift(
      window.letter("inside", null, "reader", 105),
    );
    window.publish();
  });
  await settle();
  await page.getByRole("button", { name: "Back to village" }).click();
  await settle();
  await noTravel();
  await page.keyboard.press("Escape");
  await page.evaluate(() => {
    window.snapshot.residents[1].lifecycle.state = "archived";
    window.snapshot.letters.unshift(
      window.letter("archived", null, "keeper", 106),
      window.letter("missing", "absent", "reader", 106),
    );
    window.publish();
  });
  await noTravel();
  for (const status of ["unknown", "failed", "paused"]) {
    await page.evaluate((status) => {
      window.snapshot.residents[0].presence = status;
      window.publish();
    }, status);
    await page
      .locator(".scene-label")
      .filter({ hasText: "Reader" })
      .filter({ hasText: status })
      .waitFor();
  }
  await page.screenshot({
    path: output + "finite-history.png",
    fullPage: true,
  });
  assert.deepEqual(errors, []);
  await writeFile(
    output + "results.json",
    JSON.stringify(
      {
        fixture:
          "Synthetic full App, real Client.watch and WebGL; no runtime/live data",
        checks: [
          "initial and repeated history never travels",
          "fresh letter completes along rendered street rectangles",
          "recorded status updates",
          "disconnected labels and timestamped history",
          "reconnect history no travel",
          "same-epoch cursor reset accepted without travel",
          "epoch baseline",
          "reduced motion consumes events",
          "interior events no replay on exit",
          "missing and archived doors text only",
          "unknown, failed and paused text labels",
          "no page errors",
        ],
        freshRouteSamples: samples,
        limits:
          "No real-host, deployment, daily observation or native accessibility acceptance.",
      },
      null,
      2,
    ) + "\n",
  );
  console.log("Activity browser checks passed");
} finally {
  await browser.close();
  await server.close();
}
