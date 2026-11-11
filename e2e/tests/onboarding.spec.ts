/* Connexion, puis accueil d'un nouvel athlète : choix du coach, Strava (simulé), conversation jusqu'au premier plan. */
import { expect, expectNoHorizontalScroll, goTab, login, test, TODAY } from "./fixtures";

test("mauvais code refusé, bon code accepté, déconnexion", async ({ page, request, errors }) => {
  void errors;
  await request.post("/__e2e/reset?state=onboarded");
  await page.clock.setFixedTime(new Date(TODAY));
  await page.goto("/");
  await page.fill("#passcode", "faux-code");
  await page.click("#login-form button");
  await expect(page.locator("#login-error")).toHaveText("code incorrect");
  await login(page);
  await expect(page.locator("#race-name")).toContainText("Volvic Volcanic Experience");
  await expect(page.locator("#race-countdown")).toHaveText("J-184");
  await goTab(page, "carriere");
  await page.getByRole("button", { name: "Se déconnecter" }).click();
  await expect(page.locator("#passcode")).toBeVisible();
});

test.describe("nouvel athlète", () => {
  test.use({ state: "fresh" });

  test("du choix du coach au premier plan", async ({ app }) => {
    await expect(app.getByRole("heading", { name: "Choisis ton coach" })).toBeVisible();
    await expect(app.locator("nav.tabs")).toBeHidden();
    await app.getByRole("button", { name: "Homme" }).click();
    await app.locator("#coach-name").fill("Hugo");
    await app.getByRole("button", { name: "C'est parti" }).click();
    await expect(app.getByRole("heading", { name: /Hugo, ton coach d'endurance/ })).toBeVisible();
    await expectNoHorizontalScroll(app);
    await app.getByRole("button", { name: "Connecter Strava" }).click();

    // le bilan arrive dans le chat, puis l'objectif (catalogue hors ligne) est retrouvé et confirmé
    await expect(app.locator("#view-coach")).toBeVisible();
    const last = app.locator("#chat-list .msg").last();
    await expect(last).toContainText("Salut Alex");
    await app.locator("#chat-text").fill("La VVX 85 km");
    await app.locator("#chat-send").click();
    await expect(last).toContainText("C'est bien ça", { timeout: 15_000 });
    await expect(last).toContainText("2750 m D+");
    await app.locator("#chat-quick button", { hasText: "Oui, c'est ça" }).click();
    await expect(last).toContainText("quelques questions", { timeout: 15_000 });
    await expect(last).toContainText("Ta semaine commence quel jour");          // v0.50 : premier jour de SA semaine
    await app.locator("#chat-text").fill("Je cours en trail, 4 jours par semaine, sortie longue le samedi. Ma semaine commence le dimanche.");
    await app.locator("#chat-send").click();
    await expect(app.locator("#chat-quick button", { hasText: "Je ne sais pas" })).toBeVisible({ timeout: 15_000 });
    await app.locator("#chat-quick button", { hasText: "5 h" }).click();
    await expect(app.locator("#chat-quick button", { hasText: "Ça me va" })).toBeVisible({ timeout: 15_000 });
    await app.locator("#chat-quick button", { hasText: "Ça me va" }).click();
    await expect(last).not.toContainText("Ça me va", { timeout: 15_000 });

    // le plan est en place : « Semaine » montre des séances
    await expect(app.locator("nav.tabs")).toBeVisible();
    await goTab(app, "semaine");
    await expect(app.locator("#view-semaine .item").first()).toBeVisible();
    await expect(app.locator("#view-semaine .week-nav .title")).toContainText("Semaine du dim.");   // sa semaine
    await expectNoHorizontalScroll(app);
  });
});
