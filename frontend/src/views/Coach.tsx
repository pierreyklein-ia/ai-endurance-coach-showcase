/* Chat avec le coach : historique, réponses rapides, import FIT, dictée. */
import { useCallback, useEffect, useLayoutEffect, useRef, useState, type ChangeEvent, type FormEvent, type KeyboardEvent, type ReactElement } from "react";
import { api } from "../api/client";
import type { ChatMessage, ChatResponse, CoachPersona, Me, UploadResponse } from "../api/types";
import { CoachAvatar } from "../components/CoachAvatar";
import { TeamStrip, TeamTrace } from "../components/TeamTrace";
import { coachOf } from "../lib/coach";
import { fmtMin, timeOf } from "../lib/format";
import { useDictation } from "../lib/dictation";
import { toast } from "../lib/toast";

const EMPTY_HINT = "Écris-moi comme à un coach : ressenti, fatigue, douleur, contraintes. Chaque changement important du plan attend ton « oui ».";

type Item = { id: number; kind: "msg"; m: ChatMessage } | { id: number; kind: "typing"; text: string } | { id: number; kind: "hint" };

function Bubble({ m, coach, first }: { m: ChatMessage; coach?: CoachPersona; first?: boolean }): ReactElement {
  const cls = m.role === "user" ? "user" : m.kind === "system" ? "system" : m.kind === "ask" ? "coach ask" : "coach";
  if (coach && m.role !== "user" && m.kind !== "system") {
    // v0.36 : avatar et prénom du coach en tête de chaque groupe de messages
    return (
      <div className={"coach-row" + (first ? " first" : "")}>
        <span className="coach-row-av" aria-hidden="true">{first ? <CoachAvatar gender={coach.gender} personality={coach.personality} size={32} head /> : null}</span>
        <div className="coach-row-body">
          {first ? <div className="coach-row-name">{coach.name}</div> : null}
          <Bubble m={m} />
        </div>
      </div>
    );
  }
  return (
    <div className={`msg ${cls}`}>
      {m.text}
      <span className="time">{timeOf(m.at)}</span>
      {m.meta?.eye ? <a className="btn small eye-link" href="#progres">👁 Voir le rapport</a> : null}
      {m.meta && m.meta.trace ? (
        <details className={"msg-details" + (m.meta.routing ? " has-team" : "")}>
          <summary>{m.meta.routing ? <TeamStrip meta={m.meta} /> : null}<span className="details-word">détails</span></summary>
          {m.meta.routing ? <TeamTrace meta={m.meta} /> : null}
          <div className="trace-nodes">{`${m.meta.nodes?.join(" → ") || ""}\n${m.meta.latency_s ?? ""} s · ${m.meta.cost_eur ?? 0} €${m.meta.calendar_ops ? " · agenda mis à jour" : ""}`}</div>
          {m.meta.degraded ? <div className="trace-nodes degraded-detail">{`Claude indisponible : ${m.meta.degraded}${m.meta.degraded_detail ? "\n" + m.meta.degraded_detail : ""}`}</div> : null}
        </details>
      ) : null}
    </div>
  );
}

function TypingRow({ text, id }: { text: string; id?: string }): ReactElement {
  return <div id={id} className="typing" role="status"><span className="typing-dots" aria-hidden="true"><i></i><i></i><i></i></span>{text}</div>;
}

const isCoachMsg = (it: Item | undefined): boolean => !!it && it.kind === "msg" && it.m.role !== "user" && it.m.kind !== "system";

export function Coach({ active, epoch, me, onMeChanged }: {
  active: boolean;
  /** change quand le fil doit être rechargé (retour sur l'app, séance validée avec le coach…) */
  epoch: number;
  me: Me | null;
  onMeChanged: (me: Me) => void;
}): ReactElement {
  const [items, setItems] = useState<Item[]>([]);
  const [quick, setQuick] = useState<string[]>([]);
  const [sending, setSending] = useState(false);
  const [coachTyping, setCoachTyping] = useState(false);   // v0.38 : réaction du coach en tâche de fond
  const lastSig = useRef("");
  const sendingRef = useRef(false);
  const uid = useRef(0);
  const textRef = useRef<HTMLTextAreaElement>(null);
  const levelRef = useRef<HTMLSpanElement>(null);
  const formRef = useRef<HTMLFormElement>(null);
  const [scrollReq, setScrollReq] = useState(0);
  const meRef = useRef(me);
  meRef.current = me;

  const autosize = useCallback(() => {
    const t = textRef.current; if (!t) return;
    t.style.height = "auto";
    const hgt = t.scrollHeight + (t.offsetHeight - t.clientHeight);         // bordures comprises : pas de barre inutile
    t.style.height = Math.min(hgt, 140) + "px";
    t.style.overflowY = hgt > 140 ? "auto" : "hidden";                      // barre de défilement seulement si besoin
  }, []);

  const { ui, ctl } = useDictation({
    getText: () => textRef.current?.value ?? "",
    putText: (v) => { if (textRef.current) { textRef.current.value = v; autosize(); } },
    focus: () => textRef.current?.focus(),
    levelBars: () => Array.from(levelRef.current?.querySelectorAll("i") ?? []),
  });

  const quickChips = (r: ChatResponse) => {
    setQuick(r.quick && r.quick.length ? r.quick : r.pending && !meRef.current?.onboarding ? ["Oui", "Non"] : []);
  };
  const scrollChat = () => setScrollReq((n) => n + 1);
  useLayoutEffect(() => {
    if (!scrollReq) return;
    // v0.35 : en bas du fil, puis encore une fois après la mise en page (polices, bulles longues, clavier)
    const toEnd = () => window.scrollTo({ top: document.documentElement.scrollHeight, behavior: "auto" });
    const id = requestAnimationFrame(() => requestAnimationFrame(toEnd));
    const late = setTimeout(toEnd, 350);
    return () => { cancelAnimationFrame(id); clearTimeout(late); };
  }, [scrollReq]);
  // v0.35 : chaque retour sur l'onglet Coach ouvre la conversation à la fin (le dernier message)
  useEffect(() => { if (active) scrollChat(); }, [active]);

  // v0.38 : le fil est relu à CHAQUE ouverture de l'onglet (requête légère), plus seulement quand `epoch` change :
  // un déplacement fait dans Semaine, ou une réponse arrivée pendant qu'on était ailleurs, est toujours visible.
  const load = useCallback(async (scroll: boolean): Promise<boolean> => {
    if (sendingRef.current) return true;
    const r = await api<ChatResponse>("/api/chat");
    if (sendingRef.current) return true;
    const sig = r.messages.map((m) => m.id ?? m.at).join(",") + (r.typing ? "+" : "");
    if (sig !== lastSig.current) {
      lastSig.current = sig;
      setItems(r.messages.length ? r.messages.map((m) => ({ id: ++uid.current, kind: "msg" as const, m })) : [{ id: ++uid.current, kind: "hint" }]);
      if (scroll || r.typing) scrollChat();
    }
    setCoachTyping(!!r.typing);
    quickChips(r);
    return !!r.typing;
  }, []);

  useEffect(() => {
    if (!active) return;
    lastSig.current = "";
    load(true).catch(() => { /* 401 géré */ });
  }, [active, epoch, load]);

  // v0.38 : pendant que le coach écrit, on relit le fil toutes les 2 s jusqu'à sa réponse
  useEffect(() => {
    if (!active || !coachTyping) return;
    let stop = false;
    const id = setInterval(() => {
      if (stop) return;
      load(false).then((still) => { if (!still) scrollChat(); }).catch(() => {});
    }, 2000);
    return () => { stop = true; clearInterval(id); };
  }, [active, coachTyping, load]);

  const send = async (raw: string) => {
    const text = raw.trim();
    if (!text || sendingRef.current) return;
    sendingRef.current = true; setSending(true);
    const typingId = ++uid.current;
    setItems((cur) => {
      const only = cur.length === 1 && (cur[0].kind === "hint" || (cur[0].kind === "msg" && cur[0].m.role !== "user" && cur[0].m.kind === "system"));
      return [...(only ? [] : cur), { id: ++uid.current, kind: "msg", m: { role: "user", kind: "text", text, at: new Date().toISOString() } },
        { id: typingId, kind: "typing", text: `${coachOf(meRef.current?.coach).name} écrit…` }];
    });
    scrollChat();
    try {
      const r = await api<ChatResponse>("/api/chat", { json: { text } });
      const last = r.messages[r.messages.length - 1];
      setItems((cur) => [...cur.filter((it) => it.id !== typingId), { id: ++uid.current, kind: "msg", m: last }]);
      lastSig.current = "";                                     // la prochaine relecture refait le fil complet
      quickChips(r);
      if (r.calendar_changed) toast("Plan enregistré 📅");
      if (meRef.current?.onboarding) onMeChanged(await api<Me>("/api/me"));
    } catch (err) {
      setItems((cur) => cur.map((it) => (it.id === typingId ? { ...it, text: "⚠ " + (err as Error).message } : it)));
    } finally { sendingRef.current = false; setSending(false); scrollChat(); }
  };

  const onSubmit = (e: FormEvent) => {
    e.preventDefault();
    if (ctl.isActive()) { ctl.stop(); return; }                 // on relit avant d'envoyer
    const t = textRef.current; if (!t) return;
    void send(t.value); t.value = ""; autosize();
  };
  const onKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey && window.matchMedia("(hover: hover)").matches) { e.preventDefault(); formRef.current?.requestSubmit(); }
  };
  const onQuick = (q: string) => {
    if (q === "Je veux reprendre plus tôt") {
      const t = textRef.current; if (!t) return;
      t.value = "Je me sens prêt à reprendre dès lundi"; t.focus(); autosize(); return;
    }
    void send(q);
  };
  const onFit = async (e: ChangeEvent<HTMLInputElement>) => {
    const f = e.target.files?.[0]; e.target.value = "";
    if (!f) return;
    toast("Import en cours…");
    try {
      const r = await api<UploadResponse>(`/api/upload?name=${encodeURIComponent(f.name)}`, { method: "POST", body: f });
      const a = r.activity;
      toast({ new: "Sortie importée ✓", upgraded: "Sortie mise à jour ✓", duplicate: "Déjà connue" }[r.status] + ` · ${fmtMin(a.minutes)}, charge ${a.load}`);
    } catch (err) { toast((err as Error).message); }
  };

  const mm = Math.floor(ui.seconds / 60), ss = ui.seconds % 60;
  const coach = coachOf(me?.coach);
  return (
    <section id="view-coach" className="view view-chat" data-view="coach" hidden={!active}>
      <div id="chat-list" className="chat-list" aria-live="polite">
        {items.map((it, i) => it.kind === "msg" ? <Bubble key={it.id} m={it.m} coach={coach} first={!isCoachMsg(items[i - 1])} />
          : it.kind === "typing" ? <TypingRow key={it.id} text={it.text} />
          : <div key={it.id} className="msg system">{EMPTY_HINT}</div>)}
        {coachTyping && !sending ? <TypingRow text={`${coach.name} écrit…`} id="coach-typing" /> : null}
      </div>
      <div id="chat-quick" className="chat-quick" hidden={!quick.length || coachTyping}>
        {quick.map((t, i) => <button key={i} className="chip" data-quick={t} onClick={() => onQuick(t)}>{t}</button>)}
      </div>
      <form id="chat-form" className="chat-form" ref={formRef} onSubmit={onSubmit}>
        <div id="dict-bar" className={"dict-bar" + (ui.busy ? " busy" : "")} hidden={!ui.on && !ui.busy}>
          <span className="rec-dot" aria-hidden="true"></span>
          <span id="dict-label">{ui.label}</span>
          <span id="dict-level" className="dict-level" aria-hidden="true" ref={levelRef} hidden={ui.mode !== "server" || ui.busy}><i></i><i></i><i></i><i></i><i></i></span>
          <span id="dict-time" className="dict-time" hidden={ui.busy}>{`${mm}:${String(ss).padStart(2, "0")}`}</span>
          <button id="dict-cancel" type="button" className="link-btn" hidden={ui.busy} onClick={() => ctl.cancel()}>Annuler</button>
        </div>
        <label className="icon-btn" title="Importer un fichier FIT">
          <input id="fit-input" type="file" accept=".fit,.gpx,.gz" hidden onChange={onFit} />
          <svg viewBox="0 0 24 24" aria-hidden="true" className="i-plus"><path d="M12 5v14M5 12h14" /></svg><span className="sr">Importer un fichier FIT</span>
        </label>
        <textarea id="chat-text" ref={textRef} rows={1} placeholder="Écris à ton coach…" maxLength={4000} onInput={autosize} onKeyDown={onKeyDown}></textarea>
        <button id="chat-mic" type="button" className={"icon-btn mic" + (ui.on ? " rec" : "") + (ui.busy ? " busy" : "")}
          aria-label={ui.on ? "Arrêter la dictée" : "Dicter un message"} title="Dicter" onClick={() => void ctl.start()}>
          <svg className="i-mic" viewBox="0 0 24 24" aria-hidden="true"><rect x="9" y="3" width="6" height="11" rx="3" /><path d="M5.5 11a6.5 6.5 0 0 0 13 0M12 17.5V21M8.5 21h7" /></svg>
          <svg className="i-stop" viewBox="0 0 24 24" aria-hidden="true"><rect x="7" y="7" width="10" height="10" rx="2" /></svg>
        </button>
        <button id="chat-send" type="submit" className="send" aria-label="Envoyer" disabled={ui.on || ui.busy || sending}>
          <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M5 12h13M12.5 6l6 6-6 6" /></svg>
        </button>
      </form>
    </section>
  );
}
