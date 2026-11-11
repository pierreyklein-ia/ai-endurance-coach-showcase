/* Aujourd'hui (valider une séance avec le bilan nutrition), Semaine (glisser-déposer, « Déplacer »), lier / délier
   une sortie, bouton « retour » qui ferme la fiche ouverte. */
import { expect, expectNoHorizontalScroll, goTab, test } from "./fixtures";

test("valider la sortie longue du jour avec le bilan nutrition", async ({ app }) => {
  await goTab(app, "aujourdhui");
  const card = app.locator("#view-aujourdhui article.card", { hasText: "Sortie longue vallonnée" });
  await expect(card).toBeVisible();
  await expectNoHorizontalScroll(app);
  await card.getByRole("button", { name: "Valider la séance" }).click();
  const sheet = app.locator("#fb-sheet");
  await expect(sheet).toBeVisible();
  await expect(sheet.locator("#fb-title")).toHaveText("Sortie longue vallonnée");
  await sheet.getByRole("button", { name: "RPE 6" }).click();
  await expect(sheet.locator("#fb-rpe-val")).toHaveText("6/10 soutenu");
  // bilan nutrition (sortie ≥ 1 h 30) : un gel, une flasque, digestion
  await expect(sheet.locator("#fb-nutri")).toBeVisible();
  await sheet.locator("#fb-nutri .nutri-item").first().getByRole("button", { name: /^Plus :/ }).click();
  await sheet.getByRole("button", { name: "Plus : Flasques d'eau" }).click();
  await expect(sheet.locator(".nutri-chosen")).toContainText("×1");
  await sheet.locator("#fb-save").click();
  await expect(app.locator("#toast")).toContainText("digestion");             // digestion obligatoire
  await sheet.locator(".nutri-dig button").nth(1).click();
  await sheet.locator("#fb-save").click();
  await expect(sheet).toBeHidden();
  await expect(app.locator("#toast")).toContainText("bilan nutrition enregistrés");
  await expect(app.locator("#view-coach")).toBeVisible();                     // le coach répond dans le chat
  await expect(app.locator("#coach-typing, #chat-list .msg.coach").first()).toBeVisible();
  await goTab(app, "aujourdhui");
  await expect(card.locator(".badge.done")).toHaveText("Faite · 6/10");
});

test("« retour » (Android) ferme la feuille de retour sans changer d'onglet", async ({ app }) => {
  await goTab(app, "aujourdhui");
  await app.getByRole("button", { name: "Valider la séance" }).click();
  await expect(app.locator("#fb-sheet")).toBeVisible();
  await app.goBack();
  await expect(app.locator("#fb-sheet")).toBeHidden();
  await expect(app.locator("#view-aujourdhui")).toBeVisible();
  expect(new URL(app.url()).hash).toBe("#aujourdhui");
});

test("Semaine : déplacer une séance avec le bouton « Déplacer »", async ({ app }) => {
  await goTab(app, "semaine");
  const item = app.locator("#view-semaine .item.draggable", { hasText: "Seuil" }).first();
  await expect(item).toBeVisible();
  const from = await item.evaluate((e) => e.closest("[data-drop-day]")?.getAttribute("data-drop-day"));
  await item.getByRole("button", { name: /Déplacer/ }).click();
  const sheet = app.locator("#move-sheet");
  await expect(sheet).toBeVisible();
  await expect(sheet.locator(".day-pick .btn.current")).toBeDisabled();
  const target = sheet.locator(".day-pick .btn:not([disabled])").last();
  await target.click();
  await expect(sheet).toBeHidden();
  await expect.poll(async () => item.evaluate((e) => e.closest("[data-drop-day]")?.getAttribute("data-drop-day")).catch(() => null))
    .not.toBe(from);
  await expectNoHorizontalScroll(app);
});

test("Semaine : glisser-déposer à la souris", async ({ app, isMobile }) => {
  test.skip(isMobile, "au doigt : appui long, couvert par les tests unitaires du geste ; ici la souris");
  await goTab(app, "semaine");
  const item = app.locator("#view-semaine .item.draggable", { hasText: "Seuil" }).first();
  const id = await item.getAttribute("data-session");
  const from = await item.evaluate((e) => e.closest("[data-drop-day]")!.getAttribute("data-drop-day"));
  const a = (await item.boundingBox())!;
  await app.mouse.move(a.x + 20, a.y + a.height / 2);
  await app.mouse.down();
  await app.mouse.move(a.x + 30, a.y + a.height / 2 + 10, { steps: 3 });
  await expect(app.locator(".drag-ghost")).toBeVisible();
  // pendant le glisser, chaque jour dit s'il accepte la séance (contour vert / grisé)
  const target = app.locator("#view-semaine .day.drop-ok").first();
  const to = await target.getAttribute("data-drop-day");
  await expect(app.locator("#view-semaine .day.drop-home")).toHaveCount(1);
  await target.scrollIntoViewIfNeeded();
  const b = (await target.boundingBox())!;
  await app.mouse.move(b.x + 40, b.y + b.height / 2, { steps: 8 });
  await expect(target).toHaveClass(/drop-over/);
  await app.mouse.up();
  await expect(app.locator(`#view-semaine [data-drop-day="${to}"] [data-session="${id}"]`)).toBeVisible();
  expect(to).not.toBe(from);
});

test("lier puis délier la sortie d'hier", async ({ app }) => {
  await goTab(app, "semaine");
  await app.locator("#view-semaine .item", { hasText: "Footing d'hier" }).click();
  const card = app.locator("#view-semaine article.card", { hasText: "Footing d'hier" });
  await expect(card).toBeVisible();
  await card.getByRole("button", { name: "Lier une sortie" }).click();
  const list = card.getByRole("list", { name: "Sorties possibles" });
  await expect(list.getByRole("listitem").first()).toBeVisible();
  await list.getByRole("listitem").first().click();
  await expect(app.locator("#toast")).toContainText("Séance liée");
  await expect(app.locator("#view-semaine .item", { hasText: "Footing d'hier" }).locator(".badge")).toBeVisible();
  await app.locator("#view-semaine .item", { hasText: "Footing d'hier" }).click();
  await expect(card.locator(".auto-valid")).toContainText("Liée avec ta sortie");
  await card.getByRole("button", { name: "Délier" }).click();
  await expect(app.locator("#toast")).toContainText("Séance déliée");
});

test("« retour » replie le panneau de lien ouvert", async ({ app }) => {
  await goTab(app, "semaine");
  await app.locator("#view-semaine .item", { hasText: "Footing d'hier" }).click();
  const card = app.locator("#view-semaine article.card", { hasText: "Footing d'hier" });
  await card.getByRole("button", { name: "Lier une sortie" }).click();
  await expect(card.getByRole("list", { name: "Sorties possibles" })).toBeVisible();
  await app.goBack();
  await expect(card.getByRole("list", { name: "Sorties possibles" })).toBeHidden();
  await expect(app.locator("#view-semaine")).toBeVisible();
});

test("Semaine : la feuille « Récap de la semaine »", async ({ app }) => {
  await goTab(app, "semaine");
  await app.getByRole("button", { name: /Récap de la semaine/ }).click();
  const sheet = app.locator("#recap-sheet");
  await expect(sheet).toBeVisible();
  await expect(sheet.getByRole("heading", { name: "Récap de la semaine" })).toBeVisible();
  await expect(sheet.locator(".recap-sec").first()).toBeVisible();
  await expect(sheet).toContainText("aperçu");
  await expectNoHorizontalScroll(app);
  // v0.51 : ressenti de fin de semaine envoyé au coach
  const feel = sheet.getByRole("region", { name: "Ton ressenti de la semaine" });
  await feel.getByLabel("Fatigue 3 sur 5").click();
  await feel.getByLabel("Difficulté 4 sur 5").click();
  await feel.getByLabel("Plaisir 5 sur 5").click();
  await feel.getByRole("button", { name: "Envoyer au coach" }).click();
  await expect(sheet.getByRole("heading", { name: "Ton ressenti (modifiable)" })).toBeVisible();
  await expectNoHorizontalScroll(app);
  await app.goBack();                                       // « retour » ferme la feuille
  await expect(sheet).toBeHidden();
  await app.getByRole("button", { name: /Récap de la semaine/ }).click();
  await sheet.getByRole("button", { name: "Répondre au coach" }).click();
  await expect(app.locator("#view-coach")).toBeVisible();
});
