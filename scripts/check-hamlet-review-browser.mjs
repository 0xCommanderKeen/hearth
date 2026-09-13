// Regression checks for the integrated village review, using synthetic records.
import assert from "node:assert/strict";
import { mkdir, writeFile } from "node:fs/promises";
import { createHamletPreview } from "./preview-hamlet.mjs";
const { chromium } = await import(
  process.env.PLAYWRIGHT_MODULE || "playwright"
);
const output = new URL(
  "../docs/evidence/hamlet-review-2026-09-13/",
  import.meta.url,
);
await mkdir(output, { recursive: true });
const server = await createHamletPreview(5193);
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
await page.addInitScript(() => {
  window.frameCount = 0;
  const raf = window.requestAnimationFrame.bind(window);
  window.requestAnimationFrame = (fn) =>
    raf((at) => {
      window.frameCount++;
      fn(at);
    });
});
const enter = async (name) => {
  await page
    .getByRole("button", { name: new RegExp(`Select ${name}`) })
    .last()
    .click();
  await page
    .getByRole("button", {
      name: new RegExp(name === "Mara" ? "Enter home" : `Enter ${name}`),
    })
    .click();
};
try {
  await page.goto("http://127.0.0.1:5193/__hamlet-preview");
  await page.waitForFunction(() => window.stats?.().ready);
  await page.evaluate(() => window.show(100));
  await page.getByLabel("Find a resident").scrollIntoViewIfNeeded();
  await page.waitForTimeout(1800);
  const idleBefore = await page.evaluate(() => window.frameCount);
  await page.waitForTimeout(400);
  const idleFrames = await page.evaluate(
    (n) => window.frameCount - n,
    idleBefore,
  );
  assert.equal(idleFrames, 0);
  await page.getByRole("button", { name: "Zoom in" }).click();
  await page.waitForTimeout(150);
  assert.ok(await page.evaluate((n) => window.frameCount > n, idleBefore));
  await page.evaluate(() => {
    window.show(8);
    for (let i = 0; i < 6; i++) window.change(i, "workshop");
  });
  await enter("Workshop");
  await page.waitForFunction(
    () => window.roomScene.name === "workroom:workshop",
  );
  await page.waitForTimeout(350);
  await page.evaluate(() => {
    window.labelMutations = 0;
    window.labelWatch = new MutationObserver(
      (m) => (window.labelMutations += m.length),
    );
    document.querySelectorAll(".room-person-label").forEach((n) =>
      window.labelWatch.observe(n, {
        attributes: true,
        attributeFilter: ["style"],
      }),
    );
    window.beforeArm = window.roomScene.children.find(
      (c) => c.userData.agentId,
    ).userData.arms[1].rotation.x;
  });
  await page.waitForTimeout(400);
  const labels = await page.evaluate(() => window.labelMutations);
  assert.equal(labels, 0);
  assert.equal(
    await page.evaluate(
      () =>
        window.beforeArm !==
        window.roomScene.children.find((c) => c.userData.agentId).userData
          .arms[1].rotation.x,
    ),
    true,
  );
  await page.evaluate(() => {
    Object.defineProperty(document, "hidden", {
      configurable: true,
      value: true,
    });
    document.dispatchEvent(new Event("visibilitychange"));
  });
  const hiddenBefore = await page.evaluate(() => window.frameCount);
  await page.waitForTimeout(180);
  assert.equal(
    await page.evaluate((n) => window.frameCount - n, hiddenBefore),
    0,
  );
  await page.evaluate(() => {
    delete document.hidden;
    document.dispatchEvent(new Event("visibilitychange"));
  });
  await page.setViewportSize({ width: 390, height: 900 });
  await page.waitForTimeout(150);
  assert.ok(await page.evaluate(() => window.labelMutations > 0));
  assert.equal(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
    true,
  );
  await page.screenshot({
    path: new URL("workroom-mobile.png", output).pathname,
    fullPage: true,
  });
  await page.getByRole("button", { name: "Back to village" }).click();
  await page.evaluate(() => {
    window.show();
    window.snapshot.residents[0].presence = "starting";
    window.publish();
  });
  await enter("Mara");
  await page.waitForFunction(
    () => window.roomScene.getObjectByName("agent:synthetic-0")?.visible,
  );
  await page.evaluate(() => window.connection(false));
  await page.waitForTimeout(100);
  assert.equal(
    await page.evaluate(
      () => window.roomScene.getObjectByName("agent:synthetic-0").visible,
    ),
    true,
  );
  await page.evaluate(() => {
    window.snapshot.residents[0].presence = "interrupted";
    window.publish();
  });
  await page.waitForFunction(
    () => !window.roomScene.getObjectByName("agent:synthetic-0").visible,
  );
  await page.getByRole("button", { name: "Back to village" }).click();
  await page.evaluate(() => {
    window.show();
    window.snapshot.residents[0].unresolved_runs = 2;
    window.snapshot.residents[0].lifecycle.state = "archived";
    window.snapshot.tasks = Array.from({ length: 6 }, (_, i) => ({
      id: String(i),
      resident_id: "synthetic-1",
      instruction: "Failed task " + i,
      status: "failed",
    }));
    window.publish();
  });
  const attention = page.getByRole("region", {
    name: "Work needing attention",
  });
  assert.match(
    await attention.getByRole("listitem").first().innerText(),
    /unresolved run/,
  );
  await attention
    .getByRole("button", { name: "Show all 7 attention items" })
    .click();
  assert.equal(await attention.getByRole("listitem").count(), 7);
  await attention.scrollIntoViewIfNeeded();
  await page.screenshot({
    path: new URL("attention-mobile.png", output).pathname,
    fullPage: true,
  });
  assert.deepEqual(errors, []);
  await writeFile(
    new URL("checks.json", output),
    JSON.stringify(
      {
        browser: browser.version(),
        renderer: "SwiftShader",
        idleFramesOver400ms: idleFrames,
        stationaryLabelStyleMutationsOver400ms: labels,
        checks: [
          "100-resident idle scene stops RAF and wakes on camera input",
          "Moving workroom figures do not rewrite stationary labels; resize updates them",
          "Preparing-at-home figure stays visible offline; unknown outcome hides it",
          "Archived holds precede failed tasks; all seven attention items accessible",
          "390px layouts without document overflow",
        ],
        errors,
      },
      null,
      2,
    ) + "\n",
  );
  console.log("Village review regressions passed");
} finally {
  await browser.close();
  await server.close();
}
