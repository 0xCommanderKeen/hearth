// Real WebGL checks against a synthetic fixture, without a runtime or personal data.
import assert from "node:assert/strict";
import { mkdir, writeFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { createHamletPreview } from "./preview-hamlet.mjs";
const { chromium } = await import(
  process.env.PLAYWRIGHT_MODULE || "playwright"
);
const output =
  process.env.HAMLET_EVIDENCE_DIR ||
  fileURLToPath(
    new URL("../docs/evidence/hamlet-living-2026-09-12/", import.meta.url),
  );
await mkdir(output, { recursive: true });
const server = await createHamletPreview(5196);
const browser = await chromium.launch({
  headless: true,
  ...(process.env.CHROMIUM_BINARY
    ? { executablePath: process.env.CHROMIUM_BINARY }
    : {}),
  args: [
    "--no-sandbox",
    "--use-gl=angle",
    "--use-angle=swiftshader",
    "--enable-unsafe-swiftshader",
  ],
});
const errors = [];
const result = {
  browser: browser.version(),
  renderer: "SwiftShader (software rendering; not phone GPU evidence)",
  checks: [],
  matrix: [],
};
const page = await browser.newPage({ viewport: { width: 1440, height: 1050 } });
page.on("pageerror", (error) => errors.push(error.message));
const frames = () =>
  page.evaluate(
    () =>
      new Promise((resolve) =>
        requestAnimationFrame(() => requestAnimationFrame(resolve)),
      ),
  );
const ready = () => page.waitForFunction(() => window.stats?.().ready);
const noActors = () =>
  page.waitForFunction(() => window.stats().actors.length === 0);
const picture = (name) =>
  page.screenshot({ path: output + name + ".png", fullPage: true });
try {
  await page.goto("http://127.0.0.1:5196/__hamlet-preview");
  await ready();
  await frames();
  assert.equal((await page.evaluate(() => window.stats())).actors.length, 0);
  result.checks.push(
    "Idle/ready residents are inside, with no exterior figures",
  );
  const before = await page.evaluate(() => window.stats());
  await page.getByRole("searchbox", { name: "Find a resident" }).fill("Mara");
  const choice = page
    .getByRole("group", { name: "Building directory" })
    .getByRole("button", { name: /Select Mara/ });
  assert.equal(
    await page
      .getByRole("group", { name: "Building directory" })
      .getByRole("button")
      .count(),
    5,
  );
  await choice.focus();
  await frames();
  assert.deepEqual(
    (await page.evaluate(() => window.stats())).position,
    before.position,
  );
  assert.equal(
    await page.locator(".scene-label.hovered").textContent(),
    "Maraready",
  );
  await choice.click();
  await page.getByRole("button", { name: "Enter home →" }).click();
  await page.waitForFunction(() =>
    window.roomScene?.children.some(
      (c) => c.userData.agentId === "synthetic-0" && c.visible,
    ),
  );
  await picture("home-interior");
  await page.getByRole("button", { name: "Back to village" }).click();
  await page.getByRole("button", { name: "Close village panel" }).click();
  await page.getByRole("searchbox", { name: "Find a resident" }).fill("");
  result.checks.push(
    "Search/focus highlight without camera motion; selected home opens with its resident inside",
  );
  await page.evaluate(() => window.change(0, "workshop"));
  await page.waitForFunction(() =>
    window
      .stats()
      .actors.some(
        (a) => a.visit?.to === "@workshop" && Math.abs(a.stride) > 0.01,
      ),
  );
  assert.equal(
    (await page.evaluate(() => window.stats())).actors.filter(
      (a) => a.id === "synthetic-0",
    ).length,
    1,
  );
  await picture("walk-to-workshop");
  await noActors();
  await page.evaluate(() => window.change(0, "research"));
  await page.waitForFunction(() =>
    window
      .stats()
      .actors.some(
        (a) => a.visit?.from === "@workshop" && a.visit.to === "@research",
      ),
  );
  await page
    .getByRole("group", { name: "Building directory" })
    .getByRole("button", { name: "Select Research House" })
    .click();
  await page.getByRole("link", { name: "Mara · Read memory" }).waitFor();
  await picture("research-work");
  await page.getByRole("button", { name: "Close village panel" }).click();
  await noActors();
  await page.evaluate(() => window.change(0, "post"));
  await page.waitForFunction(() =>
    window.stats().actors.some((a) => a.visit?.to === "@post"),
  );
  await noActors();
  await page.evaluate(() => window.letter());
  await page.waitForFunction(() =>
    window.stats().actors.some((a) => a.letter && a.id === "postal-courier"),
  );
  await picture("postal-courier");
  await noActors();
  await page.evaluate(() => window.home(0));
  await page.waitForFunction(() =>
    window.stats().actors.some((a) => a.visit?.to === "synthetic-0"),
  );
  await noActors();
  result.checks.push(
    "Recorded work visits Workshop → Research House → Post Office → home, with animated limbs and separate postal courier",
  );
  await page.evaluate(() => window.change(0, "research"));
  await page.waitForFunction(() => window.stats().actors.length > 0);
  await page.emulateMedia({ reducedMotion: "reduce" });
  await noActors();
  await page.evaluate(() => window.home(0));
  await frames();
  assert.equal((await page.evaluate(() => window.stats())).actors.length, 0);
  await page.emulateMedia({ reducedMotion: "no-preference" });
  await page.evaluate(() => window.change(0, "workshop"));
  await page.waitForFunction(() => window.stats().actors.length > 0);
  await page.evaluate(() => window.connection(false));
  await noActors();
  await page.evaluate(() => {
    window.change(0, "post");
    window.connection(true);
  });
  await frames();
  assert.equal((await page.evaluate(() => window.stats())).actors.length, 0);
  result.checks.push(
    "Reduced motion and disconnect clear journeys; reconnection does not replay them",
  );
  await page.evaluate(() => window.show());
  await frames();
  const plots = await page.evaluate(() => window.stats().homes);
  await page.evaluate(() => {
    window.snapshot.residents.reverse();
    window.publish();
  });
  await frames();
  assert.deepEqual(await page.evaluate(() => window.stats().homes), plots);
  result.checks.push("Resident reorder preserves home and civic plots");
  for (const count of [0, 8, 25, 100])
    for (const width of [320, 390, 768, 1440]) {
      await page.setViewportSize({ width, height: 1050 });
      await page.evaluate((n) => window.show(n), count);
      await frames();
      assert.equal(
        await page
          .getByRole("group", { name: "Building directory" })
          .getByRole("button")
          .count(),
        count + 4,
      );
      assert(
        await page.evaluate(
          () => document.documentElement.scrollWidth <= innerWidth,
        ),
      );
      const state = await page.evaluate(() => window.stats());
      assert.equal(state.actors.length, 0);
      assert.equal(state.homes.length, count + 5);
      await picture(`village-${count}-${width}`);
      result.matrix.push({ count, width, calls: state.calls });
    }
  await page.setViewportSize({ width: 390, height: 844 });
  await page.evaluate(() => window.show());
  await frames();
  await page
    .getByRole("group", { name: "Building directory" })
    .getByRole("button", { name: "Select Post Office" })
    .click();
  await page.getByRole("dialog", { name: "Post Office" }).waitFor();
  await picture("phone-post-office");
  await page.getByRole("button", { name: "Close village panel" }).click();
  await page.getByText("View & controls", { exact: true }).click();
  await page.getByRole("checkbox", { name: /Lighter graphics/ }).check();
  await page.getByText("View & controls", { exact: true }).click();
  await frames();
  assert.equal((await page.evaluate(() => window.stats())).actors.length, 0);
  await page.evaluate(() =>
    window.measure.renderer
      .getContext()
      .getExtension("WEBGL_lose_context")
      .loseContext(),
  );
  await page
    .getByText("3D is unavailable in this browser.", { exact: false })
    .waitFor();
  await page
    .getByRole("group", { name: "Building directory" })
    .getByRole("button", { name: "Select Workshop" })
    .click();
  await page.getByRole("dialog", { name: "Workshop" }).waitFor();
  result.checks.push(
    "0/8/25/100 residents fit desktop/mobile; civic panels and search survive graphics loss",
  );
  assert.deepEqual(errors, []);
  await writeFile(
    output + "results.json",
    JSON.stringify(result, null, 2) + "\n",
  );
  console.log(JSON.stringify(result, null, 2));
} finally {
  await browser.close();
  await server.close();
}
