import { api } from "../api/client";
import { toast } from "./toast";

export function b64ToBytes(b64: string): Uint8Array {
  const p = "=".repeat((4 - (b64.length % 4)) % 4);
  const raw = atob((b64 + p).replace(/-/g, "+").replace(/_/g, "/"));
  return Uint8Array.from(raw, (c) => c.charCodeAt(0));
}

export async function enablePush(publicKey: string): Promise<void> {
  if (!("serviceWorker" in navigator) || !("PushManager" in window)) {
    toast("Sur iPhone : ajoute d'abord l'app à l'écran d'accueil (Partager → Sur l'écran d'accueil).", 6000);
    return;
  }
  try {
    const perm = await Notification.requestPermission();
    if (perm !== "granted") { toast("Notifications refusées dans les réglages."); return; }
    const reg = await navigator.serviceWorker.ready;
    const sub = await reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: b64ToBytes(publicKey) as BufferSource });
    await api("/api/push/subscribe", { json: sub.toJSON() });
    await api("/api/push/test", { method: "POST" });
    toast("Notifications activées ✓");
  } catch (err) { toast("Activation impossible : " + (err as Error).message, 5000); }
}
