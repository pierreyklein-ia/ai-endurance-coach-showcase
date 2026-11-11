/* Version de l'interface, injectée au build depuis le fichier VERSION du dépôt (vite.config.ts). */
export const APP_VERSION: string = typeof __APP_VERSION__ === "string" ? __APP_VERSION__ : "?";
if (typeof window !== "undefined") window.APP_VERSION = APP_VERSION;
