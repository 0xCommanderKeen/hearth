// Synthetic shared-workroom checks with real WebGL.
import assert from "node:assert/strict";
import { mkdir, writeFile } from "node:fs/promises";
import { createHamletPreview } from "./preview-hamlet.mjs";
const { chromium } = await import(
  process.env.PLAYWRIGHT_MODULE || "playwright"
);
const output = new URL(
  "../docs/evidence/hamlet-workrooms-2026-09-12/",
  import.meta.url,
);
await mkdir(output, { recursive: true });
const server = await createHamletPreview(5195);
const browser = await chromium.launch({
  headless: true,
  executablePath: process.env.CHROMIUM_BINARY,
  args: [
    "--no-sandbox",
    "--use-gl=angle",
    "--use-angle=swiftshader",
    "--enable-unsafe-swiftshader",
  ],
});
const page = await browser.newPage({ viewport: { width: 1440, height: 1100 } });
const errors = [];
page.on("pageerror", (e) => errors.push(e.message));
const checks = [];
const enter = async (name) => {
  await page
    .getByRole("button", { name: `Select ${name}`, exact: true })
    .last()
    .click();
  await page.getByRole("button", { name: new RegExp(`Enter ${name}`) }).click();
  await page.waitForFunction(() =>
    window.roomScene?.name.startsWith("workroom:"),
  );
};
const back = () =>
  page.getByRole("button", { name: "Back to village" }).click();
const workers = () =>
  page.evaluate(() =>
    window.roomScene.children
      .filter((c) => c.userData.agentId)
      .map((c) => ({
        id: c.userData.workerId,
        arm: c.userData.arms[0].rotation.x,
      })),
  );
const snap = async (name) =>
  page.screenshot({
    path: new URL(name + ".png", output).pathname,
    fullPage: true,
  });
try {
  await page.goto("http://127.0.0.1:5195/__hamlet-preview");
  await page.waitForFunction(() => window.stats?.().ready);
  await page.getByRole("button", { name: "Show shared workrooms" }).click();
  for (const [name, kind] of [
    ["Workshop", "workshop"],
    ["Research House", "research"],
    ["Post Office", "post"],
    ["Townhall", "townhall"],
  ]) {
    await enter(name);
    await page.waitForFunction(
      (kind) =>
        window.roomScene.name === `workroom:${kind}` &&
        window.roomScene.children.filter((c) => c.userData.agentId).length ===
          3,
      kind,
    );
    assert.equal((await workers()).length, 3);
    await page.locator(".room-person-label").nth(1).click();
    assert.equal(
      await page.locator(".workroom-detail h4").innerText(),
      await page.locator(".room-person-label").nth(1).innerText(),
    );
    await snap(kind + "-desktop");
    checks.push(
      `${name}: three figures, selectable details, distinctive furniture`,
    );
    await back();
  }
  await page.evaluate(() => {
    window.show(8);
    for (let i = 0; i < 8; i++) window.change(i, "workshop");
  });
  await enter("Workshop");
  await page.waitForFunction(
    () =>
      window.roomScene.children.filter((c) => c.userData.agentId).length === 6,
  );
  await page.getByRole("button", { name: "Next residents" }).click();
  await page.waitForFunction(
    () =>
      window.roomScene.children.filter((c) => c.userData.agentId).length === 2,
  );
  await page.getByRole("button", { name: "Previous residents" }).click();
  await page.waitForFunction(
    () =>
      window.roomScene.children.filter((c) => c.userData.agentId).length === 6,
  );
  checks.push("Eight workers accessible across two pages; six figures maximum");
  // A mesh click uses the same selection as roster and name labels.
  const spot = await page.evaluate(() => {
    const p = window.roomScene.children.find(
      (c) => c.userData.agentId === "synthetic-1",
    );
    const v = p.position
      .clone()
      .add(new window.THREE.Vector3(0, 0.8, 0))
      .project(window.roomCamera);
    const r = window.roomRenderer.domElement.getBoundingClientRect();
    return {
      x: r.x + ((v.x + 1) * r.width) / 2,
      y: r.y + ((1 - v.y) * r.height) / 2,
    };
  });
  await page.mouse.click(spot.x, spot.y);
  assert.equal(await page.locator(".workroom-detail h4").innerText(), "Tavi");
  const first = (await workers())[0].arm;
  await page.waitForFunction(
    (first) =>
      window.roomScene.children.find((c) => c.userData.agentId).userData.arms[0]
        .rotation.x !== first,
    first,
  );
  await page.emulateMedia({ reducedMotion: "reduce" });
  await page.waitForTimeout(120);
  const still = await workers();
  await page.waitForTimeout(180);
  assert.deepEqual(await workers(), still);
  await page.emulateMedia({ reducedMotion: "no-preference" });
  await page.evaluate(() => {
    window.savedRoomRenderer = window.roomRenderer;
    window.connection(false);
  });
  await page.waitForTimeout(120);
  const offline = await workers();
  await page.waitForTimeout(180);
  assert.deepEqual(await workers(), offline);
  await page.evaluate(() => {
    window.home(1);
    window.connection(true);
  });
  await page.waitForFunction(
    () =>
      !window.roomScene.children.some(
        (c) => c.userData.agentId === "synthetic-1",
      ),
  );
  assert.equal(
    await page.evaluate(() => window.savedRoomRenderer === window.roomRenderer),
    true,
  );
  assert.match(
    await page.locator(".workroom-roster").innerText(),
    /selected resident is no longer/,
  );
  checks.push(
    "Mesh picking, working motion, reduced-motion/offline pause, live departure without renderer replacement",
  );
  for (const width of [390, 320]) {
    await page.setViewportSize({ width, height: 1000 });
    await page.waitForTimeout(150);
    assert.equal(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth,
      ),
      true,
    );
    assert.equal(await page.locator(".room-person-label").count(), 6);
    await snap("workshop-" + width);
  }
  await page.evaluate(() =>
    window.roomRenderer
      .getContext()
      .getExtension("WEBGL_lose_context")
      .loseContext(),
  );
  await page
    .getByText("Room graphics are unavailable.", { exact: false })
    .waitFor();
  await page.locator(".workroom-people button").first().click();
  assert.equal(
    await page.getByRole("link", { name: "Open run & task →" }).count(),
    1,
  );
  await back();
  checks.push(
    "320/390px layouts and graphics-loss roster/records/exit fallback",
  );
  assert.deepEqual(errors, []);
  await writeFile(
    new URL("checks.json", output),
    JSON.stringify(
      {
        browser: browser.version(),
        renderer: "SwiftShader; synthetic data",
        checks,
        errors,
      },
      null,
      2,
    ) + "\n",
  );
  console.log(JSON.stringify({ checks, errors }, null, 2));
} finally {
  await browser.close();
  await server.close();
}
