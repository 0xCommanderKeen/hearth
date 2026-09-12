// Synthetic visual preview and browser fixture. Never imported by the shipped app.
import { createServer } from "../web/node_modules/vite/dist/node/index.js";
import { fileURLToPath } from "node:url";
import { resolve } from "node:path";

const fixture = `
import React from 'react';
import {createRoot} from 'react-dom/client';
import * as THREE from 'three';
import {Hamlet} from '/src/features/hamlet/Hamlet.tsx';
import '/src/app/style.css';
window.THREE=THREE; window.measure={};
THREE.Object3D.prototype.onBeforeRender=function(renderer,scene,camera){
 if(scene.children.some(c=>c.name==='village')) Object.assign(window.measure,{renderer,scene,camera});
 else window.roomScene=scene;
};
window.stats=()=>{
 const {scene,camera,renderer}=window.measure;
 const actors=scene?.children.filter(c=>c.userData.agentId)??[];
 return {ready:!!scene, camera:camera?.uuid, position:camera?.position.toArray(), zoom:camera?.zoom,
 actors:actors.map(c=>({id:c.userData.agentId,visit:c.userData.visit,letter:c.userData.letter,position:c.position.toArray(),stride:c.userData.legs[0].rotation.x})),
 homes:scene?.getObjectByName('village')?.children.filter(c=>c.userData.buildingId).map(c=>({id:c.userData.buildingId,position:c.position.toArray()})),calls:renderer?.info.render.calls};
};
const root=createRoot(document.getElementById('root'));
let online=true, epoch=0, sequence=1, timers=[];
const names=['Mara','Tavi','Nell','Oren','Iris','Bram','Sela','Pip'];
const purposes=['Keep the village archive orderly.','Collect useful observations.','Arrange resident letters.','Make practical guides.'];
const runtimes={default:'fixture',configured:['fixture'],kinds:{fixture:{label:'Synthetic preview',live:false}}};
const render=()=>root.render(React.createElement(Hamlet,{snapshot:structuredClone(window.snapshot),connected:online}));
window.show=(count=8)=>{
 window.snapshot={schema_version:1,epoch:'visual-demo-'+ ++epoch,cursor:sequence++,runtimes,
 residents:Array.from({length:count},(_,i)=>({id:'synthetic-'+i,name:names[i]??'Resident '+(i+1),purpose:purposes[i%4],presence:'ready',revision:1,daily_limit:1000000,pause_reason:null,lifecycle:{state:'ready',resident_id:'synthetic-'+i},skills:[]})),
 runs:[],tasks:[],letters:[],activity:[],notifications:[],limits:{tasks:100,runs:100,letters:30,activity:30,notifications:100}};
 online=true;render();
};
window.publish=()=>{window.snapshot.cursor=sequence++;render();};
window.connection=(connected)=>{online=connected;window.publish();};
window.change=(index,place)=>{
 const resident=window.snapshot.residents[index];resident.presence='running';
 const run={id:'demo-run-'+index,resident_id:resident.id,task_id:'demo-task-'+index,status:'running',artifact_id:null,usage_known:0,cancellation_requested:0,actual_cost:null,
 action:place?{place,label:({research:'Read memory',post:'Read letters',workshop:'Wrote a journal entry',townhall:'Assigned work'})[place],at:Math.floor(Date.now()/1000),sequence:sequence}:null};
 window.snapshot.runs=window.snapshot.runs.filter(r=>r.resident_id!==resident.id).concat(run);
 window.snapshot.tasks=window.snapshot.tasks.filter(t=>t.resident_id!==resident.id).concat({id:run.task_id,resident_id:resident.id,instruction:'Synthetic visual demonstration',status:'running'});
 window.publish();
};
window.home=(index)=>{window.snapshot.residents[index].presence='ready';window.snapshot.runs=window.snapshot.runs.filter(r=>r.resident_id!==window.snapshot.residents[index].id);window.publish();};
window.letter=()=>{window.snapshot.letters.unshift({kind:'letter_sent',task_id:'letter-'+sequence,at:Math.floor(Date.now()/1000)+sequence,from_resident_id:window.snapshot.residents[0].id,to_resident_id:window.snapshot.residents[1].id,title:'Synthetic garden notes'});window.snapshot.letters=window.snapshot.letters.slice(0,30);window.publish();};
window.play=()=>{
 timers.forEach(clearTimeout);window.show();
 document.getElementById('play').disabled=true;
 document.getElementById('demo-status').textContent='Mara visits the Workshop, Research House and Post Office, then returns home.';
 for(const [at,fn] of [[500,()=>window.change(0,'workshop')],[5500,()=>window.change(0,'research')],[10500,()=>window.change(0,'post')],[15500,()=>window.letter()],[20500,()=>window.home(0)],[25500,()=>{document.getElementById('play').disabled=false;document.getElementById('demo-status').textContent='Everyone is home. Play again or inspect a building.';}]]) timers.push(setTimeout(fn,at));
};
document.getElementById('play').onclick=window.play;
window.show();
`;
export async function createHamletPreview(port = 5197) {
  const server = await createServer({
    root: fileURLToPath(new URL("../web", import.meta.url)),
    server: { host: "127.0.0.1", port, strictPort: true },
    plugins: [
      {
        name: "hamlet-visual-preview",
        resolveId(id) {
          if (id === "/@hamlet-preview") return "\0hamlet-preview";
        },
        load(id) {
          if (id === "\0hamlet-preview") return fixture;
        },
        configureServer(server) {
          server.middlewares.use("/__hamlet-preview", async (_req, res) => {
            res.setHeader("Content-Type", "text/html");
            res.end(
              await server.transformIndexHtml(
                "/__hamlet-preview",
                `<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Hamlet visual preview</title></head><body><main style="max-width:1400px;margin:0 auto;padding:20px"><header class="page-head"><div><span class="eyebrow">SYNTHETIC VISUAL PREVIEW</span><h1>Hamlet</h1><p>Explore homes and work buildings. These residents and actions are fictional.</p></div><button id="play" class="primary">Play activity</button></header><p id="demo-status" role="status">Everyone is home. Play activity to watch the village.</p><div id="root"></div></main><script type="module" src="/@hamlet-preview"></script></body></html>`,
              ),
            );
          });
        },
      },
    ],
  });
  await server.listen();
  return server;
}
if (
  process.argv[1] &&
  resolve(process.argv[1]) === fileURLToPath(import.meta.url)
) {
  const index = process.argv.indexOf("--port");
  const port = index < 0 ? 5197 : Number(process.argv[index + 1]);
  if (!Number.isInteger(port) || port < 1024 || port > 65535)
    throw Error("Invalid preview port");
  await createHamletPreview(port);
  console.log(
    `Hamlet visual preview: http://127.0.0.1:${port}/__hamlet-preview`,
  );
}
