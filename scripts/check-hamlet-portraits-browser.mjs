import assert from "node:assert/strict";
import { mkdir, writeFile } from "node:fs/promises";
import { createHamletPreview } from "./preview-hamlet.mjs";
const { chromium } = await import(
  process.env.PLAYWRIGHT_MODULE || "playwright"
);
const output = new URL(
  "../docs/evidence/hamlet-portraits-2026-09-13/",
  import.meta.url,
);
await mkdir(output, { recursive: true });
const server = await createHamletPreview(5194);
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
try {
  await page.goto("http://127.0.0.1:5194/__hamlet-preview");
  await page
    .getByRole("button", { name: "Character portraits", exact: true })
    .click();
  await page.waitForFunction(
    () => document.querySelectorAll("#portrait-gallery img").length === 12,
  );
  const urls = await page
    .locator("#portrait-gallery img")
    .evaluateAll((imgs) => imgs.map((i) => i.src));
  assert.equal(new Set(urls).size, 12);
  await page.screenshot({
    path: new URL("gallery-desktop.png", output).pathname,
    fullPage: true,
  });
  await page
    .getByRole("button", { name: "Character portraits", exact: true })
    .click();
  await page
    .getByRole("button", { name: /Select Mara/ })
    .last()
    .click();
  await page.waitForFunction(
    () => !!document.querySelector(".village-panel img"),
  );
  assert.equal(
    await page.locator(".village-panel img").getAttribute("src"),
    urls[0],
  );
  await page.evaluate(() => {
    window.snapshot.residents[0].name = "Mara renamed";
    window.change(0, "research");
  });
  assert.equal(
    await page.locator(".village-panel img").getAttribute("src"),
    urls[0],
  );
  await page.screenshot({
    path: new URL("resident-panel.png", output).pathname,
    fullPage: true,
  });
  await page.setViewportSize({ width: 320, height: 900 });
  await page
    .getByRole("button", { name: "Character portraits", exact: true })
    .click();
  await page.locator("#portrait-gallery").scrollIntoViewIfNeeded();
  assert.equal(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
    true,
  );
  await page.screenshot({
    path: new URL("gallery-mobile.png", output).pathname,
    fullPage: true,
  });
  // Graphics unavailable retains names/initials and all navigation.
  const fallback = await browser.newPage();
  await fallback.addInitScript(() => {
    HTMLCanvasElement.prototype.getContext = function () {
      return null;
    };
  });
  await fallback.goto("http://127.0.0.1:5194/__hamlet-preview");
  await fallback
    .getByRole("button", { name: "Character portraits", exact: true })
    .click();
  await fallback.waitForTimeout(250);
  assert.equal(await fallback.locator("#portrait-gallery img").count(), 0);
  assert.equal(
    await fallback.locator("#portrait-gallery .character-avatar").count(),
    12,
  );
  assert.equal(
    await fallback.getByRole("button", { name: /Select Mara/ }).count(),
    1,
  );
  await fallback.close();
  assert.deepEqual(errors, []);
  await writeFile(
    new URL("checks.json", output),
    JSON.stringify(
      {
        browser: browser.version(),
        renderer: "SwiftShader",
        checks: [
          "12 distinct portraits rendered from village models",
          "Same portrait in gallery and resident panel",
          "Rename and activity retain appearance",
          "320px layout without overflow",
          "WebGL failure retains initials and navigation",
        ],
        errors,
      },
      null,
      2,
    ) + "\n",
  );
  console.log("Portrait browser checks passed");
} finally {
  await browser.close();
  await server.close();
}
