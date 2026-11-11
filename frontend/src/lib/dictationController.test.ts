/* Contrôleur de dictée : reconnaissance du navigateur (fausse SpeechRecognition) et enregistrement transcrit par le
   serveur (faux MediaRecorder, faux getUserMedia, faux fetch). Les dépendances du navigateur sont remplacées sur
   `window` / `navigator` avant de créer le contrôleur : c'est l'inversion de contrôle côté front. */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { reply, stubFetch } from "../test/fakes";
import { createDictation, type DictUi, isAndroid } from "./dictation";
import { onToast } from "./toast";

class FakeRec {
  static last: FakeRec | null = null;
  static all: FakeRec[] = [];
  lang = ""; interimResults = false; continuous = false; maxAlternatives = 0;
  onresult: ((e: { results: unknown }) => void) | null = null;
  onerror: ((e: { error: string }) => Promise<void> | void) | null = null;
  onend: (() => void) | null = null;
  started = false; stopped = false;
  constructor() { FakeRec.last = this; FakeRec.all.push(this); }
  start() { this.started = true; }
  stop() { this.stopped = true; this.onend?.(); }
  say(...texts: string[]) { this.onresult?.({ results: texts.map((t) => ({ isFinal: true, 0: { transcript: t }, length: 1 })) }); }
}

class FakeRecorder {
  static isTypeSupported = (m: string) => m === "audio/mp4";
  static last: FakeRecorder | null = null;
  state = "inactive"; mimeType: string;
  ondataavailable: ((e: { data: Blob }) => void) | null = null;
  onstop: (() => Promise<void>) | null = null;
  constructor(_s: unknown, o?: { mimeType: string }) { this.mimeType = o?.mimeType || ""; FakeRecorder.last = this; }
  start() { this.state = "recording"; }
  async stopWith(bytes: number) {
    this.ondataavailable?.({ data: new Blob([new Uint8Array(bytes)]) });
    this.state = "inactive";
    await this.onstop?.();
  }
  stop() { this.state = "inactive"; }
}

function harness(text = "") {
  const out = { text, focused: 0, ui: [] as DictUi[] };
  const ctl = createDictation({ getText: () => out.text, putText: (v) => { out.text = v; }, focus: () => { out.focused += 1; },
    levelBars: () => [], onUi: (u) => out.ui.push(u) });
  return { out, ctl };
}

const w = window as unknown as Record<string, unknown>;
const nav = navigator as unknown as Record<string, unknown>;
let toasts: (string | null)[] = [];

beforeEach(() => {
  toasts = [];
  onToast((m) => toasts.push(m));
  localStorage.clear();
  FakeRec.all = [];
});
afterEach(() => {
  delete w.SpeechRecognition; delete w.webkitSpeechRecognition; delete w.MediaRecorder;
  delete nav.mediaDevices;
  vi.unstubAllGlobals(); vi.useRealTimers(); onToast(null);
});

describe("dictée du navigateur", () => {
  it("texte en direct ajouté au texte existant, puis fin à la demande", async () => {
    w.SpeechRecognition = FakeRec;
    const { out, ctl } = harness("Bonjour.");
    await ctl.start();
    const rec = FakeRec.last!;
    expect(rec.started && rec.lang === "fr-FR" && rec.interimResults).toBe(true);
    expect(ctl.isActive()).toBe(true);
    expect(out.ui.at(-1)).toMatchObject({ on: true, mode: "browser" });
    rec.say("je cours", "je cours demain");
    expect(out.text).toBe("Bonjour. je cours demain");
    await ctl.start();                                   // deuxième appui : arrêt
    expect(rec.stopped).toBe(true);
    expect(ctl.isActive()).toBe(false);
    expect(out.focused).toBe(1);
    expect(out.text).toBe("Bonjour. je cours demain");
    ctl.dispose();
  });

  it("rien entendu ; annulation qui remet le texte d'origine", async () => {
    w.webkitSpeechRecognition = FakeRec;
    const { out, ctl } = harness("avant");
    await ctl.start();
    ctl.stop();
    expect(toasts).toContain("Je n'ai rien entendu");
    await ctl.start();
    FakeRec.last!.say("brouillon");
    ctl.cancel();
    expect(out.text).toBe("avant");
  });

  it("Android : phrases courtes relancées automatiquement, sans doublon", async () => {
    w.SpeechRecognition = FakeRec;
    vi.spyOn(navigator, "userAgent", "get").mockReturnValue("Mozilla/5.0 (Linux; Android 14)");
    expect(isAndroid()).toBe(true);
    const { out, ctl } = harness();
    await ctl.start();
    const first = FakeRec.last!;
    expect(first.continuous).toBe(false);
    first.say("je cours le mardi");
    first.onend?.();                                     // fin de phrase : relance
    const second = FakeRec.last!;
    expect(second).not.toBe(first);
    second.say("et le vendredi");
    expect(out.text).toBe("je cours le mardi et le vendredi");
    ctl.stop();
    expect(out.text).toBe("je cours le mardi et le vendredi");
  });

  it("micro refusé : message ; réseau coupé : bascule sur l'enregistrement si le serveur transcrit", async () => {
    w.SpeechRecognition = FakeRec;
    stubFetch({ "GET /api/dictation": { server: false } });
    const { ctl } = harness();
    await ctl.start();
    await FakeRec.last!.onerror?.({ error: "not-allowed" });
    expect(toasts.at(-1)).toContain("Autorise le micro");
    await ctl.start();
    await FakeRec.last!.onerror?.({ error: "network" });
    expect(toasts.at(-1)).toContain("Dictée indisponible");
    await FakeRec.last!.onerror?.({ error: "no-speech" });     // erreur bénigne : rien
  });

  it("ni reconnaissance ni serveur : message", async () => {
    stubFetch({ "GET /api/dictation": reply(500, {}) });
    const { ctl } = harness();
    await ctl.start();
    expect(toasts.at(-1)).toContain("Dictée indisponible ici");
  });
});

describe("dictée transcrite par le serveur", () => {
  function recorderEnv(getUserMedia = vi.fn(async () => ({ getTracks: () => [{ stop: vi.fn() }] }))) {
    w.MediaRecorder = FakeRecorder;
    nav.mediaDevices = { getUserMedia };
    return getUserMedia;
  }

  it("enregistre, envoie l'audio et écrit la transcription", async () => {
    recorderEnv();
    const calls = stubFetch({ "GET /api/dictation": { server: true }, "POST /api/dictation": { text: "séance de seuil demain" } });
    const { out, ctl } = harness("Note :");
    await ctl.start();
    expect(out.ui.at(-1)).toMatchObject({ on: true, mode: "server" });
    expect(FakeRecorder.last!.mimeType).toBe("audio/mp4");
    await FakeRecorder.last!.stopWith(5000);
    expect(out.text).toBe("Note : séance de seuil demain");
    expect(out.focused).toBe(1);
    expect(calls.at(-1)).toMatchObject({ method: "POST", path: "/api/dictation" });
    expect(out.ui.some((u) => u.busy && u.label === "Transcription…")).toBe(true);
  });

  it("enregistrement trop court, transcription vide, erreur du serveur, micro refusé", async () => {
    recorderEnv();
    localStorage.setItem("dict-mode", "server");
    w.SpeechRecognition = FakeRec;
    stubFetch({ "GET /api/dictation": { server: true }, "POST /api/dictation": { text: "" } });
    const { ctl } = harness();
    await ctl.start();
    await FakeRecorder.last!.stopWith(100);
    expect(toasts.at(-1)).toBe("Je n'ai rien entendu");
    await ctl.start();
    await FakeRecorder.last!.stopWith(5000);
    expect(toasts.at(-1)).toBe("Je n'ai rien entendu");
    stubFetch({ "POST /api/dictation": reply(503, { error: "modèle absent" }) });
    await ctl.start();
    await FakeRecorder.last!.stopWith(5000);
    expect(toasts.at(-1)).toBe("Transcription impossible : modèle absent");
    recorderEnv(vi.fn(async () => { throw new Error("refus"); }));
    await ctl.start();
    expect(toasts.at(-1)).toContain("Autorise le micro");
  });

  it("le navigateur échoue (réseau) et le serveur transcrit : bascule et s'en souvient", async () => {
    recorderEnv();
    w.SpeechRecognition = FakeRec;
    stubFetch({ "GET /api/dictation": { server: true } });
    const { ctl } = harness();
    await ctl.start();
    expect(ctl.isActive()).toBe(true);
    await FakeRec.last!.onerror?.({ error: "network" });
    expect(localStorage.getItem("dict-mode")).toBe("server");
    expect(FakeRecorder.last!.state).toBe("recording");
    ctl.stop();
    expect(FakeRecorder.last!.state).toBe("inactive");
  });

  it("s'arrête seul au bout de 3 minutes", async () => {
    vi.useFakeTimers();
    recorderEnv();
    stubFetch({ "GET /api/dictation": { server: true } });
    localStorage.setItem("dict-mode", "server");
    const { out, ctl } = harness();
    await ctl.start();
    vi.advanceTimersByTime(181_000);
    expect(FakeRecorder.last!.state).toBe("inactive");
    expect(out.ui.some((u) => u.seconds >= 180)).toBe(true);
    ctl.dispose();
  });
});
