/* Chat avec le coach (« … écrit » pendant la réponse), Progrès et Œil du coach (analyse offerte). */
import { expect, expectNoHorizontalScroll, goTab, test } from "./fixtures";

test("chat : le coach écrit puis répond", async ({ app }) => {
  // la réponse est retenue un instant pour voir l'indicateur « … écrit » (le coach hors ligne répond tout de suite)
  await app.route("**/api/chat", async (r) => {
    if (r.request().method() === "POST") await new Promise((ok) => setTimeout(ok, 700));
    await r.continue();
  });
  await goTab(app, "coach");
  const before = await app.locator("#chat-list .msg").count();
  await app.locator("#chat-text").fill("J'ai les jambes lourdes aujourd'hui, je fais quoi ?");
  await app.locator("#chat-send").click();
  await expect(app.locator("#chat-list .typing")).toContainText("écrit…");
  await expect(app.locator("#chat-list .msg.user").last()).toContainText("jambes lourdes");
  await expect(app.locator("#chat-list .typing")).toHaveCount(0, { timeout: 15_000 });
  await expect.poll(() => app.locator("#chat-list .msg").count()).toBeGreaterThan(before + 1);
  await expect(app.locator("#chat-list .msg.coach").last()).not.toBeEmpty();
  await expectNoHorizontalScroll(app);
});

test("Entrée envoie, le champ se vide", async ({ app, isMobile }) => {
  test.skip(isMobile, "au téléphone, Entrée fait un retour à la ligne");
  await goTab(app, "coach");
  await app.locator("#chat-text").fill("Merci coach");
  await app.locator("#chat-text").press("Enter");
  await expect(app.locator("#chat-text")).toHaveValue("");
  await expect(app.locator("#chat-list .msg.user").last()).toContainText("Merci coach");
});

test("Progrès et Œil du coach : l'analyse offerte", async ({ app }) => {
  await goTab(app, "progres");
  const eye = app.locator("#oeil");
  await expect(eye).toBeVisible();
  await expect(eye.locator(".eye-gift")).toContainText("Ta première analyse est offerte");
  await expectNoHorizontalScroll(app);
  await eye.getByRole("button", { name: "Lancer mon analyse offerte" }).click();
  await expect(eye.locator(".eye-summary")).toBeVisible({ timeout: 20_000 });
  await expect(eye.locator(".eye-prios li").first()).toBeVisible();
  await expect(eye.getByText(/Qui a contribué/)).toBeVisible();
  // le rapport est aussi annoncé dans le chat
  await goTab(app, "coach");
  await expect(app.locator("#chat-list .eye-link").last()).toBeVisible();
});

test("État : indicateurs de charge, sans débordement", async ({ app }) => {
  await goTab(app, "etat");
  await expect(app.locator("#view-etat .metric").first()).toBeVisible();
  await expectNoHorizontalScroll(app);
});
