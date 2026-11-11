/// <reference types="vitest/config" />
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// Numéro de version unique : le fichier VERSION à la racine du dépôt (comme le backend et sw.js).
const VERSION = readFileSync(fileURLToPath(new URL("../VERSION", import.meta.url)), "utf8").trim();

export default defineConfig({
  base: "/static/app/",
  plugins: [react()],
  define: { __APP_VERSION__: JSON.stringify(VERSION) },
  build: {
    outDir: fileURLToPath(new URL("../coach/web/static/app", import.meta.url)),
    emptyOutDir: true,
    // CSP « script-src 'self' » : pas de script en ligne, pas de module preload polyfill injecté en ligne
    modulePreload: { polyfill: false },
    assetsInlineLimit: 0,
  },
  server: {
    // dev : l'API tourne à côté (python -m coach.web serve), les styles partagés sont dans coach/web/static
    fs: { allow: [".."] },
    proxy: { "/api": "http://127.0.0.1:8000", "/static/icon": "http://127.0.0.1:8000", "/sw.js": "http://127.0.0.1:8000",
             "/manifest.webmanifest": "http://127.0.0.1:8000" },
  },
  test: {
    environment: "jsdom",
    globals: true,
    // v0.50 : couverture (npx vitest run --coverage) des aides et des composants ; les vues sont couvertes par e2e/
    coverage: {
      provider: "v8",
      include: ["src/lib/**", "src/components/**"],
      exclude: ["src/**/*.test.*", "src/test/**"],
      reporter: ["text-summary", "text", "html"],
      // seuils un peu sous le niveau atteint (v0.50 : 96 % des lignes) : la CI échoue si la couverture recule
      thresholds: { lines: 94, statements: 94, functions: 93, branches: 83 },
    },
  },
});
