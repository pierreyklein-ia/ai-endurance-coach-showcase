/* dictée (v0.21)
   1. reconnaissance vocale du navigateur : texte en direct dans le champ ;
   2. sinon (iPhone en app installée, refus, absence) : enregistrement, transcription par le serveur (Whisper).
   Le texte arrive toujours dans le champ, modifiable : rien n'est envoyé sans toucher ➤. */
import { useEffect, useRef, useState } from "react";
import { api } from "../api/client";
import { joinBase, lsGet, lsSet } from "./format";
import { toast } from "./toast";

export const MAX_REC_S = 180;

export interface DictUi {
  on: boolean;          // enregistrement en cours
  busy: boolean;        // transcription en cours
  label: string;
  mode: "browser" | "server" | null;
  seconds: number;
}
export const IDLE: DictUi = { on: false, busy: false, label: "Je t'écoute…", mode: null, seconds: 0 };

export interface DictationDeps {
  getText: () => string;
  /** remplace le contenu du champ (et le redimensionne) */
  putText: (v: string) => void;
  focus: () => void;
  levelBars: () => HTMLElement[];
  onUi: (ui: DictUi) => void;
}

const norm = (t: string): string => t.toLowerCase().replace(/[\s.,;:!?…'’-]+/g, " ").trim();

/** v0.48 : texte dicté reconstruit à partir de TOUS les résultats de la reconnaissance (pas d'ajout morceau par
    morceau). Chrome Android renvoie souvent, en mode continu, des résultats « finaux » cumulatifs (« je cours »,
    « je cours au club », « je cours au club le mardi ») ou répétés à l'identique, et un resultIndex qui repart de 0 :
    ajouter chaque morceau dupliquait la première phrase. Ici un résultat qui reprend le précédent le remplace, un
    résultat identique ou déjà contenu est ignoré. */
export function transcriptFrom(results: ArrayLike<{ isFinal: boolean; 0: { transcript: string } }>): string {
  const parts: string[] = [];
  for (let i = 0; i < results.length; i++) {
    const t = (results[i]?.[0]?.transcript || "").trim();
    if (!t) continue;
    const nt = norm(t);
    const j = parts.findIndex((p) => norm(p) === nt || norm(p).startsWith(nt));
    if (j >= 0) continue;                                   // déjà là (répété, ou plus court qu'un précédent)
    const last = parts.length ? norm(parts[parts.length - 1]) : "";
    if (last && nt.startsWith(last)) { parts[parts.length - 1] = t; continue; }   // cumulatif : on remplace
    parts.push(t);
  }
  return parts.join(" ").replace(/\s+/g, " ").trim();
}

/** Chrome Android : le mode continu est celui qui duplique ; on fait des phrases courtes relancées à la suite. */
export const isAndroid = (): boolean => typeof navigator !== "undefined" && /Android/i.test(navigator.userAgent || "");
export const MAX_RESTARTS = 20;

/** Contrôleur sans React (testable) : même déroulé que l'interface historique. */
export function createDictation(deps: DictationDeps) {
  const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
  const dict = {
    active: false, mode: null as DictUi["mode"], rec: null as SpeechRecognitionLike | null, recorder: null as MediaRecorder | null,
    chunks: [] as Blob[], base: "", t0: 0, timer: 0 as ReturnType<typeof setInterval> | 0, raf: 0, actx: null as AudioContext | null,
    cancelled: false, server: null as boolean | null, userStop: false, restarts: 0, origin: "",
  };
  const setText = (text: string) => deps.putText(joinBase(dict.base, text));

  async function serverStt(): Promise<boolean> {
    if (dict.server === null) {
      try { dict.server = (await api<{ server?: boolean }>("/api/dictation")).server === true; } catch { dict.server = false; }
    }
    return dict.server;
  }

  function dictUI(on: boolean, label = "Je t'écoute…", busy = false) {
    clearInterval(dict.timer);
    deps.onUi({ on, busy, label, mode: dict.mode, seconds: 0 });
    if (on) {
      dict.t0 = Date.now();
      dict.timer = setInterval(() => {
        const s = Math.floor((Date.now() - dict.t0) / 1000);
        deps.onUi({ on, busy, label, mode: dict.mode, seconds: s });
        if (s >= MAX_REC_S) stop();
      }, 250);
    }
  }

  async function start() {
    if (dict.active) { stop(); return; }
    dict.base = dict.origin = deps.getText(); dict.cancelled = false;
    const iosApp = navigator.standalone === true;                 // app iPhone installée : reco du navigateur peu fiable
    const preferServer = lsGet("dict-mode") === "server" || iosApp || !SR;
    const canRecord = typeof navigator.mediaDevices?.getUserMedia === "function" && typeof window.MediaRecorder === "function";
    if (preferServer && (await serverStt()) && canRecord) return startRecorder();
    if (SR) return startBrowser();
    toast("Dictée indisponible ici : utilise le micro du clavier de ton téléphone.", 4000);
  }

  function startBrowser(restart = false) {
    const rec = new SR!();
    const android = isAndroid();
    rec.lang = "fr-FR"; rec.interimResults = true; rec.continuous = !android; rec.maxAlternatives = 1;
    if (!restart) { dict.userStop = false; dict.restarts = 0; }
    let current = "", gotSomething = restart;
    rec.onresult = (e) => {
      current = transcriptFrom(e.results);                // v0.48 : reconstruit à chaque fois, jamais ajouté
      gotSomething = true;
      setText(current);
    };
    rec.onerror = async (e) => {
      if (["not-allowed", "service-not-allowed", "network", "audio-capture", "language-not-supported"].includes(e.error)) {
        end();
        if (e.error !== "not-allowed" && (await serverStt())) { lsSet("dict-mode", "server"); return startRecorder(); }
        toast(e.error === "not-allowed" ? "Autorise le micro pour dicter (réglages du navigateur)." : "Dictée indisponible : utilise le micro du clavier.", 4000);
      }
    };
    rec.onend = () => {
      if (dict.rec !== rec) return;
      if (dict.cancelled) { end(); deps.putText(dict.origin); return; }
      // la phrase dite devient la base : une relance (fin de phrase sur Android) repart d'une liste vide
      if (current) { dict.base = joinBase(dict.base, current); current = ""; }
      const elapsed = (Date.now() - dict.t0) / 1000;
      if (!dict.userStop && gotSomething && dict.restarts < MAX_RESTARTS && elapsed < MAX_REC_S) {
        dict.restarts += 1;                               // on écoute la phrase suivante, sans doublon
        try { startBrowser(true); return; } catch { /* on s'arrête là */ }
      }
      end();
      if (!gotSomething) toast("Je n'ai rien entendu");
      else { deps.putText(dict.base); deps.focus(); }
    };
    dict.rec = rec; dict.mode = "browser"; dict.active = true;
    try { rec.start(); } catch { end(); return; }
    if (!restart) dictUI(true);
  }

  async function startRecorder() {
    let stream: MediaStream;
    try { stream = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true } }); }
    catch { toast("Autorise le micro pour dicter (réglages du téléphone → l'app ou Safari → Micro).", 4500); return; }
    const mime = ["audio/webm;codecs=opus", "audio/mp4", "audio/webm", "audio/ogg;codecs=opus"].find((m) => MediaRecorder.isTypeSupported?.(m));
    const recorder = new MediaRecorder(stream, mime ? { mimeType: mime } : undefined);
    dict.chunks = []; dict.recorder = recorder; dict.mode = "server"; dict.active = true;
    recorder.ondataavailable = (e) => { if (e.data && e.data.size) dict.chunks.push(e.data); };
    recorder.onstop = async () => {
      stream.getTracks().forEach((t) => t.stop());
      const blob = new Blob(dict.chunks, { type: (recorder.mimeType || mime || "audio/webm").split(";")[0] });
      end();
      if (dict.cancelled || blob.size < 1200) { if (!dict.cancelled) toast("Je n'ai rien entendu"); return; }
      dictUI(false, "Transcription…", true);
      try {
        const r = await api<{ text?: string }>("/api/dictation", { method: "POST", body: blob, headers: { "Content-Type": blob.type } });
        if (r.text) { setText(r.text); deps.focus(); } else toast("Je n'ai rien entendu");
      } catch (err) { toast("Transcription impossible : " + (err as Error).message, 4000); }
      finally { dictUI(false, "", false); }
    };
    meter(stream);
    recorder.start(1000);
    dictUI(true);
  }

  function meter(stream: MediaStream) {
    try {
      const AC = window.AudioContext || window.webkitAudioContext; if (!AC) return;
      const actx = new AC(), an = actx.createAnalyser(); an.fftSize = 256;
      actx.createMediaStreamSource(stream).connect(an); dict.actx = actx;
      const buf = new Uint8Array(an.frequencyBinCount);
      const tick = () => {
        an.getByteTimeDomainData(buf);
        let peak = 0; for (const v of buf) peak = Math.max(peak, Math.abs(v - 128));
        const lvl = Math.min(1, peak / 60);
        deps.levelBars().forEach((b, i) => { b.style.transform = `scaleY(${Math.max(0.15, lvl * [0.6, 0.85, 1, 0.85, 0.6][i])})`; });
        dict.raf = requestAnimationFrame(tick);
      };
      tick();
    } catch { /* pas de niveau : sans importance */ }
  }

  function stop() {
    dict.userStop = true;
    if (dict.mode === "browser" && dict.rec) { try { dict.rec.stop(); } catch { end(); } }
    else if (dict.recorder && dict.recorder.state !== "inactive") dict.recorder.stop();
  }

  function end() {
    cancelAnimationFrame(dict.raf);
    if (dict.actx) { dict.actx.close().catch(() => {}); dict.actx = null; }
    dict.active = false;
    dictUI(false);
  }

  function cancel() { dict.cancelled = true; stop(); deps.putText(dict.origin); }

  return { start, stop, cancel, isActive: () => dict.active, dispose: () => { clearInterval(dict.timer); cancelAnimationFrame(dict.raf); } };
}

export type Dictation = ReturnType<typeof createDictation>;

export function useDictation(deps: Omit<DictationDeps, "onUi">): { ui: DictUi; ctl: Dictation } {
  const [ui, setUi] = useState<DictUi>(IDLE);
  const depsRef = useRef(deps);
  depsRef.current = deps;
  const ctlRef = useRef<Dictation | null>(null);
  if (!ctlRef.current) {
    ctlRef.current = createDictation({
      getText: () => depsRef.current.getText(),
      putText: (v) => depsRef.current.putText(v),
      focus: () => depsRef.current.focus(),
      levelBars: () => depsRef.current.levelBars(),
      onUi: setUi,
    });
  }
  useEffect(() => () => ctlRef.current?.dispose(), []);
  return { ui, ctl: ctlRef.current };
}
