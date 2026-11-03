// Screenshots of the UI for pull requests (SPEC 10.3), taken headless with Playwright.
//
//   node scripts/screenshots.cjs <milestone> [<milestone> …]
//
// Starts the app on a fresh temporary database with the offline fake LLM provider, loads the
// synthetic demo data through the UI routes and writes PNGs to docs/screenshots/<milestone>/.
// Requires Playwright with Chromium (`npm i -D playwright` or a global installation).

const { spawn } = require("node:child_process");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { chromium } = require("playwright");

const ROOT = path.resolve(__dirname, "..");
const PORT = 8765;
const BASE = `http://127.0.0.1:${PORT}`;

const LAPTOP = { width: 1366, height: 900 };
const TABLET = { width: 820, height: 1180 };

// Pages per milestone: [file name, path, options].
const SHOTS = {
  U1: [
    ["einstellungen", "/settings", {}],
    ["design-system", "/design", { fullPage: true }],
    ["kampagnen", "/social/campaigns", {}],
    ["navigation-tablet", "/settings", { viewport: TABLET, click: "[data-nav-toggle]" }],
  ],
  U2: [
    ["uebersicht", "/", { fullPage: true }],
    ["uebersicht-tablet", "/", { viewport: TABLET, fullPage: true }],
  ],
  U3: [
    ["posteingang-liste", "/inbox", { fullPage: true }],
    ["posteingang-detail", "/items/{inbox_item}", { fullPage: true }],
    ["posteingang-detail-ocr", "/items/{ocr_item}", {}],
    ["posteingang-audit", "/items/{inbox_item}/audit", {}],
  ],
  U4: [
    ["social-uebersicht", "/social", { fullPage: true }],
    ["social-briefing-formular", "/social/briefings/new", { fullPage: true }],
    ["social-entwurf-detail", "/social/briefings/{briefing}", { fullPage: true }],
    ["social-entwurf-freigegeben", "/social/briefings/{approved_briefing}", { fullPage: true }],
    ["social-kalender-woche", "/social/calendar?view=week&date={schedule_date}", { fullPage: true }],
    ["social-kalender-monat", "/social/calendar?view=month&date={schedule_date}", { fullPage: true }],
    ["social-kampagne", "/social/campaigns/{campaign}", {}],
  ],
  B2: [["bildvorschlag", "/social/briefings/{image_briefing}", { fullPage: true, images: true }]],
  B3: [["grafik-vorlage", "/social/briefings/{approved_briefing}", { fullPage: true }]],
  U5: [
    ["laptop-uebersicht", "/", { fullPage: true }],
    ["laptop-posteingang", "/inbox", {}],
    ["laptop-entwurf", "/social/briefings/{briefing}", {}],
    ["tablet-uebersicht", "/", { viewport: TABLET, fullPage: true }],
    ["tablet-posteingang", "/inbox", { viewport: TABLET }],
    ["tablet-entwurf", "/social/briefings/{briefing}", { viewport: TABLET }],
    ["tablet-kalender", "/social/calendar?view=week&date={schedule_date}", { viewport: TABLET }],
    ["tablet-navigation", "/social", { viewport: TABLET, click: "[data-nav-toggle]" }],
    ["leer-posteingang", "/inbox", { empty: true }],
    ["leer-social", "/social", { empty: true }],
  ],
};

function startServer(env) {
  const child = spawn(
    "uv",
    ["run", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", String(PORT)],
    { cwd: ROOT, env, stdio: ["ignore", "ignore", "pipe"] },
  );
  child.stderr.on("data", () => {});
  return child;
}

async function waitForServer() {
  for (let i = 0; i < 100; i++) {
    try {
      const response = await fetch(`${BASE}/health`);
      if (response.ok) return;
    } catch {
      /* not up yet */
    }
    await new Promise((resolve) => setTimeout(resolve, 200));
  }
  throw new Error("server did not start");
}

async function post(urlPath, form) {
  const body = new URLSearchParams(form || {});
  const response = await fetch(BASE + urlPath, { method: "POST", body, redirect: "manual" });
  return response.headers.get("location") || "";
}

function isoDate(date) {
  return date.toISOString().slice(0, 10);
}

// Demo data through the regular routes, as a person would create it.
async function seed(withImages) {
  const ids = {};
  await post("/samples/import");
  await post("/items/process");
  ids.inbox_item = 1;
  ids.ocr_item = 1;
  for (let id = 1; id <= 40; id++) {
    const html = await (await fetch(`${BASE}/items/${id}`)).text();
    if (html.includes("Texterkennung (OCR)")) {
      ids.ocr_item = id;
      break;
    }
  }
  for (let id = 1; id <= 40; id++) {
    const html = await (await fetch(`${BASE}/items/${id}`)).text();
    if (html.includes("Freigeben") && html.includes("Antwortentwurf")) {
      ids.inbox_item = id;
      break;
    }
  }

  const soon = new Date();
  soon.setDate(soon.getDate() + 9);
  const briefing = {
    area: "kurse",
    occasion_type: "kurs",
    notes: "Neuer Rückenkurs am Abend, für Einsteiger geeignet, kleine Gruppe, Matten vorhanden",
    datum: isoDate(soon),
    uhrzeit: "18:30",
    ort: "Kursraum 2",
    channels: "instagram_post",
  };
  const form = new URLSearchParams(briefing);
  form.append("channels", "facebook");
  let response = await fetch(`${BASE}/social/briefings`, { method: "POST", body: form, redirect: "manual" });
  ids.briefing = (response.headers.get("location") || "/1").split("/").pop();

  // A second briefing whose drafts get approved and scheduled (calendar, publishing aids).
  const approved = new URLSearchParams({
    area: "fitness",
    occasion_type: "aktion",
    notes: "Sommeraktion: zwei Wochen gratis trainieren, nur für Neukunden",
    zeitraum_von: isoDate(soon),
    zeitraum_bis: isoDate(new Date(soon.getTime() + 13 * 86400000)),
    preis: "0",
    channels: "instagram_post",
  });
  approved.append("channels", "facebook");
  response = await fetch(`${BASE}/social/briefings`, { method: "POST", body: approved, redirect: "manual" });
  ids.approved_briefing = (response.headers.get("location") || "/2").split("/").pop();
  // Synthetic demo image (abstract gradient, no people) for previews and the JPEG download.
  const image = fs.readFileSync(path.join(ROOT, "samples", "social", "images", "demo_hintergrund.jpg"));
  for (const briefingId of [ids.briefing, ids.approved_briefing]) {
    const upload = new FormData();
    upload.append("file", new Blob([image], { type: "image/jpeg" }), "demo_hintergrund.jpg");
    await fetch(`${BASE}/social/briefings/${briefingId}/assets`, { method: "POST", body: upload, redirect: "manual" });
  }
  const detail = await (await fetch(`${BASE}/social/briefings/${ids.approved_briefing}`)).text();
  const draftIds = [...new Set([...detail.matchAll(/id="draft-(\d+)"/g)].map((m) => m[1]))];
  const confirmIds = [...new Set([...detail.matchAll(/\/social\/checks\/(\d+)\/confirm/g)].map((m) => m[1]))];
  for (const id of confirmIds) await post(`/social/checks/${id}/confirm`);
  const when = new Date(soon);
  when.setDate(when.getDate() - 3);
  ids.schedule_date = isoDate(when);
  for (const [index, id] of draftIds.entries()) {
    await post(`/social/drafts/${id}/approve`);
    if (index === 0) {
      await post(`/social/drafts/${id}/schedule`, { when: `${isoDate(when)}T09:00` });
      await post(`/social/drafts/${id}/external`);
    }
  }

  const campaign = await post("/social/campaigns/demo");
  ids.campaign = campaign.split("/").pop() || "1";
  ids.image_briefing = ids.briefing;
  if (withImages) {
    const first = await (await fetch(`${BASE}/social/briefings/${ids.briefing}`)).text();
    const draft = (first.match(/id="draft-(\d+)"/) || [])[1];
    if (draft) await post(`/social/drafts/${draft}/images/suggest`);
  }
  return ids;
}

function fill(template, ids) {
  return template.replace(/\{(\w+)\}/g, (_, key) => String(ids[key] ?? ""));
}

async function shoot(browser, milestone, ids, empty) {
  const dir = path.join(ROOT, "docs", "screenshots", milestone);
  fs.mkdirSync(dir, { recursive: true });
  for (const [name, urlPath, options] of SHOTS[milestone]) {
    if (Boolean(options.empty) !== empty) continue;
    const context = await browser.newContext({
      viewport: options.viewport || LAPTOP,
      deviceScaleFactor: 2,
      locale: "de-DE",
      timezoneId: "Europe/Berlin",
    });
    const page = await context.newPage();
    await page.goto(BASE + fill(urlPath, ids), { waitUntil: "networkidle" });
    if (options.click) await page.click(options.click);
    await page.screenshot({ path: path.join(dir, `${name}.png`), fullPage: Boolean(options.fullPage) });
    await context.close();
    console.log(`docs/screenshots/${milestone}/${name}.png`);
  }
}

async function run(milestones, empty) {
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "screens-"));
  const env = {
    ...process.env,
    POSTEINGANG_NO_DOTENV: "1",
    DATABASE_URL: `sqlite:///${path.join(tmp, "demo.db")}`,
    STORAGE_DIR: path.join(tmp, "raw"),
    LLM_PROVIDER: "fake",
    IMAGE_GENERATION_ENABLED: milestones.includes("B2") ? "true" : "false",
    IMAGE_PROVIDER: "fake",
  };
  const server = startServer(env);
  try {
    await waitForServer();
    const ids = empty ? {} : await seed(milestones.includes("B2"));
    const browser = await chromium.launch();
    for (const milestone of milestones) await shoot(browser, milestone, ids, empty);
    await browser.close();
  } finally {
    server.kill();
    fs.rmSync(tmp, { recursive: true, force: true });
  }
}

(async () => {
  const milestones = process.argv.slice(2);
  const unknown = milestones.filter((m) => !SHOTS[m]);
  if (milestones.length === 0 || unknown.length) {
    console.error(`usage: node scripts/screenshots.cjs ${Object.keys(SHOTS).join("|")} …`);
    process.exit(2);
  }
  await run(milestones, false);
  if (milestones.some((m) => SHOTS[m].some(([, , o]) => o.empty))) await run(milestones, true);
})().catch((error) => {
  console.error(error);
  process.exit(1);
});
