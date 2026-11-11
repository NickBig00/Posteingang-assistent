// Accessibility check of the main pages (ROADMAP_social U5), headless with Playwright.
//
//   node scripts/a11y.cjs            → writes docs/accessibility/report.md (+ report.json)
//
// 1. axe-core (WCAG 2.1 A/AA rules, incl. colour contrast) on every page, laptop and tablet.
// 2. Keyboard: Tab through every page; each stop must be visible (focus outline) and the
//    sequence must reach the main actions. Uses the same seeded demo as the screenshots.

const fs = require("node:fs");
const path = require("node:path");
const { chromium } = require("playwright");
const { withServer, BASE, LAPTOP, TABLET, ROOT } = require("./screenshots.cjs");

const AXE = fs.readFileSync(path.join(ROOT, "node_modules", "axe-core", "axe.min.js"), "utf8");

const PAGES = [
  ["Übersicht", "/"],
  ["Posteingang", "/inbox"],
  ["Eingang (Detail)", "/items/{inbox_item}"],
  ["Audit Eingang", "/items/{inbox_item}/audit"],
  ["Social Media", "/social"],
  ["Neues Briefing", "/social/briefings/new"],
  ["Entwurf in Prüfung (mit KI-Bild)", "/social/briefings/{briefing}"],
  ["Entwurf freigegeben", "/social/briefings/{approved_briefing}"],
  ["Kalender Woche", "/social/calendar?view=week&date={schedule_date}"],
  ["Kalender Monat", "/social/calendar?view=month&date={schedule_date}"],
  ["Kampagnen", "/social/campaigns"],
  ["Kampagne", "/social/campaigns/{campaign}"],
  ["Einstellungen", "/settings"],
  ["Design-System", "/design"],
];

const fill = (t, ids) => t.replace(/\{(\w+)\}/g, (_, k) => String(ids[k] ?? ""));

async function axe(page) {
  await page.addScriptTag({ content: AXE });
  return page.evaluate(async () => {
    const result = await window.axe.run(document, {
      runOnly: { type: "tag", values: ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"] },
    });
    return result.violations.map((v) => ({
      id: v.id,
      impact: v.impact,
      help: v.help,
      nodes: v.nodes.slice(0, 5).map((n) => n.target.join(" ")),
      count: v.nodes.length,
    }));
  });
}

async function keyboard(page, maxStops = 60) {
  const stops = [];
  await page.keyboard.press("Tab");
  for (let i = 0; i < maxStops; i++) {
    const info = await page.evaluate(() => {
      const el = document.activeElement;
      if (!el || el === document.body) return null;
      const style = getComputedStyle(el);
      const visible =
        (style.outlineStyle !== "none" && parseFloat(style.outlineWidth) > 0) ||
        style.boxShadow !== "none";
      const rect = el.getBoundingClientRect();
      const label = (el.getAttribute("aria-label") || el.innerText || el.value || el.name || el.tagName)
        .trim().replace(/\s+/g, " ").slice(0, 40);
      return { tag: el.tagName.toLowerCase(), label, visible, onScreen: rect.width > 0 && rect.height > 0 };
    });
    if (!info) break;
    stops.push(info);
    await page.keyboard.press("Tab");
  }
  return stops;
}

(async () => {
  const report = await withServer(async (ids) => {
    const browser = await chromium.launch();
    const results = [];
    for (const [viewportName, viewport] of [["Laptop 1366×900", LAPTOP], ["Tablet 820×1180", TABLET]]) {
      for (const [name, url] of PAGES) {
        const context = await browser.newContext({ viewport, locale: "de-DE" });
        const page = await context.newPage();
        await page.goto(BASE + fill(url, ids), { waitUntil: "networkidle" });
        const violations = await axe(page);
        const stops = viewportName.startsWith("Laptop") ? await keyboard(page) : [];
        const overflow = await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth + 1);
        results.push({ viewport: viewportName, name, url: fill(url, ids), violations, stops, overflow });
        await context.close();
      }
    }
    await browser.close();
    return results;
  }, { images: true });

  const dir = path.join(ROOT, "docs", "accessibility");
  fs.mkdirSync(dir, { recursive: true });
  fs.writeFileSync(path.join(dir, "report.json"), JSON.stringify(report, null, 2) + "\n");
  const lines = [
    "# Barrierefreiheit – automatischer Prüfbericht",
    "",
    "Erzeugt mit `node scripts/a11y.cjs` (axe-core 4.10, Regeln WCAG 2.1 A/AA inkl. Farbkontrast; Tastatur-Durchlauf mit Tab).",
    "Demo-Daten wie bei den Screenshots (Fake-Anbieter, KI-Bilder eingeschaltet).",
    "",
    "| Ansicht | Seite | axe-Verstöße | Tab-Stopps | ohne sichtbaren Fokus | horizontales Scrollen |",
    "|---|---|---|---|---|---|",
  ];
  for (const r of report) {
    const invisible = r.stops.filter((s) => !s.visible).length;
    lines.push(
      `| ${r.viewport} | ${r.name} | ${r.violations.length ? r.violations.map((v) => `${v.id} (${v.count})`).join(", ") : "keine"} | ` +
        `${r.viewport.startsWith("Laptop") ? r.stops.length : "–"} | ${r.viewport.startsWith("Laptop") ? invisible : "–"} | ${r.overflow ? "ja" : "nein"} |`,
    );
  }
  const details = report.filter((r) => r.violations.length);
  if (details.length) {
    lines.push("", "## Details der Verstöße", "");
    for (const r of details) {
      for (const v of r.violations) lines.push(`- **${r.name}** (${r.viewport}): \`${v.id}\` ${v.impact} – ${v.help}: ${v.nodes.join("; ")}`);
    }
  }
  fs.writeFileSync(path.join(dir, "report.md"), lines.join("\n") + "\n");
  const total = report.reduce((n, r) => n + r.violations.length, 0);
  const invisible = report.reduce((n, r) => n + r.stops.filter((s) => !s.visible).length, 0);
  console.log(`axe violations: ${total}, focus without outline: ${invisible}`);
})().catch((error) => {
  console.error(error);
  process.exit(1);
});
