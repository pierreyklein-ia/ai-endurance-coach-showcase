/* Fixtures communes : remise à zéro du serveur, date figée, connexion, et surveillance des erreurs de la page
   (console, exceptions, violations CSP) vérifiée à la fin de CHAQUE test. */
import { expect, test as base, type Page } from "@playwright/test";

export const PASSCODE = "code-test-1234";
export const TODAY = "2026-11-04T10:00:00";          // même jour que e2e/server.py (TODAY)

type State = "fresh" | "onboarded";

export const test = base.extend<{ errors: string[]; state: State; app: Page }>({
  state: ["onboarded", { option: true }],
  errors: async ({ page }, use) => {
    const errors: string[] = [];
    page.on("console", (m) => {
      // 401 attendu avant la connexion ; 409/422 : refus métier affichés dans l'interface
      if (m.type() === "error" && !/status of (401|409|422)/.test(m.text())) errors.push(m.text());
    });
    page.on("pageerror", (e) => errors.push(`[exception] ${e.message}`));
    await use(errors);
    expect(errors, "erreurs dans la console (CSP, exceptions…)").toEqual([]);
  },
  /** Page connectée, sur l'état demandé (par défaut : accueil terminé), aide de la carrière déjà vue. */
  app: async ({ page, request, state, errors }, use) => {
    void errors;
    expect((await request.post(`/__e2e/reset?state=${state}`)).ok()).toBe(true);
    await page.clock.setFixedTime(new Date(TODAY));
    await page.addInitScript(() => { try { localStorage.setItem("coach.careerHint", "1"); } catch { /* */ } });
    await login(page);
    await use(page);
  },
});

export async function login(page: Page): Promise<void> {
  await page.goto("/");
  await expect(page.locator("#passcode")).toBeVisible();
  await page.fill("#passcode", PASSCODE);
  await page.click("#login-form button");
  await expect(page.locator("#app")).toBeVisible();
}

/** Va sur un onglet par son ancre (comme un toucher sur la barre du bas). */
export async function goTab(page: Page, id: string): Promise<void> {
  await page.evaluate((h) => { location.hash = h; }, `#${id}`);
  await expect(page.locator(`#view-${id}`)).toBeVisible();
}

/** Pas de défilement horizontal (mise en page qui déborde au téléphone). */
export async function expectNoHorizontalScroll(page: Page): Promise<void> {
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
  expect(overflow, "défilement horizontal").toBeLessThanOrEqual(0);
}

export { expect };
