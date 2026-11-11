/* Simulateur de course (survol du profil, dépôt d'un GPX synthétique), Carrière (éditeur d'avatar : lieu et
   ambiance) et Réglages (thème, volume maximum, « Tester Claude » sans clé). */
import { expect, expectNoHorizontalScroll, goTab, test } from "./fixtures";

/** Tracé synthétique de ~85 km au départ de Volvic, avec des bosses pour ~2 750 m D+. */
function synthGpx(): Buffer {
  const pts: string[] = [];
  const n = 900, km = 85;
  for (let i = 0; i <= n; i++) {
    const d = (km * i) / n;
    const lat = 45.872 - (d / 111.2) * 0.9, lon = 3.04 + Math.sin(d / 6) * 0.05;
    const ele = 600 + 52 * Math.sin((d * 2 * Math.PI) / 3.1) + 300 * Math.sin(d / 20);
    pts.push(`<trkpt lat="${lat.toFixed(6)}" lon="${lon.toFixed(6)}"><ele>${ele.toFixed(1)}</ele></trkpt>`);
  }
  return Buffer.from(`<?xml version="1.0" encoding="UTF-8"?><gpx version="1.1" creator="e2e" xmlns="http://www.topografix.com/GPX/1/1">`
    + `<trk><name>VVX</name><trkseg>${pts.join("")}</trkseg></trk></gpx>`);
}

test("simulateur : survol du profil, scénarios, dépôt d'un GPX", async ({ app, isMobile }) => {
  await goTab(app, "course");
  await expect(app.locator("#sim-title")).toContainText("Volvic Volcanic Experience");
  const svg = app.locator(".elev-svg");
  await expect(svg).toBeVisible();
  await expectNoHorizontalScroll(app);
  const box = (await svg.boundingBox())!;
  if (isMobile) await svg.tap({ position: { x: box.width / 2, y: box.height / 2 } });
  else await app.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
  await expect(app.locator(".elev-readout")).toContainText(/km [\d,]+ · \d+ m · \d\d:\d\d/);
  await app.getByRole("button", { name: /Puy de Dôme.*voir la fiche/ }).first().click();
  await expect(app.getByRole("region", { name: /Fiche : Puy de Dôme/ })).toBeVisible();
  await app.locator(".sim-scen button", { hasText: "Prudent" }).click();
  await expect(app.locator(".sim-scen button.on")).toContainText("Prudent");

  // GPX : refus d'un fichier qui n'en est pas un, puis tracé accepté
  await app.locator("#sim-gpx summary").click();
  await app.locator("#sim-gpx-file").setInputFiles({ name: "notes.txt", mimeType: "text/plain", buffer: Buffer.from("x") });
  await expect(app.locator("#sim-gpx [role=alert]")).toContainText("n'est pas un fichier GPX");
  await app.locator("#sim-gpx-file").setInputFiles({ name: "vvx.gpx", mimeType: "application/gpx+xml", buffer: synthGpx() });
  await expect(app.locator("#toast")).toContainText("GPX enregistré", { timeout: 20_000 });
  await expect(app.locator("#sim-gpx summary")).toContainText("vvx.gpx");
  await app.locator("#sim-gpx").getByRole("button", { name: "Supprimer" }).click();
  await expect(app.locator("#toast")).toContainText("GPX supprimé");
});

test("carrière : éditeur d'avatar, lieu et ambiance", async ({ app }) => {
  await goTab(app, "carriere");
  await expect(app.locator(".career-hero")).toBeVisible();
  await expectNoHorizontalScroll(app);
  await app.getByRole("button", { name: /mon avatar/ }).click();
  const ed = app.locator("dialog[open]");
  await expect(ed.locator("#avatar-places")).toBeVisible();
  const place = ed.locator("[data-place]:not([disabled])").last();
  const placeId = await place.getAttribute("data-place");
  await place.click();
  await expect(place).toHaveClass(/on/);
  const amb = ed.locator("[data-ambiance]:not([disabled])").last();
  await amb.click();
  await expect(amb).toHaveClass(/on/);
  await ed.locator("#avatar-save").click();
  await expect(app.locator("#toast")).toHaveText("Avatar enregistré ✓");
  await app.getByRole("button", { name: /mon avatar/ }).click();
  await expect(app.locator(`dialog[open] [data-place="${placeId}"]`)).toHaveClass(/on/);
  await app.goBack();                                        // « retour » ferme l'éditeur
  await expect(app.locator("dialog[open] #avatar-places")).toHaveCount(0);
  await expect(app.locator("#view-carriere")).toBeVisible();
});

test("réglages : thème, volume maximum, Tester Claude", async ({ app }) => {
  await goTab(app, "carriere");
  await app.getByRole("button", { name: "⚙ Réglages" }).click();
  const themes = app.locator(".theme-pick button");
  await themes.last().click();
  const chosen = await app.evaluate(() => document.documentElement.dataset.theme);
  expect(chosen).not.toBe("classique");
  await app.reload();
  await expect.poll(() => app.evaluate(() => document.documentElement.dataset.theme)).toBe(chosen);
  await goTab(app, "carriere");
  await app.locator(".theme-pick button").first().click();
  await expect.poll(() => app.evaluate(() => document.documentElement.dataset.theme)).toBe("classique");

  const wm = app.locator("#week-max");
  await expect(wm).toContainText("6 h");
  await wm.locator('input[name="hours"]').fill("n'importe");
  await wm.getByRole("button", { name: "Enregistrer" }).click();
  await expect(wm.getByRole("alert")).toContainText("Je n'ai pas compris");
  await wm.locator('input[name="hours"]').fill("7h30");
  await wm.getByRole("button", { name: "Enregistrer" }).click();
  await expect(app.locator("#toast")).toContainText("Enregistré ✓");
  await expect(wm.locator(".wm-now")).toContainText("7 h 30");

  await app.getByRole("button", { name: "Tester Claude" }).click();
  await expect(app.locator("#claude-check")).toContainText("Claude n'est pas activé sur ce serveur");
  await expectNoHorizontalScroll(app);
});

test("réglages : mes zones FC et premier jour de la semaine", async ({ app }) => {
  await goTab(app, "carriere");
  await app.getByRole("button", { name: "⚙ Réglages" }).click();
  const zones = app.locator("#mes-zones");
  await expect(zones.locator(".zones-table tr")).toHaveCount(5);
  const max = zones.getByLabel(/maximale/);
  await max.fill("300");
  await zones.getByRole("button", { name: "Enregistrer" }).click();
  await expect(zones.getByRole("alert")).toContainText("entre 120 et 230");
  await max.fill("192");
  await zones.getByRole("button", { name: "Enregistrer" }).click();
  await expect(app.locator("#toast")).toContainText("Zones enregistrées");
  await expect(zones.locator(".zone-val").first()).toContainText("192");
  await expect(zones.getByLabel(/maximale/)).toHaveValue("192");

  const week = app.locator("#ma-semaine");
  await week.getByLabel("Premier jour de la semaine").selectOption({ label: "dimanche" });
  await expect(app.locator("#toast")).toContainText("Ta semaine va du dimanche au samedi");
  await expect(week).toContainText("Semaine du dimanche au samedi");
  await expectNoHorizontalScroll(app);
  await goTab(app, "semaine");
  await expect(app.locator("#view-semaine .week-nav .title")).toContainText("Semaine du dim.");
});
