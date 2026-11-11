import { createRoot } from "react-dom/client";
import "../../coach/web/static/styles.css";      // feuille de style unique, partagée avec l'ancienne interface
import "./coach-persona.css";                     // v0.36 : coach personnalisable
import { App } from "./App";
import { initTheme } from "./lib/theme";
import "./version";

initTheme();
createRoot(document.getElementById("root") as HTMLElement).render(<App />);
if ("serviceWorker" in navigator) navigator.serviceWorker.register("/sw.js").catch(() => {});
