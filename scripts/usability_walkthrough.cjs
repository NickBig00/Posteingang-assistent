// Scripted walkthrough of the five usability tasks (ROADMAP_social U8), headless with Playwright.
//
//   node scripts/usability_walkthrough.cjs   → docs/usability/walkthrough.json (+ screenshots)
//
// NOT a usability test: a script knows where to click. It only checks that every task can be
// done with the visible labels alone (roles and names, no ids or CSS selectors), counts the
// clicks and records the time. The acceptance of U8 needs a person who does not know the app
// (protocol: docs/usability/U8_usability_test.md).

const fs = require("node:fs");
const path = require("node:path");
const { chromium } = require("playwright");
const { withServer, BASE, LAPTOP, ROOT } = require("./screenshots.cjs");

const OUT = path.join(ROOT, "docs", "usability");
const PHOTO = path.join(ROOT, "samples", "social", "images", "demo_hintergrund.jpg");

async function newBriefing() {
  const soon = new Date();
  soon.setDate(soon.getDate() + 12);
  const form = new URLSearchParams({
    area: "kurse",
    occasion_type: "kurs",
    notes: "Rückenkurs am Abend, für Einsteiger, kleine Gruppe, Matten vorhanden",
    datum: soon.toISOString().slice(0, 10),
    uhrzeit: "18:30",
    ort: "Kursraum 2",
    channels: "instagram_post",
  });
  form.append("channels", "facebook");
  const response = await fetch(`${BASE}/social/briefings`, { method: "POST", body: form, redirect: "manual" });
  return response.headers.get("location");
}

(async () => {
  fs.mkdirSync(OUT, { recursive: true });
  const results = await withServer(async () => {
    const url = await newBriefing();
    const browser = await chromium.launch();
    const context = await browser.newContext({ viewport: LAPTOP, locale: "de-DE", acceptDownloads: true });
    await context.grantPermissions(["clipboard-read", "clipboard-write"], { origin: BASE });
    const page = await context.newPage();
    page.on("dialog", (dialog) => dialog.accept());
    let clicks = 0;
    const click = async (locator) => {
      clicks += 1;
      await Promise.all([page.waitForLoadState("networkidle"), locator.click()]);
      await page.waitForLoadState("networkidle");
    };
    const tasks = [];
    const task = async (name, fn) => {
      const start = Date.now();
      const before = clicks;
      let ok = true;
      let error = "";
      try {
        await fn();
      } catch (e) {
        ok = false;
        error = String(e.message || e).split("\n")[0];
      }
      tasks.push({ name, ok, clicks: clicks - before, seconds: (Date.now() - start) / 1000, error });
      await page.screenshot({ path: path.join(OUT, `aufgabe-${tasks.length}.png`), fullPage: false });
    };
    await page.goto(BASE + url, { waitUntil: "networkidle" });

    await task("Instagram-Beitrag mit eigenem Foto und Überschrift „Starker Rücken“ auf dem Bild", async () => {
      await click(page.getByText("Eigenes Foto", { exact: true }));
      clicks += 1; // choosing the file
      await Promise.all([page.waitForNavigation(), page.getByLabel(/Foto hochladen/).setInputFiles(PHOTO)]);
      await click(page.getByRole("group", { name: "Text aufs Bild?" }).getByRole("button", { name: "Ja" }));
      await page.getByLabel("Überschrift", { exact: true }).fill("Starker Rücken");
      await click(page.getByRole("button", { name: "Text übernehmen und prüfen" }));
      const preview = page.locator('[data-preview="instagram_post"] img[data-post-image]');
      if (!(await preview.isVisible())) throw new Error("keine Vorschau mit Bild");
    });

    await task("Die KI ein anderes Bild erzeugen lassen und nehmen", async () => {
      await click(page.getByText("KI-Bild", { exact: true }).first());
      await click(page.getByRole("button", { name: "Vorschlag von der KI" }));
      await click(page.getByRole("button", { name: "Prüfen", exact: true }));
      while (await page.getByRole("button", { name: "Verstanden" }).count()) {
        await click(page.getByRole("button", { name: "Verstanden" }).first());
      }
      await click(page.getByRole("button", { name: /Bilder erzeugen/ }));
      await click(page.getByRole("button", { name: "KI-Variante 1 wählen" }));
      if (!(await page.getByText("Gewählt.").isVisible())) throw new Error("KI-Bild nicht gewählt");
    });

    await task("Den Text kürzer machen", async () => {
      await click(page.getByText("Anders formulieren …").first());
      await click(page.getByRole("button", { name: "Kürzer fassen" }).first());
    });

    await task("Den Beitrag freigeben (mit offenen Hinweisen)", async () => {
      const confirmations = page.locator('[data-review-item="open"] button:visible');
      while (await confirmations.count()) await click(confirmations.first());
      const approve = page.getByRole("button", { name: "Freigeben" }).first();
      if (await approve.isDisabled()) throw new Error("Freigeben ist noch gesperrt");
      await click(approve);
      if (!(await page.getByRole("heading", { name: "Veröffentlichen" }).first().isVisible())) {
        throw new Error("Schritt Veröffentlichen fehlt");
      }
    });

    await task("Bild und Text für Instagram herunterladen", async () => {
      clicks += 1;
      await page.getByRole("button", { name: "Text kopieren" }).first().click();
      const text = await page.evaluate(() => navigator.clipboard.readText());
      if (!text.trim()) throw new Error("Text nicht kopiert");
      clicks += 1;
      const [download] = await Promise.all([
        page.waitForEvent("download"),
        page.getByRole("link", { name: /Bild herunterladen/ }).first().click(),
      ]);
      if (!(await download.suggestedFilename()).endsWith(".jpg")) throw new Error("kein JPEG");
    });

    await browser.close();
    return tasks;
  }, { images: true });

  fs.writeFileSync(path.join(OUT, "walkthrough.json"), JSON.stringify(results, null, 2) + "\n");
  for (const r of results) {
    console.log(`${r.ok ? "ok  " : "FAIL"} ${r.clicks} Klicks ${r.seconds.toFixed(1)} s  ${r.name}${r.error ? " – " + r.error : ""}`);
  }
  if (results.some((r) => !r.ok)) process.exit(1);
})().catch((error) => {
  console.error(error);
  process.exit(1);
});
