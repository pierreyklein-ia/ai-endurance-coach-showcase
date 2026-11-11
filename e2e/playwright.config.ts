/* Tests d'interface (v0.50) : l'application construite (frontend/ → coach/web/static/app), servie par un serveur de
   test déterministe (e2e/server.py : coach hors ligne, faux Strava et faux Google Agenda, date figée), parcourue dans
   Chromium sur un téléphone et sur un ordinateur.  Lancer : cd e2e && npx playwright test  (voir docs/tests.md). */
import { defineConfig, devices } from "@playwright/test";

const PORT = Number(process.env.E2E_PORT || 8967);
const PYTHON = process.env.PYTHON || "python3";

export default defineConfig({
  testDir: "./tests",
  timeout: 45_000,
  expect: { timeout: 8_000 },
  fullyParallel: false,
  workers: 1,                      // un seul serveur, remis à zéro avant chaque test : pas de parallélisme
  retries: process.env.CI ? 1 : 0,
  reporter: process.env.CI ? [["list"], ["html", { open: "never" }]] : "list",
  use: {
    baseURL: `http://127.0.0.1:${PORT}`,
    locale: "fr-FR",
    timezoneId: "Europe/Paris",
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
  projects: [
    { name: "telephone", use: { ...devices["Pixel 7"], browserName: "chromium" } },
    { name: "ordinateur", use: { browserName: "chromium", viewport: { width: 1280, height: 860 } } },
  ],
  webServer: {
    command: `${PYTHON} server.py --port ${PORT}`,
    url: `http://127.0.0.1:${PORT}/manifest.webmanifest`,
    reuseExistingServer: !process.env.CI,
    timeout: 60_000,
    stdout: "pipe",
  },
});
