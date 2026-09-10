// Production Client.watch + task content UI + real SSE/SQLite, in Chromium.
import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { createInterface } from "node:readline";
import { fileURLToPath } from "node:url";
import { createServer } from "../web/node_modules/vite/dist/node/index.js";
const { chromium } = await import(
  process.env.PLAYWRIGHT_MODULE || "playwright"
);
const root = fileURLToPath(new URL("../", import.meta.url));
const backend = spawn(
  "uv",
  [
    "run",
    "--frozen",
    "python",
    "-m",
    "tests.api.large_snapshot_browser_server",
  ],
  { cwd: root, stdio: ["ignore", "pipe", "inherit"] },
);
const port = await new Promise((resolve, reject) => {
  createInterface({ input: backend.stdout }).once("line", (line) =>
    resolve(Number(line)),
  );
  backend.once("exit", (code) => reject(new Error(`Fixture exited: ${code}`)));
});
const fixture = `
import React from 'react';
import {createRoot} from 'react-dom/client';
import {Client} from '/src/shared/client.ts';
import {TaskInstruction} from '/src/features/tasks/TaskInstruction.tsx';
window.check = {states: [], connections: [], requests: 0, details: 0, bytes: 0, fragmented: 0, disconnect: false};
const originalFetch = window.fetch;
window.fetch = async (...args) => {
 const url = String(args[0]);
 if (/api\\/tasks\\//.test(url)) window.check.details++;
 if (!url.startsWith('/api/events')) return originalFetch(...args);
 window.check.requests++;
 const response = await originalFetch(...args);
 const reader = response.body.getReader();
 // Only reshape real server bytes: deliberately split UTF-8 and delimiters.
 const body = new ReadableStream({
  async pull(controller) {
   const part = await reader.read();
   if (window.check.disconnect) {
    window.check.disconnect = false;
    await reader.cancel(); controller.error(new Error('Synthetic connection loss')); return;
   }
   if (part.done) {controller.close(); return;}
   window.check.bytes += part.value.length;
   for (let i = 0; i < part.value.length;) {
    const size = i < 2048 ? 1 : 8191;
    controller.enqueue(part.value.slice(i, i + size)); i += size; window.check.fragmented++;
   }
  }, cancel() {return reader.cancel();}
 });
 return new Response(body, {status: response.status, headers: response.headers});
};
const client = new Client('synthetic-large-snapshot-token');
function App() {
 const [snapshot, setSnapshot] = React.useState(null);
 React.useEffect(() => {
  const abort = new AbortController();
  client.watch(abort.signal, s => {window.check.states.push({cursor:s.cursor, tasks:s.tasks.length, unicode:s.tasks.filter(t=>t.instruction.startsWith('🦔')).length}); setSnapshot(s);}, c => window.check.connections.push(c));
  return () => abort.abort();
 }, []);
 const task = snapshot?.tasks.find(t=>t.instruction.startsWith('🦔')) || snapshot?.tasks[0];
 return task && React.createElement(TaskInstruction, {key:snapshot.epoch+':'+task.id, client, task});
}
createRoot(document.getElementById('root')).render(React.createElement(App));
`;
let browser, server;
try {
  server = await createServer({
    root: root + "web",
    server: {
      host: "127.0.0.1",
      port: 0,
      proxy: {
        "/api": `http://127.0.0.1:${port}`,
        "/__test": `http://127.0.0.1:${port}`,
      },
    },
    plugins: [
      {
        name: "large-snapshot-fixture",
        resolveId(id) {
          if (id === "/@large-fixture.tsx") return "\0large-fixture.tsx";
        },
        load(id) {
          if (id === "\0large-fixture.tsx") return fixture;
        },
        configureServer(server) {
          server.middlewares.use("/__large_check", async (_req, res) => {
            res.setHeader("Content-Type", "text/html");
            res.end(
              await server.transformIndexHtml(
                "/__large_check",
                '<div id="root"></div><script type="module" src="/@large-fixture.tsx"></script>',
              ),
            );
          });
        },
      },
    ],
  });
  await server.listen();
  browser = await chromium.launch({ headless: true });
  const page = await browser.newPage();
  const errors = [];
  page.on("pageerror", (error) => errors.push(error.message));
  const url = server.resolvedUrls.local[0];
  await page.goto(url + "__large_check");
  await page.waitForFunction(() => window.check.connections.includes(true));
  const add = async (query = "") => {
    const response = await page.request.post(url + "__test/add" + query);
    assert.equal(response.status(), 200);
  };
  const waitStates = async (count) =>
    page.waitForFunction((count) => window.check.states.length >= count, count);
  await add();
  await waitStates(2);
  await add();
  await waitStates(3);
  assert.equal(await page.evaluate(() => window.check.requests), 1);
  assert.equal(await page.evaluate(() => window.check.details), 0);
  await page.getByRole("button", { name: "Read full instructions" }).click();
  await page.getByRole("button", { name: "Hide full instructions" }).waitFor();
  assert.equal((await page.locator("p").textContent()).length, 32_000);
  await add("?unicode=true&amount=100");
  await waitStates(4);
  assert.equal(
    await page.evaluate(() => window.check.states.at(-1).unicode),
    100,
  );
  await page.getByRole("button", { name: "Read full instructions" }).click();
  await page.getByRole("button", { name: "Hide full instructions" }).waitFor();
  const full = "🦔漢\n".repeat(32_000);
  // Python's semantic length counts code points, so slice by code points here too.
  assert.equal(
    await page.locator("p").textContent(),
    Array.from(full).slice(0, 32_000).join(""),
  );
  const cursor = await page.evaluate(() => window.check.states.at(-1).cursor);
  await page.evaluate(() => {
    window.check.disconnect = true;
  });
  await add("?unicode=true");
  await page.waitForFunction(
    (cursor) =>
      window.check.requests === 2 && window.check.states.at(-1).cursor > cursor,
    cursor,
  );
  const restoredCount = await page.evaluate(() => window.check.states.length);
  await add("?unicode=true");
  await waitStates(restoredCount + 1);
  await add("?unicode=true");
  await waitStates(restoredCount + 2);
  const result = await page.evaluate(() => window.check);
  assert.equal(result.requests, 2);
  assert.deepEqual(result.connections, [true, false, true]);
  assert.deepEqual(errors, []);
  console.log(
    JSON.stringify(
      {
        browser: browser.version(),
        ...result,
        checks: [
          "65 accepted 32k ASCII tasks",
          "successive live updates",
          "100 accepted 32k Unicode tasks",
          "exact full content on demand",
          "byte-fragmented production SSE",
          "reconnect then successive updates",
        ],
      },
      null,
      2,
    ),
  );
} finally {
  await browser?.close();
  await server?.close();
  backend.kill("SIGTERM");
}
