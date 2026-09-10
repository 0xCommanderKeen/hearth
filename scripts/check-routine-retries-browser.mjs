// Real RoutinePanel + Client + API + temporary SQLite; no provider or live data.
// PLAYWRIGHT_MODULE may name an installed Playwright ESM entry outside this checkout.
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
  ["run", "--frozen", "python", "-m", "tests.work.routine_browser_server"],
  { cwd: root, stdio: ["ignore", "pipe", "inherit"] },
);
const port = await new Promise((resolve, reject) => {
  const lines = createInterface({ input: backend.stdout });
  lines.once("line", (line) => resolve(Number(line)));
  backend.once("exit", (code) => reject(new Error(`Fixture exited: ${code}`)));
});
const fixture = `
import React from 'react';
import {createRoot} from 'react-dom/client';
import {RoutinePanel} from '/src/features/routines/Routines.tsx';
import {Client} from '/src/shared/client.ts';
const client = new Client('synthetic-routine-browser-token');
function App() {
 const [snapshot, setSnapshot] = React.useState(null);
 const [busy, setBusy] = React.useState(false);
 const [error, setError] = React.useState('');
 React.useEffect(() => { client.state().then(setSnapshot); }, []);
 const act = async op => {
  setBusy(true); setError('');
  try { await op(); setSnapshot(await client.state()); }
  catch(e) { setError(e.message); }
  finally { setBusy(false); }
 };
 return React.createElement(React.Fragment, null, React.createElement('p', {role:'alert'}, error), snapshot && React.createElement(RoutinePanel, {client, snapshot, busy, act}));
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
        name: "routine-retry-fixture",
        resolveId(id) {
          if (id === "/@routine-fixture.tsx") return "\0routine-fixture.tsx";
        },
        load(id) {
          if (id === "\0routine-fixture.tsx") return fixture;
        },
        configureServer(server) {
          server.middlewares.use("/__routine_check", async (_req, res) => {
            res.setHeader("Content-Type", "text/html");
            res.end(
              await server.transformIndexHtml(
                "/__routine_check",
                '<div id="root"></div><script type="module" src="/@routine-fixture.tsx"></script>',
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
  page.on("pageerror", (e) => errors.push(e.message));
  await page.goto(server.resolvedUrls.local[0] + "__routine_check");
  const input = page.getByLabel("The daily assignment");
  const button = page.getByRole("button", { name: "Schedule daily routine" });
  await input.fill("Synthetic daily report");
  await page.getByLabel("Timezone", { exact: true }).fill("UTC");
  const attempts = [];
  let lose = true;
  await page.route("**/api/routines/*", async (route) => {
    attempts.push({
      url: route.request().url(),
      body: route.request().postDataJSON(),
    });
    const response = await route.fetch();
    if (lose) {
      lose = false;
      assert.equal(response.status(), 200);
      await route.abort("failed");
    } else await route.fulfill({ response });
  });
  const state = async () =>
    (
      await page.request.get(server.resolvedUrls.local[0] + "api/state", {
        headers: { Authorization: "Bearer synthetic-routine-browser-token" },
      })
    ).json();
  await button.click();
  await page.getByRole("alert").filter({ hasText: /fetch/i }).waitFor();
  assert.equal((await state()).routines.length, 1);
  assert.equal(await input.inputValue(), "Synthetic daily report");
  await button.click();
  await page.waitForFunction(
    () => document.querySelector("textarea").value === "",
  );
  assert.deepEqual(attempts[1], attempts[0]);
  const tick = await page.request.post(
    server.resolvedUrls.local[0] + "__test/tick",
  );
  assert.equal((await tick.json()).tasks.length, 1);
  let saved = await state();
  assert.equal(saved.routines.length, 1);
  assert.equal(saved.occurrences.length, 1);
  assert.equal(saved.tasks.length, 1);
  // Deliberately scheduling the same payload again gets a distinct identity.
  await input.fill("Synthetic daily report");
  await button.click();
  await page.waitForFunction(
    () => document.querySelector("textarea").value === "",
  );
  assert.notEqual(attempts[2].url, attempts[0].url);
  // Another uncertain creation is changed concurrently before the retry.
  lose = true;
  await input.fill("Concurrent synthetic report");
  await button.click();
  await page.getByRole("alert").filter({ hasText: /fetch/i }).waitFor();
  const uncertain = attempts.at(-1);
  const edited = await page.request.post(uncertain.url, {
    headers: { Authorization: "Bearer synthetic-routine-browser-token" },
    data: { ...uncertain.body, enabled: false, expected_revision: 1 },
  });
  assert.equal(edited.status(), 200);
  await button.click();
  await page
    .getByRole("alert")
    .filter({ hasText: "revision conflict" })
    .waitFor();
  assert.deepEqual(attempts.at(-1), uncertain);
  saved = await state();
  assert.equal(saved.routines.length, 3);
  assert.equal(
    saved.routines.find((r) => uncertain.url.endsWith(r.id)).enabled,
    0,
  );
  assert.equal(await input.inputValue(), "Concurrent synthetic report");
  // A changed form is a new schedule, leaving the concurrent edit intact.
  await input.fill("Changed synthetic report");
  await button.click();
  await page.waitForFunction(
    () => document.querySelector("textarea").value === "",
  );
  assert.notEqual(attempts.at(-1).url, uncertain.url);
  assert.equal((await state()).routines.length, 4);
  assert.deepEqual(errors, []);
  console.log(
    JSON.stringify({
      browser: browser.version(),
      checks: [
        "commit then response loss",
        "same identity and payload on retry",
        "revision conflict reconciled",
        "one routine and occurrence task",
        "deliberate duplicate distinct",
        "concurrent edit preserved",
        "changed payload distinct",
      ],
      routineRequests: attempts.length,
    }),
  );
} finally {
  await browser?.close();
  await server?.close();
  backend.kill("SIGTERM");
}
