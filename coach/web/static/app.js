"use strict";
/* AI Endurance Coach — interface (v0.28). JavaScript sans dépendance ni build. */

window.APP_VERSION = "0.52.5";
/* v0.29 : thèmes à l'essai, choisis dans Détails et gardés sur cet appareil */
const THEMES = [["classique", "Classique"], ["bd", "BD"], ["crepuscule", "Crépuscule"], ["toon", "Cel-shading"]];
const THEME_COLOR = { classique: "#12805c", bd: "#ff7a1a", crepuscule: "#46437e", toon: "#4bb9ec" };
function applyTheme(t) {
  if (!THEMES.some(([k]) => k === t)) t = "classique";
  document.documentElement.dataset.theme = t;
  try { localStorage.setItem("theme", t); } catch (e) { /* navigation privée */ }
  document.querySelectorAll('meta[name="theme-color"]').forEach((m) => m.setAttribute("content", THEME_COLOR[t]));
  return t;
}
function currentTheme() { return document.documentElement.dataset.theme || "classique"; }
applyTheme((() => { try { return localStorage.getItem("theme"); } catch (e) { return null; } })());
const $ = (s, r = document) => r.querySelector(s);
const JOURS = ["dim.", "lun.", "mar.", "mer.", "jeu.", "ven.", "sam."];
const MOIS = ["janv.", "févr.", "mars", "avr.", "mai", "juin", "juil.", "août", "sept.", "oct.", "nov.", "déc."];
const STATUS = { done: "Faite", modified: "Modifiée", skipped: "Sautée" };
const state = { me: null, weekStart: null, view: "aujourdhui", fb: null, chatLoaded: false, sending: false };

/* ------------------------------------------------------------------ outils */
function h(tag, attrs = {}, ...kids) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === null || v === undefined || v === false) continue;
    if (k === "class") el.className = v;
    else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else el.setAttribute(k, v === true ? "" : v);
  }
  for (const k of kids.flat()) if (k !== null && k !== undefined && k !== false)
    el.append(k instanceof Node ? k : document.createTextNode(String(k)));
  return el;
}
function fill(root, ...kids) {
  root.replaceChildren(...kids.flat(3).filter((k) => k !== null && k !== undefined && k !== false));
}
const d8 = (iso) => new Date(iso + "T12:00:00");
const dayLabel = (iso) => { const d = d8(iso); return `${JOURS[d.getDay()]} ${d.getDate()} ${MOIS[d.getMonth()]}`; };
const fmtMin = (m) => (!m ? "" : m < 60 ? `${m} min` : `${Math.floor(m / 60)} h${m % 60 ? String(m % 60).padStart(2, "0") : ""}`);
const timeOf = (iso) => new Date(iso).toLocaleTimeString("fr-FR", { hour: "2-digit", minute: "2-digit" });
const isoDay = (d) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
const frDateTime = (iso) => { const d = new Date(iso); return `${d.getDate()}/${String(d.getMonth() + 1).padStart(2, "0")} à ${String(d.getHours()).padStart(2, "0")} h ${String(d.getMinutes()).padStart(2, "0")}`; };
function addDays(iso, n) { const d = d8(iso); d.setDate(d.getDate() + n); return isoDay(d); }

function toast(msg, ms = 2600) {
  const t = $("#toast"); t.textContent = msg; t.hidden = false;
  clearTimeout(toast.t); toast.t = setTimeout(() => { t.hidden = true; }, ms);
}

async function api(path, opts = {}) {
  const init = { credentials: "same-origin", headers: {}, ...opts };
  if (opts.json !== undefined) { init.method = init.method || "POST"; init.body = JSON.stringify(opts.json);
    init.headers["Content-Type"] = "application/json"; }
  const r = await fetch(path, init);
  if (r.status === 401 && path !== "/api/login") { showLogin(); throw new Error("connexion requise"); }
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(data.error || `erreur ${r.status}`);
  return data;
}

/* ------------------------------------------------------------------ connexion */
function showLogin() { $("#app").hidden = true; $("#login").hidden = false; $("#passcode").focus(); }
$("#login-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  $("#login-error").textContent = "";
  try { await api("/api/login", { json: { passcode: $("#passcode").value } }); $("#passcode").value = ""; await boot(); }
  catch (err) { $("#login-error").textContent = err.message; }
});

/* ------------------------------------------------------------------ navigation */
function go(view) {
  state.view = view;
  document.querySelectorAll(".view").forEach((v) => { v.hidden = v.dataset.view !== view; });
  document.querySelectorAll(".tabs a").forEach((a) => a.classList.toggle("on", a.dataset.tab === view));
  ({ aujourdhui: renderToday, semaine: renderWeek, coach: renderChat, progres: renderProgress, etat: renderState, details: renderDetails, carriere: renderCareer, onboarding: renderOnboarding })[view]?.();
}
window.addEventListener("hashchange", () => go(location.hash.slice(1) || "aujourdhui"));

function header(me) {
  if (!me.race_name) {
    $("#race-name").textContent = "Bienvenue" + (me.first_name ? " " + me.first_name : "");
    $("#race-sub").textContent = "Configurons ton coach"; $("#race-countdown").textContent = ""; return;
  }
  $("#race-name").textContent = me.race_name;
  $("#race-sub").textContent = `${dayLabel(me.race_date)}`;
  $("#race-countdown").textContent = me.days_to_race > 0 ? `J-${me.days_to_race}` : me.days_to_race === 0 ? "Jour J" : "";
}

/* ------------------------------------------------------------------ premier lancement (v0.11) */
const tabs = (on) => { document.querySelector(".tabs").hidden = !on; };
async function renderOnboarding() {
  const root = $("#view-onboarding");
  let ob;
  try { ob = await api("/api/onboarding"); } catch (e) { return; }
  if (state.view !== "onboarding") return;
  if (ob.step === "done" || ob.step === "chat") { await boot(); return; }
  tabs(false);
  clearTimeout(renderOnboarding.t);
  if (ob.step === "syncing") {
    fill(root, h("section", { class: "welcome" },
      h("img", { src: "/static/icon.svg", alt: "", class: "login-logo" }),
      h("h1", {}, "Je lis ton historique"),
      h("p", { class: "progress-line" }, ob.job?.message || "Connexion à Strava…"),
      h("div", { class: "spinner", "aria-hidden": "true" }),
      h("p", { class: "muted small" }, "Je remonte jusqu'à 5 ans en arrière : une à deux minutes la première fois.")));
    renderOnboarding.t = setTimeout(renderOnboarding, 2000);
    return;
  }
  // étape « connect » : écran d'accueil
  const err = ob.job?.error || (ob.connect === "error" ? ob.connect_error : null);
  const fit = h("input", { type: "file", accept: ".fit,.gpx,.gz", multiple: true, hidden: true });
  fit.addEventListener("change", async () => {
    const files = [...fit.files]; fit.value = "";
    let ok = 0;
    for (const [i, file] of files.entries()) {
      toast(`Import ${i + 1}/${files.length}…`, 10000);
      try { await api(`/api/upload?name=${encodeURIComponent(file.name)}`, { method: "POST", body: file }); ok++; } catch (e) { /* suivant */ }
    }
    toast(`${ok}/${files.length} fichier(s) importé(s)`); renderOnboarding();
  });
  const connectBtn = h("button", { class: "btn primary strava", onclick: async (e) => {
    e.target.disabled = true; e.target.textContent = "Ouverture de Strava…";
    try {
      const r = await api("/api/strava/connect", { json: { origin: location.origin } });
      if (r.status === "redirect") { location.href = r.url; return; }
      if (r.status === "connected") { renderOnboarding(); return; }
      toast(r.message || "Connexion impossible", 6000);
    } catch (err2) { toast(err2.message, 6000); }
    e.target.disabled = false; e.target.textContent = "Connecter Strava";
  } }, ob.strava_connected ? "Continuer avec Strava" : "Connecter Strava");
  fill(root, h("section", { class: "welcome" },
    h("img", { src: "/static/icon.svg", alt: "", class: "login-logo" }),
    h("h1", {}, "Ton coach d'endurance"),
    h("p", {}, "Je commence par lire tes entraînements pour comprendre où tu en es. Ensuite on discute de ton objectif et je te propose un plan, que tu peux ajuster avec moi."),
    ob.strava ? connectBtn : h("p", { class: "error" }, "Strava n'est pas configuré sur ce serveur (Client ID / Secret : relance « bash install.sh »)."),
    h("p", { class: "muted small" }, "Je lis tes activités (lecture seule) pour ton usage personnel. Rien n'est publié sur Strava."),
    err ? h("p", { class: "error" }, err) : null,
    h("details", { class: "alt" }, h("summary", {}, "Pas de Strava ? Importer des fichiers FIT"),
      h("p", { class: "muted" }, "Importe tes sorties des derniers mois (fichiers .fit exportés de ta montre)."),
      h("div", { class: "row-actions" }, h("label", { class: "btn" }, fit, "Choisir des fichiers"),
        ob.activities ? h("button", { class: "btn primary", onclick: async (e) => {
          e.target.disabled = true; e.target.textContent = "Le coach analyse…";
          try { await api("/api/onboarding/start", { method: "POST" }); await boot(); }
          catch (err3) { toast(err3.message, 5000); e.target.disabled = false; e.target.textContent = "Faire mon bilan"; } } },
          `Faire mon bilan (${ob.activities} sorties)`) : null))));
}

/* ------------------------------------------------------------------ séances */
function sessionCard(s, { full = true } = {}) {
  const fb = s.feedback;
  const meta = [fmtMin(s.duration_min), s.elevation_gain_m ? `${s.elevation_gain_m} m D+` : ""].filter(Boolean).join(" · ");
  return h("article", { class: "card" },
    h("div", { class: "card-head" },
      h("span", { class: "eyebrow" }, dayLabel(s.day)),
      h("span", {}, s.key ? h("span", { class: "badge key" }, "clé") : null, " ",
        fb ? h("span", { class: `badge ${fb.status}` }, STATUS[fb.status] + (fb.rpe ? ` · ${fb.rpe}/10` : "")) : null)),
    h("div", { class: "session-title" }, s.title),
    meta ? h("div", { class: "session-meta" }, meta) : null,
    full && s.detail ? h("div", { class: "session-detail" }, s.detail) : null,
    fb && fb.note ? h("div", { class: "note" }, fb.note) : null,
    full ? h("div", { class: "row-actions" },
      h("button", { class: `btn ${fb ? "" : "primary"}`, onclick: () => openFeedback(s) }, fb ? "Modifier mon retour" : "Valider la séance")) : null);
}

async function renderToday() {
  const root = $("#view-aujourdhui");
  if (state.me?.onboarding) {
    fill(root, h("div", { class: "card" }, h("div", { class: "eyebrow" }, "Ton plan arrive"),
      h("p", {}, "On le construit ensemble dans le chat : réponds au coach, il ajuste la proposition jusqu'à ce qu'elle te convienne."),
      h("a", { class: "btn primary", href: "#coach" }, "Aller au chat")));
    return;
  }
  let t;
  try { t = await api("/api/today"); } catch (e) { return; }
  header(t);
  const st = t.state;
  fill(root, 
    t.focus ? h("div", { class: "card" }, h("div", { class: "eyebrow" }, "Cette semaine"), h("div", {}, t.focus),
      t.coach_note ? h("div", { class: "note" }, t.coach_note) : null) : null,
    t.sessions.length ? t.sessions.map((s) => sessionCard(s))
      : h("div", { class: "card empty" }, "Pas de séance prévue aujourd'hui. Repos ✌︎"),
    t.activities.length ? h("div", { class: "card" }, h("div", { class: "eyebrow" }, "Enregistré aujourd'hui"),
      t.activities.map(actRow)) : null,
    t.next ? h("div", { class: "card" }, h("div", { class: "eyebrow" }, "Prochaine séance"),
      h("div", { class: "session-title" }, t.next.title),
      h("div", { class: "session-meta" }, `${dayLabel(t.next.day)} · ${fmtMin(t.next.duration_min)}`)) : null,
    h("div", { class: "metrics" },
      metric("Fraîcheur (TSB)", st.tsb, signed(st.tsb), "forme − fatigue"),
      metric("ACWR", st.acwr, st.acwr === null ? "n/d" : String(st.acwr).replace(".", ","), "aigu / chronique",
        st.acwr > 1.7 ? "bad" : st.acwr > 1.5 ? "warn" : "")));
}
const eur = (x) => (x ?? 0).toFixed(2).replace(".", ",") + " €";
const signed = (x) => (x === null ? "n/d" : (x > 0 ? "+" : "") + x);
function metric(k, raw, v, hint, cls = "") {
  return h("div", { class: `metric ${cls}` }, h("div", { class: "k" }, k), h("div", { class: "v" }, v ?? "n/d"),
    hint ? h("div", { class: "h" }, hint) : null);
}
function actRow(a) {
  const bits = [fmtMin(a.minutes), a.km ? `${String(a.km).replace(".", ",")} km` : "", a.dplus ? `${a.dplus} m D+` : "",
    a.avg_hr ? `FC ${a.avg_hr}` : ""].filter(Boolean).join(" · ");
  return h("div", { class: "act" }, h("div", {}, h("div", { class: "t" }, a.name || a.sport), h("div", { class: "s" },
    `${dayLabel(a.day)} · ${bits}`)), h("div", { class: "l" }, `charge ${a.load}`));
}


/* ------------------------------------------------------------------ progrès (v0.19) */
// Graphiques SVG sans dépendance (fonctionnent hors ligne). Points : [iso, valeur] ; null = trou.
const SVGNS = "http://www.w3.org/2000/svg";
function sv(tag, attrs = {}, ...kids) {
  const el = document.createElementNS(SVGNS, tag);
  for (const [k, v] of Object.entries(attrs)) if (v !== null && v !== undefined) el.setAttribute(k, v);
  for (const k of kids.flat()) if (k) el.append(k instanceof Node ? k : document.createTextNode(String(k)));
  return el;
}
const tms = (iso) => d8(iso).getTime();
const fmtDur = (s) => { if (s === null || s === undefined) return "n/d"; s = Math.round(s);
  const hh = Math.floor(s / 3600), mm = Math.floor((s % 3600) / 60), ss = s % 60;
  return hh ? `${hh} h ${String(mm).padStart(2, "0")}` : `${mm}:${String(ss).padStart(2, "0")}`; };
const fmtPace = (s) => `${Math.floor(s / 60)}:${String(Math.round(s % 60)).padStart(2, "0")}/km`;
const fr1 = (x) => (x === null || x === undefined ? "n/d" : String(Math.round(x * 10) / 10).replace(".", ","));
function niceTicks(lo, hi, n = 4) {
  const span = hi - lo || 1, step0 = span / n, mag = Math.pow(10, Math.floor(Math.log10(step0)));
  const step = [1, 2, 2.5, 5, 10].map((m) => m * mag).find((s) => span / s <= n) || 10 * mag;
  const out = []; for (let v = Math.ceil(lo / step) * step; v <= hi + 1e-9; v += step) out.push(+v.toFixed(6));
  return out;
}
function lineChart({ series, height = 180, yFmt = (v) => fr1(v), zero = false, invert = false, markers = [], note, spark = false }) {
  const W = 640, H = height, L = spark ? 4 : 52, R = spark ? 4 : 10, T = spark ? 6 : 10, B = spark ? 6 : 24;
  const all = series.flatMap((s) => s.points.filter((p) => p[1] !== null && p[1] !== undefined));
  if (all.length < 2) return h("div", { class: "empty" }, "Pas encore assez de données.");
  const xs = all.map((p) => tms(p[0])), ys = all.map((p) => p[1]);
  let x0 = Math.min(...xs), x1 = Math.max(...xs, ...markers.map((m) => tms(m.iso)));
  let y0 = Math.min(...ys), y1 = Math.max(...ys);
  if (zero) { y0 = Math.min(y0, 0); y1 = Math.max(y1, 0); }
  const mid = (y0 + y1) / 2, minSpan = Math.abs(mid) * 0.04 || 1;      // courbe plate : on garde une échelle lisible
  if (y1 - y0 < minSpan) { y0 = mid - minSpan / 2; y1 = mid + minSpan / 2; }
  const pad = (y1 - y0) * 0.08; y0 -= pad; y1 += pad;
  const X = (t) => L + ((t - x0) / (x1 - x0 || 1)) * (W - L - R);
  const Y = (v) => invert ? T + ((v - y0) / (y1 - y0)) * (H - T - B) : H - B - ((v - y0) / (y1 - y0)) * (H - T - B);
  const g = sv("svg", { viewBox: `0 0 ${W} ${H}`, class: "chart-svg", role: "img" });
  const seen = new Set();
  for (const v of spark ? [] : niceTicks(y0, y1)) {
    const lbl = yFmt(v); if (seen.has(lbl)) continue; seen.add(lbl);
    g.append(sv("line", { x1: L, x2: W - R, y1: Y(v), y2: Y(v), class: v === 0 && zero ? "axis0" : "grid" }));
    g.append(sv("text", { x: L - 6, y: Y(v) + 4, "text-anchor": "end", class: "tick" }, lbl));
  }
  const span = (x1 - x0) / 864e5;
  const d = new Date(x0); d.setDate(1); d.setHours(12);
  const stepM = span > 800 ? 12 : span > 400 ? 3 : span > 120 ? 1 : 0;
  if (stepM === 0) { d.setTime(x0); }
  while (!spark && d.getTime() <= x1) {
    if (d.getTime() >= x0) {
      const lbl = stepM >= 12 ? String(d.getFullYear()) : stepM ? MOIS[d.getMonth()] : `${d.getDate()} ${MOIS[d.getMonth()]}`;
      g.append(sv("text", { x: X(d.getTime()), y: H - 6, "text-anchor": "middle", class: "tick" }, lbl));
    }
    if (stepM === 0) d.setDate(d.getDate() + 14); else d.setMonth(d.getMonth() + stepM);
  }
  for (const m of markers) {
    const x = X(tms(m.iso));
    g.append(sv("line", { x1: x, x2: x, y1: T, y2: H - B, class: "marker" }));
    g.append(sv("text", { x: x - 4, y: T + 10, "text-anchor": "end", class: "tick mk" }, m.label));
  }
  for (const s of series) {
    let dpath = "", pen = false;
    for (const [iso, v] of s.points) {
      if (v === null || v === undefined) { pen = false; continue; }
      dpath += `${pen ? "L" : "M"}${X(tms(iso)).toFixed(1)},${Y(v).toFixed(1)}`; pen = true;
    }
    if (s.area && s.points.length) {
      const p0 = s.points.find((p) => p[1] !== null), pl = [...s.points].reverse().find((p) => p[1] !== null);
      g.append(sv("path", { d: `${dpath}L${X(tms(pl[0]))},${Y(y0 + pad)}L${X(tms(p0[0]))},${Y(y0 + pad)}Z`,
        class: `area c-${s.color}` }));
    }
    g.append(sv("path", { d: dpath, class: `ln c-${s.color}` + (s.dashed ? " dashed" : "") }));
  }
  // lecture au doigt : ligne verticale + valeurs du point le plus proche
  const cursor = sv("line", { y1: T, y2: H - B, class: "cursor", visibility: "hidden" });
  const tip = h("div", { class: "chart-tip", hidden: true });
  g.append(cursor);
  const ref = series.flatMap((s) => s.points.map((p) => p[0]));
  const uniq = [...new Set(ref)].sort();
  const show = (ev) => {
    const r = g.getBoundingClientRect(), px = ((ev.clientX - r.left) / r.width) * W;
    const t = x0 + ((px - L) / (W - L - R)) * (x1 - x0);
    let best = uniq[0], bd = Infinity;
    for (const iso of uniq) { const dd = Math.abs(tms(iso) - t); if (dd < bd) { bd = dd; best = iso; } }
    const x = X(tms(best)); cursor.setAttribute("x1", x); cursor.setAttribute("x2", x); cursor.setAttribute("visibility", "visible");
    const vals = series.map((s) => { const p = s.points.find((q) => q[0] === best); return p && p[1] !== null ? `${s.label} ${yFmt(p[1])}` : null; }).filter(Boolean);
    tip.textContent = `${dayLabel(best)} · ${vals.join(" · ")}`; tip.hidden = false;
  };
  g.addEventListener("pointermove", show); g.addEventListener("pointerdown", show);
  g.addEventListener("pointerleave", () => { cursor.setAttribute("visibility", "hidden"); tip.hidden = true; });
  const legend = series.length > 1 ? h("div", { class: "legend" }, series.filter((s) => !s.hideLegend).map((s) =>
    h("span", {}, h("i", { class: `c-${s.color}` + (s.dashed ? " dashed" : "") }), s.label))) : null;
  return h("div", { class: "chart" }, legend, g, tip, note ? h("div", { class: "chart-note" }, note) : null);
}


// ---- profil du coureur en radar (v0.20)
function radarChart(axes) {
  const S = 340, c = S / 2, R = 112, n = axes.length;
  const ang = (i) => -Math.PI / 2 + (2 * Math.PI * i) / n;
  const pt = (i, v) => [c + Math.cos(ang(i)) * R * v / 10, c + Math.sin(ang(i)) * R * v / 10];
  const g = sv("svg", { viewBox: `-34 0 ${S + 68} ${S}`, class: "radar-svg", role: "img", "aria-label": "Profil du coureur" });
  for (const lv of [2, 4, 6, 8, 10]) g.append(sv("polygon", { points: axes.map((_, i) => pt(i, lv).join(",")).join(" "), class: lv === 10 ? "ring outer" : "ring" }));
  axes.forEach((a, i) => {
    const [x, y] = pt(i, 10); g.append(sv("line", { x1: c, y1: c, x2: x, y2: y, class: "spoke" }));
    const [lx, ly] = pt(i, 12.6), anchor = Math.abs(lx - c) < 8 ? "middle" : lx > c ? "start" : "end";
    const words = a.label.split(" ");
    const lines = words.length > 2 ? [words.slice(0, 2).join(" "), words.slice(2).join(" ")] : words.length === 2 && a.label.length > 14 ? words : [a.label];
    const t = sv("text", { x: lx, y: ly - (lines.length - 1) * 6, "text-anchor": anchor, class: "rlabel" + (a.score === null ? " off" : "") });
    lines.forEach((l, k) => t.append(sv("tspan", { x: lx, dy: k ? 12 : 0 }, l)));
    t.append(sv("tspan", { x: lx, dy: 13, class: "rscore" }, a.score === null ? "?" : fr1(a.score)));
    g.append(t);
  });
  const poly = (key, cls) => {
    const pts = axes.map((a, i) => (a[key] === null ? null : pt(i, a[key]))).filter(Boolean);
    if (pts.length >= 3) g.append(sv("polygon", { points: pts.map((p) => p.join(",")).join(" "), class: cls }));
  };
  poly("score_3m", "rp old"); poly("score", "rp now");
  axes.forEach((a, i) => { if (a.score !== null) { const [x, y] = pt(i, a.score); g.append(sv("circle", { cx: x, cy: y, r: 3.5, class: "rdot" })); } });
  return g;
}
function profileCard(pr) {
  if (!pr || !pr.axes) return null;
  const hasOld = pr.axes.some((a) => a.score_3m !== null);
  const rows = pr.axes.map((a) => {
    const d = a.score !== null && a.score_3m !== null ? Math.round((a.score - a.score_3m) * 10) / 10 : null;
    return h("div", { class: "axis" },
      h("div", { class: "axis-head" }, h("b", {}, a.label),
        h("span", { class: "axis-score" }, a.score === null ? "à mesurer" : `${fr1(a.score)}/10`,
          d !== null && Math.abs(d) >= 0.3 ? h("span", { class: d > 0 ? "good" : "warnt" }, ` ${d > 0 ? "+" : "−"}${fr1(Math.abs(d))}`) : null)),
      a.value ? h("div", {}, a.value, h("span", { class: "conf" }, ` · confiance ${a.confidence}`)) : null,
      a.basis ? h("div", { class: "muted small" }, a.basis) : null,
      h("div", { class: "small tip" }, a.tip));
  });
  return h("div", { class: "card" }, h("h3", {}, "Ton profil de coureur"),
    h("div", { class: "muted small" }, "Une note sur 10 par qualité, calculée sur tes données. 10 = repère d'un amateur expert." +
      (hasOld ? " En pointillés : il y a 3 mois." : "")),
    radarChart(pr.axes),
    pr.weakest ? h("div", {}, `Point fort : ${pr.strongest.toLowerCase()} · à travailler en priorité : ${pr.weakest.toLowerCase()}.`) : null,
    h("details", { class: "alt" }, h("summary", {}, "Le détail de chaque note"), rows,
      h("div", { class: "axis" }, h("b", {}, "Non mesurés sans laboratoire"),
        pr.not_measured.map((m) => h("div", { class: "small" }, h("b", {}, m.label), ` : ${m.why} ${m.how}`)))));
}

const RANGES = [["3 mois", 91], ["6 mois", 182], ["1 an", 365], ["5 ans", 1826]];
function getRange() { try { return +localStorage.getItem("progRange") || 182; } catch (e) { return 182; } }
function setRange(d) { try { localStorage.setItem("progRange", d); } catch (e) {} }

async function renderProgress() {
  const root = $("#view-progres");
  if (!root.childElementCount) fill(root, h("div", { class: "empty" }, "Calcul de ta progression…"));
  const days = getRange();
  let p;
  try { p = await api(`/api/progress?days=${days}`); } catch (e) { fill(root, h("div", { class: "empty" }, e.message)); return; }
  if (p.empty) { fill(root, h("div", { class: "empty" }, "Aucune sortie pour l'instant : connecte Strava dans État.")); return; }
  const C = { ctl: "accent", atl: "warn", tsb: "ink" };
  const n = p.now;

  const rangeBar = h("div", { class: "seg" }, RANGES.map(([l, d]) => h("button", { class: "chip" + (d === days ? " on" : ""),
    onclick: () => { setRange(d); renderProgress(); } }, l)));

  // objectifs : estimation du temps et son évolution
  const goals = (p.goals || []).map((g) => {
    const key = `goal:${g.name}`;
    const pts = p.estimates.map((e) => [e.day, e[key] ? e[key][1] : null]);
    const d = g.delta_28d;
    const trend = d === null || d === undefined ? "" : Math.abs(d) < 60 ? "stable sur 4 semaines"
      : `${d < 0 ? "−" : "+"}${fmtDur(Math.abs(d))} en 4 semaines`;
    return h("div", { class: "card goal" },
      h("div", { class: "card-head" }, h("span", { class: "eyebrow" }, "Objectif"), g.days_to !== null ? h("span", { class: "muted" }, `J-${g.days_to}`) : null),
      h("h2", {}, g.name),
      h("div", { class: "muted" }, [g.date ? dayLabel(g.date) : "", g.km ? `${g.km} km` : "", g.dplus ? `${g.dplus} m D+` : ""].filter(Boolean).join(" · ")),
      g.estimate ? h("div", { class: "goal-est" }, h("span", { class: "big" }, `≈ ${fmtDur(g.estimate[1])}`),
        h("span", { class: "muted" }, ` entre ${fmtDur(g.estimate[0])} et ${fmtDur(g.estimate[2])}`)) :
        h("div", { class: "muted" }, "Pas encore de référence de vitesse (un effort plat de 5 km ou plus)."),
      trend ? h("div", { class: d < 0 ? "good" : d > 60 ? "warnt" : "muted" }, trend) : null,
      lineChart({ series: [{ label: "estimation", color: "accent", points: pts }], height: 120, yFmt: fmtDur, invert: true }),
      h("div", { class: "chart-note" }, "Estimation à partir de ta référence de vitesse et de ton endurance, pour un parcours roulant. " +
        "Le terrain technique, la chaleur et la nuit ne sont pas pris en compte."));
  });
  const rf = p.race_form ? h("div", { class: "card" }, h("div", { class: "eyebrow" }, "Forme prévue le jour J"),
    h("div", {}, h("b", {}, `Fraîcheur ${signed(p.race_form.tsb)} (${p.race_form.label})`), ` · forme ${fr1(p.race_form.ctl)}`),
    h("div", { class: "muted" }, `Le ${dayLabel(p.race_form.day)}, si tu suis le plan. Idéal le jour d'une course : entre +5 et +25.`)) : null;
  const nodist = (p.goals_without_distance || []).length ? h("div", { class: "muted small" },
    `Pas de distance connue pour : ${p.goals_without_distance.join(", ")}. Donne-la au coach pour avoir une estimation.`) : null;

  const metrics = h("div", { class: "metrics" },
    metric("Forme (CTL)", n.ctl, fr1(n.ctl), "charge moyenne 42 j"),
    metric("Fatigue (ATL)", n.atl, fr1(n.atl), "charge moyenne 7 j"),
    metric("Fraîcheur (TSB)", n.tsb, signed(n.tsb === null ? null : Math.round(n.tsb)), n.tsb_label, n.tsb < -30 ? "bad" : n.tsb < -10 ? "warn" : ""),
    metric("7 derniers jours", n.km7, `${fr1(n.km7)} km`, `${fr1(n.h7)} h · ${n.dplus7} m D+`));

  const pmcChart = lineChart({
    series: [
      { label: "Forme", color: C.ctl, points: p.pmc.map((r) => [r[0], r[1]]) },
      { label: "Fatigue", color: C.atl, points: p.pmc.map((r) => [r[0], r[2]]) },
      { label: "Forme prévue", color: C.ctl, dashed: true, points: p.projection.map((r) => [r[0], r[1]]) },
      { label: "Fatigue prévue", color: C.atl, dashed: true, hideLegend: true, points: p.projection.map((r) => [r[0], r[2]]) }],
    markers: p.race_form ? [{ iso: p.race_form.day, label: "objectif" }] : [] });
  const tsbChart = lineChart({ height: 130, zero: true,
    series: [{ label: "Fraîcheur", color: C.tsb, points: p.pmc.map((r) => [r[0], r[3]]) },
             { label: "prévue", color: C.tsb, dashed: true, points: p.projection.map((r) => [r[0], r[3]]) }],
    yFmt: (v) => (v > 0 ? "+" : "") + Math.round(v),
    markers: p.race_form ? [{ iso: p.race_form.day, label: "objectif" }] : [] });

  // 7 jours glissants
  state.rollKey = state.rollKey || "km";
  const RK = { km: ["km", 1, (v) => `${fr1(v)} km`], h: ["heures", 2, (v) => `${fr1(v)} h`], dplus: ["D+", 3, (v) => `${Math.round(v)} m`], load: ["charge", 4, (v) => `${Math.round(v)}`] };
  const rollBox = h("div", {});
  const drawRoll = () => {
    const [lbl, i, fmt] = RK[state.rollKey];
    fill(rollBox, h("div", { class: "seg" }, Object.entries(RK).map(([k, v]) => h("button", { class: "chip" + (k === state.rollKey ? " on" : ""),
      onclick: () => { state.rollKey = k; drawRoll(); } }, v[0]))),
      lineChart({ series: [{ label: lbl, color: "accent", area: true, points: p.rolling7.map((r) => [r[0], r[i]]) }], yFmt: fmt }));
  };
  drawRoll();

  // chronos estimés
  const cur = p.estimates_now;
  const chronos = cur ? h("div", { class: "chronos" }, ["5 km", "10 km", "semi", "marathon"].map((k) => {
    const pts = p.estimates.map((e) => [e.day, e[k] ?? null]);
    const vals = pts.filter((q) => q[1] !== null);
    const old = vals.length > 4 ? vals[vals.length - 5][1] : null;
    const dd = old ? cur[k] - old : null;
    return h("div", { class: "chrono" }, h("div", { class: "k" }, k), h("div", { class: "v" }, fmtDur(cur[k])),
      dd !== null && Math.abs(dd) >= 5 ? h("div", { class: dd < 0 ? "good" : "warnt" }, `${dd < 0 ? "−" : "+"}${fmtDur(Math.abs(dd))} / 4 sem.`) : h("div", { class: "muted small" }, "stable"),
      lineChart({ series: [{ label: k, color: "accent", points: pts }], height: 90, yFmt: fmtDur, invert: true, spark: true }));
  })) : h("div", { class: "empty" }, "Pas encore de référence de vitesse : un effort plat de 5 km ou plus suffit.");
  const chronoNote = cur ? h("div", { class: "chart-note" }, `D'après ta référence : ${cur.ref.name} (${dayLabel(cur.ref.day)}), ` +
    `meilleur effort plat des 9 derniers mois. Marathon corrigé de ton endurance (+${Math.round(cur.marathon_gap * 100)} %).`) : null;

  // efficacité aérobie
  const eff = p.efficiency.length >= 3 ? h("div", { class: "card" }, h("h3", {}, `Efficacité en endurance`),
    h("div", { class: "muted small" }, `Allure de tes footings plats ramenée à ${p.efficiency_hr} bpm. Plus elle est rapide, plus ton endurance progresse.`),
    lineChart({ series: [{ label: `allure à ${p.efficiency_hr} bpm`, color: "accent", points: p.efficiency.map((r) => [r[0], r[1]]) }],
      yFmt: fmtPace, invert: true, height: 150 })) : null;

  // semaines
  const wk = [...p.weeks].reverse().slice(0, 8);
  const weeks = h("div", { class: "card" }, h("h3", {}, "Semaines"),
    h("div", { class: "table-wrap" }, h("table", { class: "weeks" },
      h("thead", {}, h("tr", {}, ["Sem.", "km", "h", "D+", "charge", "fait", "mono."].map((t) => h("th", {}, t)))),
      h("tbody", {}, wk.map((w) => h("tr", { class: w.partial ? "partial" : "" },
        h("td", {}, `${dayLabel(w.start).replace(/^\S+ /, "")}${w.partial ? " *" : ""}`), h("td", {}, w.km), h("td", {}, fr1(w.hours)),
        h("td", {}, w.dplus), h("td", {}, w.load),
        h("td", {}, w.planned ? `${w.done}/${w.planned}` : "—"),
        h("td", { class: w.monotony >= 2 ? "warnt" : "" }, w.monotony === null ? "—" : fr1(w.monotony))))))),
    h("div", { class: "chart-note" }, "* semaine en cours. Fait : jours prévus où tu as bien couru. Monotonie (Foster) : au-delà de 2, les journées se ressemblent trop ; alterner facile et dur limite le risque de surmenage."));

  // activités
  const acts = h("div", { class: "card" }, h("div", { class: "card-head" }, h("h3", {}, "Activités")), h("div", { id: "acts-list" }));

  fill(root, rangeBar, goals, rf, nodist, profileCard(p.profile), metrics,
    h("div", { class: "card" }, h("h3", {}, "Forme et fatigue"), h("div", { class: "muted small" }, "Forme : charge moyenne des 6 dernières semaines. Fatigue : des 7 derniers jours. En pointillés : ce que donne le plan s'il est suivi."), pmcChart),
    h("div", { class: "card" }, h("h3", {}, "Fraîcheur"), h("div", { class: "muted small" }, "Forme moins fatigue. Négative quand tu charges, positive quand tu récupères ou t'affûtes."), tsbChart),
    h("div", { class: "card" }, h("h3", {}, "Cumul sur 7 jours glissants"), rollBox),
    h("div", { class: "card" }, h("h3", {}, "Chronos estimés"), chronos, chronoNote),
    eff, weeks, acts);
  loadActivities(true);
}

async function loadActivities(reset) {
  const box = $("#acts-list"); if (!box) return;
  if (reset) { state.actsOffset = 0; state.actsSport = state.actsSport || ""; }
  const first = state.actsOffset === 0;
  const q = new URLSearchParams({ limit: first ? 5 : 20, offset: state.actsOffset });   // v0.23 : 5 par défaut
  if (state.actsSport) q.set("sport", state.actsSport);
  if (state.actsQuery) q.set("q", state.actsQuery);
  const ticket = (state.actsTicket = (state.actsTicket || 0) + 1);
  let r; try { r = await api(`/api/activities?${q}`); } catch (e) { return; }
  if (ticket !== state.actsTicket) return;                        // une recherche plus récente est partie
  if (first) {
    if (!box.querySelector(".acts-tools")) {
      const input = h("input", { type: "search", class: "acts-search", placeholder: "Rechercher : nom, mois, « 50 km »…",
                                 "aria-label": "Rechercher une activité", value: state.actsQuery || "" });
      let t; input.addEventListener("input", () => { clearTimeout(t); t = setTimeout(() => { state.actsQuery = input.value.trim(); loadActivities(true); }, 250); });
      const seg = r.sports.length > 1 ? h("div", { class: "seg" }, ["", ...r.sports].map((s) => h("button", {
        class: "chip" + (s === state.actsSport ? " on" : ""), "data-sport": s,
        onclick: () => { state.actsSport = s; box.querySelectorAll(".seg .chip").forEach((c) => c.classList.toggle("on", c.dataset.sport === s)); loadActivities(true); } }, s || "Toutes"))) : null;
      box.append(h("div", { class: "acts-tools" }, input, seg), h("div", { class: "acts-rows" }));
    }
    box.querySelector(".acts-rows").replaceChildren();
  }
  const rows = box.querySelector(".acts-rows");
  box.querySelector(".more")?.remove(); box.querySelector(".acts-count")?.remove();
  if (first && !r.activities.length) rows.append(h("div", { class: "muted small" }, "Aucune activité trouvée."));
  r.activities.forEach((a) => rows.append(actRow(a)));
  state.actsOffset = r.next_offset ?? state.actsOffset + r.activities.length;
  if (r.next_offset !== null && r.next_offset !== undefined)
    box.append(h("button", { class: "btn small more", onclick: () => loadActivities(false) }, `Voir plus (${r.total - state.actsOffset} autres)`));
  else if (r.total > 5) box.append(h("div", { class: "muted small acts-count" }, `${r.total} activités`));
}

/* ------------------------------------------------------------------ validation */
function openFeedback(s) {
  state.fb = { s, status: s.feedback?.status || "done", rpe: s.feedback?.rpe || null };
  $("#fb-title").textContent = s.title;
  $("#fb-sub").textContent = `${dayLabel(s.day)}${s.duration_min ? " · " + fmtMin(s.duration_min) : ""}`;
  $("#fb-note").value = s.feedback?.note || "";
  $("#fb-notify").checked = false;
  paintFeedback();
  $("#fb-sheet").showModal();
}
function paintFeedback() {
  document.querySelectorAll("#fb-status button").forEach((b) => b.classList.toggle("on", b.dataset.v === state.fb.status));
  const rpe = $("#fb-rpe");
  rpe.replaceChildren(...Array.from({ length: 10 }, (_, i) => h("button", {
    type: "button", class: state.fb.rpe === i + 1 ? "on" : "", "aria-label": `RPE ${i + 1}`,
    onclick: () => { state.fb.rpe = state.fb.rpe === i + 1 ? null : i + 1; paintFeedback(); } }, i + 1)));
  const labels = { 1: "très facile", 3: "facile", 5: "modéré", 6: "soutenu", 7: "dur", 8: "très dur", 10: "maximal" };
  $("#fb-rpe-val").textContent = state.fb.rpe ? `${state.fb.rpe}/10 ${labels[state.fb.rpe] || ""}` : "";
}
$("#fb-status").addEventListener("click", (e) => { const v = e.target.dataset?.v; if (v) { state.fb.status = v; paintFeedback(); } });
$("#fb-note").addEventListener("input", () => { if ($("#fb-note").value.trim().length > 3) $("#fb-notify").checked = true; });
$("#fb-cancel").addEventListener("click", () => $("#fb-sheet").close());
$("#fb-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const { s, status, rpe } = state.fb;
  const notify = $("#fb-notify").checked;
  $("#fb-save").disabled = true;
  try {
    const r = await api(`/api/sessions/${encodeURIComponent(s.session_id)}/feedback`,
      { json: { status, rpe, note: $("#fb-note").value, notify_coach: notify } });
    $("#fb-sheet").close();
    if (r.coach_error) toast("Séance validée ✓ — le coach n'a pas pu répondre : réessaie dans le chat.", 4500);
    else toast(notify ? "Séance validée, le coach a répondu" : "Séance validée ✓");
    if (notify && r.messages?.length) { state.chatLoaded = false; location.hash = "#coach"; }
    else go(state.view);
  } catch (err) { toast(err.message); }
  finally { $("#fb-save").disabled = false; }
});

/* ------------------------------------------------------------------ semaine */
async function renderWeek() {
  const root = $("#view-semaine");
  let w;
  try { w = await api("/api/week" + (state.weekStart ? `?start=${state.weekStart}` : "")); } catch (e) { return; }
  state.weekStart = w.start;
  const pct = w.planned_min ? Math.min(100, Math.round((100 * w.done_min) / w.planned_min)) : 0;
  const bar = h("i"); bar.style.width = pct + "%";
  fill(root, 
    h("div", { class: "week-nav" },
      h("button", { class: "btn small", onclick: () => { state.weekStart = addDays(w.start, -7); renderWeek(); } }, "‹"),
      h("div", { class: "title" }, `Semaine du ${dayLabel(w.start)}`, w.phase ? h("div", { class: "muted" }, w.phase) : null),
      h("button", { class: "btn small", onclick: () => { state.weekStart = addDays(w.start, 7); renderWeek(); } }, "›")),
    h("div", { class: "card" },
      h("div", { class: "card-head" }, h("span", { class: "eyebrow" }, "Fait / prévu"),
        h("span", { class: "muted" }, `${fmtMin(w.done_min) || "0 min"} / ${fmtMin(w.planned_min) || "—"}`)),
      h("div", { class: "progress" }, bar),
      w.focus ? h("div", {}, w.focus) : null,
      w.review ? h("details", {}, h("summary", {}, `Bilan du coach${w.reviewed_at ? " · " + w.reviewed_at : ""}`),
        h("div", { class: "note" }, w.review)) : null),
    !w.days.some((d) => d.sessions.length) && w.keys.length ? h("div", { class: "card" },
      h("div", { class: "eyebrow" }, "Séances clés prévues"),
      h("div", { class: "muted" }, "Le détail jour par jour arrive avec le bilan du dimanche précédent."),
      w.keys.map((k) => h("div", { class: "act" }, h("div", { class: "t" }, k.day), h("div", {}, k.text)))) : null,
    w.days.map((d) => h("div", { class: `day ${d.date === w.today ? "today" : ""}` },
      h("div", { class: "day-head" }, h("span", {}, dayLabel(d.date)), d.date === w.today ? h("span", { class: "badge key" }, "aujourd'hui") : null),
      h("div", { class: "day-body" },
        d.sessions.length ? d.sessions.map((s) => h("div", { class: "item", onclick: () => openDetail(s) },
          h("div", {}, h("div", { class: "it" }, s.title), h("div", { class: "im" },
            [fmtMin(s.duration_min), s.key ? "clé" : ""].filter(Boolean).join(" · "))),
          s.feedback ? h("span", { class: `badge ${s.feedback.status}` }, STATUS[s.feedback.status]) : h("span", { class: "muted" }, "›")))
          : h("div", { class: "off" }, w.days.some((x) => x.sessions.length) ? "Repos" : "À détailler"),
        d.activities.map((a) => h("div", { class: "off" }, `✓ ${a.name || a.sport} · ${fmtMin(a.minutes)}${a.km ? " · " + String(a.km).replace(".", ",") + " km" : ""}`))))));
}
function openDetail(s) {
  const root = $("#view-semaine");
  const back = h("button", { class: "btn small", onclick: renderWeek }, "‹ Semaine");
  fill(root, h("div", { class: "week-nav" }, back), sessionCard(s));
  window.scrollTo(0, 0);
}

/* ------------------------------------------------------------------ chat */
function bubble(m) {
  const cls = m.role === "user" ? "user" : m.kind === "system" ? "system" : m.kind === "ask" ? "coach ask" : "coach";
  return h("div", { class: `msg ${cls}` }, m.text,
    h("span", { class: "time" }, timeOf(m.at)),
    m.meta && m.meta.trace ? h("details", {}, h("summary", {}, "détails"),
      `${m.meta.nodes?.join(" → ") || ""}\n${m.meta.latency_s ?? ""} s · ${m.meta.cost_eur ?? 0} €${m.meta.calendar_ops ? " · agenda mis à jour" : ""}`) : null);
}
async function renderChat(force = false) {
  if (state.chatLoaded && !force) return;
  try {
    const r = await api("/api/chat");
    const list = $("#chat-list");
    fill(list, ...(r.messages.length ? r.messages.map(bubble)
      : [h("div", { class: "msg system" }, "Écris-moi comme à un coach : ressenti, fatigue, douleur, contraintes. Chaque changement important du plan attend ton « oui ».")]));
    quickChips(r);
    state.chatLoaded = true;
    scrollChat();
  } catch (e) { /* 401 géré */ }
}
const scrollChat = () => requestAnimationFrame(() => window.scrollTo(0, document.body.scrollHeight));
async function send(text) {
  text = text.trim();
  if (!text || state.sending) return;
  state.sending = true; $("#chat-send").disabled = true;
  const list = $("#chat-list");
  if (list.querySelector(".msg.system:only-child")) list.replaceChildren();
  list.append(bubble({ role: "user", kind: "text", text, at: new Date().toISOString() }));
  const typing = h("div", { class: "typing" }, "Le coach réfléchit…");
  list.append(typing); scrollChat();
  try {
    const r = await api("/api/chat", { json: { text } });
    typing.remove();
    list.append(bubble(r.messages[r.messages.length - 1]));
    quickChips(r);
    if (r.calendar_changed) toast("Plan enregistré 📅");
    if (state.me?.onboarding) { state.me = await api("/api/me"); header(state.me); }
  } catch (err) { typing.textContent = "⚠ " + err.message; }
  finally { state.sending = false; $("#chat-send").disabled = false; scrollChat(); }
}
$("#chat-form").addEventListener("submit", (e) => {
  e.preventDefault();
  if (dict.active) { stopDictation(); return; }                 // on relit avant d'envoyer
  const t = $("#chat-text"); send(t.value); t.value = ""; autosize();
});
$("#chat-text").addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey && window.matchMedia("(hover: hover)").matches) { e.preventDefault(); $("#chat-form").requestSubmit(); }
});
function autosize() {
  const t = $("#chat-text"); t.style.height = "auto";
  const hgt = t.scrollHeight + (t.offsetHeight - t.clientHeight);         // bordures comprises : pas de barre inutile
  t.style.height = Math.min(hgt, 140) + "px";
  t.style.overflowY = hgt > 140 ? "auto" : "hidden";                      // barre de défilement seulement si besoin
}
$("#chat-text").addEventListener("input", autosize);

/* ------------------------------------------------------------------ dictée (v0.21)
   1. reconnaissance vocale du navigateur : texte en direct dans le champ ;
   2. sinon (iPhone en app installée, refus, absence) : enregistrement, transcription par le serveur (Whisper).
   Le texte arrive toujours dans le champ, modifiable : rien n'est envoyé sans toucher ➤. */
const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
const dict = { active: false, mode: null, rec: null, recorder: null, stream: null, chunks: [], base: "",
               t0: 0, timer: 0, raf: 0, actx: null, cancelled: false, server: null };
const MAX_REC_S = 180;
const lsGet = (k) => { try { return localStorage.getItem(k); } catch { return null; } };
const lsSet = (k, v) => { try { localStorage.setItem(k, v); } catch { /* navigation privée */ } };
async function serverStt() {
  if (dict.server === null) { try { dict.server = (await api("/api/dictation")).server === true; } catch { dict.server = false; } }
  return dict.server;
}
const joinBase = (base, text) => (base && text ? base.replace(/\s+$/, "") + " " + text.trim() : (base || "") + (text || "").trim());
function setText(text) { const t = $("#chat-text"); t.value = joinBase(dict.base, text); autosize(); }
function dictUI(on, label = "Je t'écoute…", busy = false) {
  const bar = $("#dict-bar"), mic = $("#chat-mic");
  bar.hidden = !on && !busy; bar.classList.toggle("busy", busy);
  $("#dict-label").textContent = label;
  $("#dict-level").hidden = dict.mode !== "server" || busy;
  $("#dict-time").hidden = busy;
  $("#dict-cancel").hidden = busy;
  mic.classList.toggle("rec", on); mic.classList.toggle("busy", busy);
  mic.setAttribute("aria-label", on ? "Arrêter la dictée" : "Dicter un message");
  $("#chat-send").disabled = on || busy || state.sending;
  clearInterval(dict.timer);
  if (on) {
    dict.t0 = Date.now(); $("#dict-time").textContent = "0:00";
    dict.timer = setInterval(() => {
      const s = Math.floor((Date.now() - dict.t0) / 1000);
      $("#dict-time").textContent = `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
      if (s >= MAX_REC_S) stopDictation();
    }, 250);
  }
}
async function startDictation() {
  if (dict.active) { stopDictation(); return; }
  dict.base = $("#chat-text").value; dict.cancelled = false;
  const iosApp = navigator.standalone === true;                 // app iPhone installée : reco du navigateur peu fiable
  const preferServer = lsGet("dict-mode") === "server" || iosApp || !SR;
  if (preferServer && (await serverStt()) && navigator.mediaDevices?.getUserMedia && window.MediaRecorder) return startRecorder();
  if (SR) return startBrowser();
  toast("Dictée indisponible ici : utilise le micro du clavier de ton téléphone.", 4000);
}
function startBrowser() {
  const rec = new SR();
  rec.lang = "fr-FR"; rec.interimResults = true; rec.continuous = true; rec.maxAlternatives = 1;
  let finalText = "", gotSomething = false;
  rec.onresult = (e) => {
    let interim = "";
    for (let i = e.resultIndex; i < e.results.length; i++) {
      const r = e.results[i];
      if (r.isFinal) finalText += r[0].transcript + " "; else interim += r[0].transcript;
    }
    gotSomething = true;
    setText(finalText + interim);
  };
  rec.onerror = async (e) => {
    if (["not-allowed", "service-not-allowed", "network", "audio-capture", "language-not-supported"].includes(e.error)) {
      endDictation();
      if (e.error !== "not-allowed" && (await serverStt())) { lsSet("dict-mode", "server"); return startRecorder(); }
      toast(e.error === "not-allowed" ? "Autorise le micro pour dicter (réglages du navigateur)." : "Dictée indisponible : utilise le micro du clavier.", 4000);
    }
  };
  rec.onend = () => {
    if (dict.rec !== rec) return;
    endDictation();
    if (dict.cancelled) { setText(""); return; }
    if (!gotSomething) toast("Je n'ai rien entendu");
    else { const t = $("#chat-text"); setText(finalText.trim() || t.value.slice(dict.base.length)); t.focus(); }
  };
  dict.rec = rec; dict.mode = "browser"; dict.active = true;
  try { rec.start(); } catch { endDictation(); return; }
  dictUI(true);
}
async function startRecorder() {
  let stream;
  try { stream = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true } }); }
  catch { toast("Autorise le micro pour dicter (réglages du téléphone → l'app ou Safari → Micro).", 4500); return; }
  const mime = ["audio/webm;codecs=opus", "audio/mp4", "audio/webm", "audio/ogg;codecs=opus"].find((m) => MediaRecorder.isTypeSupported?.(m));
  const recorder = new MediaRecorder(stream, mime ? { mimeType: mime } : undefined);
  dict.chunks = []; dict.stream = stream; dict.recorder = recorder; dict.mode = "server"; dict.active = true;
  recorder.ondataavailable = (e) => { if (e.data && e.data.size) dict.chunks.push(e.data); };
  recorder.onstop = async () => {
    stream.getTracks().forEach((t) => t.stop());
    const blob = new Blob(dict.chunks, { type: (recorder.mimeType || mime || "audio/webm").split(";")[0] });
    endDictation();
    if (dict.cancelled || blob.size < 1200) { if (!dict.cancelled) toast("Je n'ai rien entendu"); return; }
    dictUI(false, "Transcription…", true);
    try {
      const r = await api("/api/dictation", { method: "POST", body: blob, headers: { "Content-Type": blob.type } });
      if (r.text) { setText(r.text); $("#chat-text").focus(); } else toast("Je n'ai rien entendu");
    } catch (err) { toast("Transcription impossible : " + err.message, 4000); }
    finally { dictUI(false, "", false); $("#dict-bar").hidden = true; }
  };
  meter(stream);
  recorder.start(1000);
  dictUI(true);
}
function meter(stream) {
  try {
    const AC = window.AudioContext || window.webkitAudioContext; if (!AC) return;
    const actx = new AC(), an = actx.createAnalyser(); an.fftSize = 256;
    actx.createMediaStreamSource(stream).connect(an); dict.actx = actx;
    const buf = new Uint8Array(an.frequencyBinCount), bars = [...document.querySelectorAll("#dict-level i")];
    const tick = () => {
      an.getByteTimeDomainData(buf);
      let peak = 0; for (const v of buf) peak = Math.max(peak, Math.abs(v - 128));
      const lvl = Math.min(1, peak / 60);
      bars.forEach((b, i) => { b.style.transform = `scaleY(${Math.max(0.15, lvl * [0.6, 0.85, 1, 0.85, 0.6][i])})`; });
      dict.raf = requestAnimationFrame(tick);
    };
    tick();
  } catch { /* pas de niveau : sans importance */ }
}
function stopDictation() {
  if (dict.mode === "browser" && dict.rec) { try { dict.rec.stop(); } catch { endDictation(); } }
  else if (dict.recorder && dict.recorder.state !== "inactive") dict.recorder.stop();
}
function endDictation() {
  cancelAnimationFrame(dict.raf);
  if (dict.actx) { dict.actx.close().catch(() => {}); dict.actx = null; }
  dict.active = false;
  dictUI(false);
}
$("#chat-mic").addEventListener("click", startDictation);
$("#dict-cancel").addEventListener("click", () => { dict.cancelled = true; stopDictation(); setText(""); });
function quickChips(r) {
  const box = $("#chat-quick");
  const labels = r.quick && r.quick.length ? r.quick : r.pending && !state.me?.onboarding ? ["Oui", "Non"] : [];
  fill(box, labels.map((t) => h("button", { class: "chip", "data-quick": t }, t)));
  box.hidden = !labels.length;
}
$("#chat-quick").addEventListener("click", (e) => {
  const q = e.target.dataset?.quick; if (!q) return;
  if (q === "Je veux reprendre plus tôt") { const t = $("#chat-text"); t.value = "Je me sens prêt à reprendre dès lundi"; t.focus(); autosize(); return; }
  send(q);
});
$("#fit-input").addEventListener("change", async (e) => {
  const f = e.target.files[0]; e.target.value = "";
  if (!f) return;
  toast("Import en cours…");
  try {
    const r = await api(`/api/upload?name=${encodeURIComponent(f.name)}`, { method: "POST", body: f });
    const a = r.activity;
    toast({ new: "Sortie importée ✓", upgraded: "Sortie mise à jour ✓", duplicate: "Déjà connue" }[r.status] +
      ` · ${fmtMin(a.minutes)}, charge ${a.load}`);
  } catch (err) { toast(err.message); }
});

/* ------------------------------------------------------------------ état */
async function renderState() {
  const root = $("#view-etat");
  let s;
  try { s = await api("/api/state"); } catch (e) { return; }
  const me = state.me || {};
  fill(root, 
    h("div", { class: "metrics" },
      metric("Forme (CTL)", s.ctl, s.ctl ?? "n/d", "charge chronique 42 j"),
      metric("Fatigue (ATL)", s.atl, s.atl ?? "n/d", "charge aiguë 7 j"),
      metric("Fraîcheur (TSB)", s.tsb, signed(s.tsb), "forme − fatigue"),
      metric("ACWR", s.acwr, s.acwr === null ? "n/d" : String(s.acwr).replace(".", ","),
        s.history_days < 28 ? "historique < 28 j : peu fiable" : "alerte 1,5 · blocage 1,7",
        s.acwr > 1.7 ? "bad" : s.acwr > 1.5 ? "warn" : "")),
    memoryCard(s.memory),
    h("div", { class: "card" },
      h("div", { class: "card-head" }, h("span", { class: "eyebrow" }, "Sorties (14 jours)"),
        h("button", { class: "btn small", onclick: syncNow }, "Synchroniser")),
      s.last_sync ? h("div", { class: "muted" }, `Dernière synchro Strava : ${s.last_sync.replace("T", " ").slice(0, 16)}`) : null,
      s.activities.length ? s.activities.map(actRow) : h("div", { class: "empty" }, "Aucune sortie. Synchronise Strava ou importe un FIT depuis le chat.")));
}
function memoryCard(m) {
  // v0.17 : ce que tu m'as dit et le plan validé, enregistrés et donnés au coach à chaque message
  if (!m || !m.commitments || !m.commitments.length) return null;
  const a = m.agreed_plan;
  return h("div", { class: "card" },
    h("div", { class: "eyebrow" }, "Ce que tu m'as dit"),
    h("ul", { class: "memory" }, m.commitments.map(c => h("li", {}, c))),
    a ? h("div", { class: "muted" }, `Plan validé le ${a.adopted_at.slice(8, 10)}/${a.adopted_at.slice(5, 7)} à ${a.adopted_at.slice(11, 16)} : `
        + `${a.sessions} séances du ${a.first_day.slice(8, 10)}/${a.first_day.slice(5, 7)} au ${a.last_day.slice(8, 10)}/${a.last_day.slice(5, 7)}`
        + (m.versions > 1 ? ` (version ${m.versions})` : "")) : h("div", { class: "muted" }, m.has_plan
          ? "Plan validé avant la v0.17 : il est dans Semaine. Son texte sera enregistré à ta prochaine validation."
          : "Plan pas encore validé."),
    a && a.plan_text ? h("details", { class: "alt" }, h("summary", {}, "Revoir le plan validé"),
        h("pre", { class: "plan-text" }, a.plan_text)) : null,
    h("div", { class: "muted" }, "Enregistré sur le serveur et sauvegardé chaque nuit. Pour changer quelque chose, dis-le au coach dans le chat."));
}
/* ------------------------------------------------------------------ détails (v0.28) */
async function renderDetails() {
  const root = $("#view-details");
  let a = {};
  try { a = await api("/api/about"); } catch (e) { /* page partielle */ }
  const me = state.me || {};
  const row = (k, v) => h("div", { class: "kv" }, h("span", { class: "k" }, k), h("span", { class: "v" }, v));
  const dict = a.dictation || {};
  const img = a.image || {};
  const pushBtn = h("button", { class: "btn small", onclick: enablePush }, "Activer les notifications");
  const themeCard = h("div", { class: "card" },
    h("div", { class: "eyebrow" }, "Thème (à l'essai)"),
    h("div", { class: "muted small" }, "Change l'apparence de toute l'app, sur cet appareil seulement."),
    h("div", { class: "seg theme-pick" }, THEMES.map(([k, label]) => h("button", { type: "button",
      class: currentTheme() === k ? "on" : null, onclick: () => { applyTheme(k); renderDetails(); } }, label))));
  fill(root,
    themeCard,
    h("div", { class: "card about" },
      h("div", { class: "eyebrow" }, "Application"),
      h("div", { class: "ver" }, a.version ? "v" + a.version : "version inconnue"),
      h("div", { class: "muted small" }, "Si ce numéro n'est pas le dernier déployé, recharge la page en force ou ferme puis rouvre l'app."),
      row("Interface chargée", "v" + (window.APP_VERSION || "?")),
      row("Règles de sécurité", a.registry ? `${a.registry} (${a.registry_hash})` : "n/d"),
      row("Fiches scientifiques", a.cards ?? "n/d"),
      row("Coach", a.llm || "n/d"),
      row("Dictée serveur", dict.server ? (dict.ready ? `prête (${dict.model})` : dict.loading ? "chargement du modèle…" : "installée, modèle à charger") : "indisponible (micro du navigateur)"),
      row("Image d'avatar (Gemini)", img.enabled ? `activée · ${img.today}/${img.per_day} aujourd'hui` : "désactivée (avatar dessiné)"),
      img.enabled ? h("div", { class: "row-actions" }, h("button", { class: "btn small", onclick: async (e) => {
        e.target.disabled = true; toast("Test de la clé Gemini…", 8000);
        try { const r = await api("/api/about/image-check", { method: "POST" }); toast((r.ok ? "✓ " : "✗ ") + r.message, 9000);
          $("#gem-result").textContent = (r.ok ? "✓ " : "✗ ") + r.message; } catch (err) { toast(err.message); }
        e.target.disabled = false; } }, "Tester la clé Gemini")) : null,
      h("div", { id: "gem-result", class: "muted small" }),
      row("Date du serveur", a.today || "n/d")),
    h("div", { class: "card" }, h("div", { class: "eyebrow" }, "Connexions"),
      h("div", {}, `Agenda : ${me.google ? "Google ✓" : "local"} · Strava : ${me.strava ? "✓" : "non connecté"}`),
      h("div", { class: "row-actions" }, h("button", { class: "btn small", onclick: async () => {
        try { const r = await api("/api/strava/connect", { json: { origin: location.origin, force: true } });
          if (r.status === "redirect") location.href = r.url; else toast(r.message || "Strava connecté ✓"); }
        catch (e) { toast(e.message); } } }, me.strava ? "Reconnecter Strava" : "Connecter Strava")),
      h("div", { class: "muted" }, `Coût Claude aujourd'hui : ${eur(me.spent_today_eur)} / ${eur(me.budget_eur)}`),
      h("div", { class: "row-actions" }, me.push_public_key ? pushBtn : h("span", { class: "muted" }, "Notifications non configurées sur le serveur"),
        h("button", { class: "btn small", onclick: logout }, "Se déconnecter")),
      h("details", { class: "alt" }, h("summary", {}, "Recommencer à zéro"),
        h("p", { class: "muted" }, "Efface tes sorties, ton plan et la discussion, pour refaire le parcours d'accueil. Une sauvegarde complète est faite juste avant ; la connexion Strava est gardée."),
        h("button", { class: "btn small danger", onclick: async (e) => {
          if (e.target.dataset.armed !== "1") { e.target.dataset.armed = "1"; e.target.textContent = "Confirmer : tout effacer"; return; }
          await api("/api/reset", { json: { confirm: "recommencer" } });
          state.chatLoaded = false; location.hash = ""; await boot(); } }, "Tout effacer et recommencer"))));
}
async function syncNow() {
  toast("Synchronisation…", 8000);
  try { const r = await api("/api/sync", { method: "POST" }); toast(r.message); renderState(); } catch (e) { toast(e.message); }
}
async function logout() { await api("/api/logout", { method: "POST" }).catch(() => {}); showLogin(); }

/* ------------------------------------------------------------------ notifications */
function b64ToBytes(b64) {
  const p = "=".repeat((4 - (b64.length % 4)) % 4);
  const raw = atob((b64 + p).replace(/-/g, "+").replace(/_/g, "/"));
  return Uint8Array.from(raw, (c) => c.charCodeAt(0));
}
async function enablePush() {
  if (!("serviceWorker" in navigator) || !("PushManager" in window)) {
    toast("Sur iPhone : ajoute d'abord l'app à l'écran d'accueil (Partager → Sur l'écran d'accueil).", 6000); return;
  }
  try {
    const perm = await Notification.requestPermission();
    if (perm !== "granted") { toast("Notifications refusées dans les réglages."); return; }
    const reg = await navigator.serviceWorker.ready;
    const sub = await reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: b64ToBytes(state.me.push_public_key) });
    await api("/api/push/subscribe", { json: sub.toJSON() });
    await api("/api/push/test", { method: "POST" });
    toast("Notifications activées ✓");
  } catch (err) { toast("Activation impossible : " + err.message, 5000); }
}

/* ------------------------------------------------------------------ démarrage */
/* ------------------------------------------------------------------ carrière (v0.24) : XP, niveaux, avatar */
const AV = {
  skin: ["#f6d7c3", "#eac0a0", "#d9a27e", "#b97a55", "#8d5a3b", "#5e3b26"],
  hairColor: ["#1f1a17", "#4a2f1f", "#8a5a2b", "#c99a4f", "#b8b2a8", "#a83a2a"],
  shirt: ["#1f7a4d", "#2563eb", "#dc2626", "#f59e0b", "#7c3aed", "#0f172a", "#ec4899", "#14b8a6"],
  shorts: ["#0f172a", "#334155", "#1e3a8a", "#14532d", "#7f1d1d", "#e5e7eb"],
  shoes: ["#f97316", "#22c55e", "#e11d48", "#3b82f6", "#facc15", "#f8fafc"],
  hair: [["court", "Court"], ["long", "Queue de cheval"], ["chignon", "Chignon"], ["boucles", "Bouclés"], ["rase", "Rasé"]],
};
const TIER = { 1: ["bronze", "#b87333"], 2: ["argent", "#9ca3af"], 3: ["or", "#eab308"], 4: ["légende", "#8b5cf6"] };
let avatarUid = 0;
function wornGear(cfg, gear) {
  const unlocked = new Set((gear || []).filter((g) => g.unlocked).map((g) => g.id));
  const w = new Set(cfg.gear === null || cfg.gear === undefined ? unlocked : cfg.gear.filter((g) => unlocked.has(g)));
  if (w.has("casquette")) w.delete("bandeau");
  return w;
}
function avatarSvg(cfg, level, gear, scene, { size = 160, head = false } = {}) {
  const id = `av${++avatarUid}`;
  const skin = AV.skin[cfg.skin] || AV.skin[2], hair = AV.hairColor[cfg.hair_color] || AV.hairColor[1];
  const w = wornGear(cfg, gear);
  const shirt = AV.shirt[cfg.shirt] || AV.shirt[0], shorts = AV.shorts[cfg.shorts] || AV.shorts[1];
  const shoes = w.has("chaussures_or") ? "#eab308" : AV.shoes[cfg.shoes] || AV.shoes[2];
  const vb = head ? "44 14 48 48" : "0 0 120 150";
  const g = sv("svg", { viewBox: vb, width: size, height: head ? size : size * 1.25, class: "avatar-svg", role: "img", "aria-label": `Avatar niveau ${level}` });
  const defs = sv("defs", {}, sv("clipPath", { id: `${id}c` }, head ? sv("circle", { cx: 68, cy: 38, r: 24 }) : sv("rect", { x: 0, y: 0, width: 120, height: 150, rx: 22 })));
  g.append(defs);
  const root = sv("g", { "clip-path": `url(#${id}c)` }); g.append(root);
  // décor : évolue avec le niveau
  const sc = {
    route: ["#dbeafe", "#bfdbfe"], collines: ["#e0f2fe", "#bbf7d0"], montagne: ["#e0e7ff", "#c7d2fe"],
    nuit: ["#1e1b4b", "#312e81"], sommet: ["#fde68a", "#fdba74"], foret: ["#dcfce7", "#86efac"],
    ville: ["#e0e7ff", "#cbd5e1"], bord_de_mer: ["#e0f2fe", "#fde68a"], neige: ["#e0f2fe", "#f8fafc"], volcan: ["#fed7aa", "#44403c"],
  }[scene] || ["#dbeafe", "#bfdbfe"];
  root.append(sv("rect", { x: 0, y: 0, width: 120, height: 150, fill: sc[0] }));
  if (scene === "nuit") { for (const [x, y] of [[14, 18], [30, 34], [96, 14], [104, 40], [80, 26], [20, 60]]) root.append(sv("circle", { cx: x, cy: y, r: 1.2, fill: "#fef9c3" })); root.append(sv("circle", { cx: 96, cy: 26, r: 7, fill: "#fef3c7" })); }
  if (scene === "sommet") root.append(sv("circle", { cx: 90, cy: 52, r: 18, fill: "#fff7ed", opacity: ".8" }));
  if (scene === "collines") root.append(sv("path", { d: "M0 112 Q30 88 60 108 T120 100 V150 H0Z", fill: "#86efac" }));
  if (scene === "montagne" || scene === "nuit" || scene === "sommet") {
    const m = scene === "nuit" ? "#4338ca" : scene === "sommet" ? "#b45309" : "#818cf8";
    root.append(sv("path", { d: "M-5 120 L30 62 L52 96 L78 50 L125 120Z", fill: m }));
    if (scene !== "nuit") root.append(sv("path", { d: "M78 50 L70 63 L76 60 L81 66 L86 61Z M30 62 L24 72 L31 69 L35 72Z", fill: "#fff" }));
  }
  // v0.30 : décors gagnés avec les trophées
  if (scene === "foret") for (const [x, hgt] of [[10, 70], [28, 56], [96, 64], [112, 74]]) { root.append(sv("path", { d: `M${x} ${136 - hgt} l-12 ${hgt} h24z`, fill: "#16a34a" })); root.append(sv("path", { d: `M${x} ${136 - hgt} l12 ${hgt} h-12z`, fill: "#15803d" })); }
  if (scene === "ville") for (const [x, w2, hgt] of [[0, 18, 70], [20, 14, 50], [86, 16, 62], [104, 16, 84]]) { root.append(sv("rect", { x, y: 136 - hgt, width: w2, height: hgt, fill: "#94a3b8" })); for (let yy = 136 - hgt + 6; yy < 130; yy += 10) root.append(sv("rect", { x: x + 4, y: yy, width: 4, height: 4, fill: "#fef9c3" })); }
  if (scene === "bord_de_mer") { root.append(sv("rect", { x: 0, y: 96, width: 120, height: 40, fill: "#22d3ee" })); root.append(sv("path", { d: "M0 100 q10 -4 20 0 t20 0 t20 0 t20 0 t20 0 t20 0", fill: "none", stroke: "#fff", "stroke-width": 1.5 })); root.append(sv("circle", { cx: 98, cy: 30, r: 10, fill: "#fde047" })); }
  if (scene === "neige") { root.append(sv("path", { d: "M-5 120 L30 58 L52 92 L80 44 L125 120Z", fill: "#cbd5e1" })); root.append(sv("path", { d: "M80 44 L66 68 L74 64 L80 72 L88 64 L94 68Z M30 58 L20 76 L28 72 L34 78 L40 74Z", fill: "#fff" })); for (const [x, y] of [[10, 20], [40, 30], [70, 14], [100, 26], [20, 50], [110, 60]]) root.append(sv("circle", { cx: x, cy: y, r: 1.6, fill: "#fff" })); }
  if (scene === "volcan") { root.append(sv("path", { d: "M-5 136 L40 60 L54 60 L70 66 L84 60 L125 136Z", fill: "#57534e" })); root.append(sv("path", { d: "M54 60 Q62 70 70 66 L64 90 Q60 100 56 112", fill: "none", stroke: "#f97316", "stroke-width": 2.5 })); root.append(sv("path", { d: "M50 52 q4 -12 12 -8 q6 -10 14 -2", fill: "none", stroke: "#a8a29e", "stroke-width": 3, opacity: ".7" })); }
  root.append(sv("rect", { x: 0, y: 136, width: 120, height: 14, fill: sc[1] }));
  root.append(sv("ellipse", { cx: 60, cy: 138, rx: 26, ry: 3.5, fill: "#000", opacity: ".12" }));
  if (level >= 5 && !head) for (const y of [70, 80, 90]) root.append(sv("path", { d: `M${14 + (y % 20)} ${y} h14`, stroke: "#fff", "stroke-width": 2.5, "stroke-linecap": "round", opacity: ".7" }));
  const limb = (pts, color, wdt) => sv("polyline", { points: pts.map((p) => p.join(",")).join(" "), fill: "none", stroke: color, "stroke-width": wdt, "stroke-linecap": "round", "stroke-linejoin": "round" });
  if (w.has("aura")) root.append(sv("ellipse", { cx: 64, cy: 80, rx: 38, ry: 56, fill: "#fde047", opacity: ".28" }));
  const stars = Math.max(0, level - 12);
  for (let i = 0; i < Math.min(stars, 8) && !head; i++) root.append(sv("path", { d: "M0 -4 L1.2 -1.2 L4 -1 L1.8 .9 L2.5 3.8 L0 2.2 L-2.5 3.8 L-1.8 .9 L-4 -1 L-1.2 -1.2Z", fill: "#facc15", stroke: "#a16207", "stroke-width": .4, transform: `translate(${108 - (i % 4) * 10} ${12 + Math.floor(i / 4) * 10})` }));
  const fig = sv("g", {}); root.append(fig);
  // bâtons (derrière)
  if (w.has("batons")) { fig.append(limb([[44, 62], [30, 137]], "#475569", 2)); fig.append(limb([[86, 62], [100, 137]], "#475569", 2)); }
  // queue de cheval (derrière la tête)
  if (cfg.hair === "long") fig.append(sv("path", { d: "M58 33 Q44 34 40 48 Q48 42 57 41Z", fill: hair }));
  // jambe et bras arrière
  fig.append(limb([[60, 88], [49, 106]], shorts, 9)); fig.append(limb([[49, 106], [37, 120]], skin, 6.5));
  fig.append(sv("path", { d: "M31 118 l9 4 l-2 5 l-11 -3z", fill: shoes, stroke: "#0002", "stroke-width": .8 }));
  fig.append(limb([[62, 56], [51, 70], [44, 62]], skin, 5.5));
  // torse
  fig.append(sv("path", { d: "M58 52 Q66 47 72 53 L68 90 Q61 93 55 89Z", fill: shirt }));
  if (w.has("coupe_vent")) { fig.append(sv("path", { d: "M57 52 Q66 46 73 53 L69 91 Q61 94 54 90Z", fill: "#0ea5e9", opacity: ".85" })); fig.append(sv("path", { d: "M65 50 L62 91", stroke: "#e0f2fe", "stroke-width": .8 })); }
  if (w.has("gilet")) {
    fig.append(sv("path", { d: "M57 54 L61 52 L59 88 L55 88Z M69 53 L72 54 L68 89 L64 89Z", fill: "#111827", opacity: ".85" }));
    fig.append(sv("rect", { x: 66, y: 58, width: 5, height: 9, rx: 2, fill: "#38bdf8", stroke: "#0369a1", "stroke-width": .6 }));
  }
  if (w.has("dossard")) { fig.append(sv("rect", { x: 58, y: 66, width: 11, height: 9, rx: 1.5, fill: "#fff", stroke: "#94a3b8", "stroke-width": .6 })); fig.append(sv("text", { x: 63.5, y: 73, "text-anchor": "middle", "font-size": 6, "font-weight": 800, fill: "#0f172a" }, String(level))); }
  if (w.has("medaille")) { fig.append(sv("path", { d: "M62 52 L65 62 L68 52", fill: "none", stroke: "#dc2626", "stroke-width": 1.6 })); fig.append(sv("circle", { cx: 65, cy: 64, r: 3, fill: "#eab308", stroke: "#a16207", "stroke-width": .6 })); }
  // short, jambe avant
  fig.append(sv("path", { d: "M54 86 L69 87 L70 95 L56 96Z", fill: shorts }));
  fig.append(limb([[63, 90], [77, 104]], shorts, 9)); fig.append(limb([[77, 104], [73, 128]], skin, 6.5));
  fig.append(sv("path", { d: "M70 126 l12 0 q3 0 3 3 l0 2 l-16 0z", fill: shoes, stroke: "#0002", "stroke-width": .8 }));
  if (w.has("manchons")) { fig.append(limb([[76.4, 108], [74, 122]], "#111827", 6.8)); fig.append(limb([[46.5, 109.5], [40.5, 117]], "#111827", 6.8)); }
  if (w.has("guetres")) { fig.append(sv("rect", { x: 69.5, y: 121.5, width: 10, height: 5.5, rx: 1.5, fill: "#64748b" })); fig.append(sv("rect", { x: 31, y: 114, width: 9, height: 5.5, rx: 1.5, fill: "#64748b", transform: "rotate(20 35 117)" })); }
  // cou, tête
  fig.append(limb([[66, 46], [66, 52]], skin, 6));
  fig.append(sv("circle", { cx: 68, cy: 38, r: 11, fill: skin }));
  fig.append(sv("circle", { cx: 73.5, cy: 36.5, r: 1.3, fill: "#1f2937" }));
  fig.append(sv("path", { d: "M72 43 q2.5 1.2 4.5 -.6", fill: "none", stroke: "#7c2d12", "stroke-width": 1, "stroke-linecap": "round" }));
  fig.append(sv("circle", { cx: 63, cy: 39, r: 2.2, fill: skin, stroke: "#0001" }));
  if (w.has("buff")) { fig.append(sv("rect", { x: 61.5, y: 46, width: 10, height: 5.5, rx: 2, fill: "#f97316" })); fig.append(sv("path", { d: "M62 48.5 h9", stroke: "#fde68a", "stroke-width": 1 })); }
  // cheveux
  const hairD = {
    court: "M57 37 Q56 24 69 25 Q80 25 79 34 Q73 30 66 31 Q61 32 60 39Z",
    long: "M57 38 Q56 24 69 25 Q80 25 79 34 Q73 30 66 31 Q61 32 60 40Z",
    chignon: "M57 37 Q56 24 69 25 Q80 25 79 34 Q73 30 66 31 Q61 32 60 39Z",
    boucles: "M56 39 Q53 30 58 26 Q61 21 67 23 Q73 20 77 25 Q82 27 79 33 Q73 29 66 31 Q61 32 60 40Z",
    rase: "M57.5 36 Q58 26 69 26 Q78 26 79 33 Q72 29.5 66 30.5 Q61 31.5 60 38Z",
  }[cfg.hair] || "";
  if (cfg.hair === "chignon") fig.append(sv("circle", { cx: 58, cy: 29, r: 4.5, fill: hair }));
  if (cfg.hair === "boucles") for (const [x, y] of [[57, 31], [60, 25], [66, 22], [73, 22], [78, 27]]) fig.append(sv("circle", { cx: x, cy: y, r: 3.2, fill: hair }));
  fig.append(sv("path", { d: hairD, fill: hair, opacity: cfg.hair === "rase" ? ".55" : "1" }));
  if (cfg.beard) fig.append(sv("path", { d: "M61 41 Q63 50 70 50 Q77 49 78 42 Q74 45 70 45 Q65 45 61 41Z", fill: hair, opacity: ".9" }));
  // équipement de tête
  if (w.has("laurier")) { fig.append(sv("circle", { cx: 68, cy: 37, r: 15, fill: "none", stroke: "#facc15", "stroke-width": 1.2, opacity: ".7" })); for (let i = 0; i < 7; i++) { const a = Math.PI * (1.05 + i * 0.14); fig.append(sv("ellipse", { cx: 68 + Math.cos(a) * 11.5, cy: 35 + Math.sin(a) * 11.5, rx: 2.6, ry: 1.3, fill: "#16a34a", transform: `rotate(${(a * 180) / Math.PI + 90} ${68 + Math.cos(a) * 11.5} ${35 + Math.sin(a) * 11.5})` })); } }
  if (w.has("bandeau")) fig.append(sv("path", { d: "M57.3 33 Q68 28.5 79.2 32.5 L79.6 35.5 Q68 31.5 57.4 36.2Z", fill: "#ef4444" }));
  if (w.has("casquette")) { fig.append(sv("path", { d: "M57 33 Q57 23 69 23 Q80 24 80 32Z", fill: "#f8fafc", stroke: "#cbd5e1", "stroke-width": .7 })); fig.append(sv("path", { d: "M78 31 Q86 31 89 33 Q84 34.5 79 33.6Z", fill: "#e2e8f0" })); }
  if (w.has("lunettes")) { fig.append(sv("path", { d: "M60 35.5 L70 35", stroke: "#111827", "stroke-width": .9 })); fig.append(sv("rect", { x: 69.5, y: 34, width: 9.5, height: 4, rx: 2, fill: "#111827" })); fig.append(sv("path", { d: "M71 35 h3", stroke: "#60a5fa", "stroke-width": .8 })); }
  if (w.has("frontale")) { fig.append(sv("path", { d: "M79 33 L120 22 L120 50Z", fill: "#fde047", opacity: scene === "nuit" ? ".45" : ".25" })); fig.append(sv("rect", { x: 77, y: 31, width: 4, height: 4, rx: 1, fill: "#facc15", stroke: "#a16207", "stroke-width": .5 })); }
  // bras avant (devant le torse)
  fig.append(limb([[67, 56], [77, 70], [87, 62]], skin, 5.5));
  if (w.has("gants")) { fig.append(sv("circle", { cx: 87, cy: 62, r: 2.8, fill: "#334155" })); fig.append(sv("circle", { cx: 44, cy: 62, r: 2.8, fill: "#334155" })); }
  if (w.has("poignets")) { fig.append(sv("rect", { x: 78, y: 66.5, width: 4.5, height: 3, rx: 1, fill: "#ef4444", transform: "rotate(-40 80 68)" })); fig.append(sv("rect", { x: 45, y: 64.5, width: 4.5, height: 3, rx: 1, fill: "#ef4444", transform: "rotate(48 47 66)" })); }
  if (w.has("montre")) fig.append(sv("rect", { x: 82.5, y: 63, width: 4, height: 3.4, rx: 1, fill: "#111827", transform: "rotate(-40 84.5 64.7)" }));
  return g;
}

function avatarPic(c, size, head) {
  const im = c.avatar_image;
  if (im && im.ready) {
    const img = h("img", { src: im.url, alt: `Avatar niveau ${c.level}`, class: head ? "av-img head" : "av-img", width: size, height: head ? size : Math.round(size * 1.25), loading: "lazy" });
    img.addEventListener("error", () => img.replaceWith(avatarSvg(c.avatar, c.level, c.gear, c.scene, { size, head })));
    return img;
  }
  return avatarSvg(c.avatar, c.level, c.gear, c.scene, { size, head });
}
async function loadCareer(force) {
  if (state.career && !force) return state.career;
  try { state.career = await api("/api/career"); } catch (e) { return null; }
  paintMeAvatar(); return state.career;
}
function paintMeAvatar() {
  const c = state.career, a = $("#me-avatar"); if (!c || !a) return;
  fill(a, avatarPic(c, 40, true), h("span", { class: "lvl" }, c.level));
  a.hidden = !!state.me?.onboarding;
  a.classList.toggle("ping", (c.new || []).length > 0 || c.level_up);
}
const medal = (tier, done) => {
  const [, col] = TIER[tier];
  return sv("svg", { viewBox: "0 0 24 24", width: 30, height: 30, class: "medal" + (done ? "" : " off"), "aria-hidden": "true" },
    sv("path", { d: "M7 2h4l2 6-4 1z M17 2h-4l-2 6 4 1z", fill: done ? "#ef4444" : "currentColor", opacity: done ? 1 : .35 }),
    sv("circle", { cx: 12, cy: 15, r: 7, fill: done ? col : "none", stroke: done ? "#0003" : "currentColor", "stroke-width": 1.5, opacity: done ? 1 : .45 }),
    done ? sv("path", { d: "M12 11.2l1.2 2.4 2.6.4-1.9 1.8.5 2.6-2.4-1.3-2.4 1.3.5-2.6-1.9-1.8 2.6-.4z", fill: "#fff", opacity: ".9" }) : null);
};
async function renderCareer() {
  const root = $("#view-carriere");
  const c = await loadCareer(true); if (!c || state.view !== "carriere") return;
  const fr = (n) => n.toLocaleString("fr-FR");
  const bar = h("i"); bar.style.width = `${Math.max(2, c.pct)}%`;
  const newIds = new Set((c.new || []).map((t) => t.id));
  const hero = h("div", { class: "card career-hero" + (c.level_up ? " levelup" : "") },
    h("div", { class: "hero-av" }, avatarPic(c, 150, false),
      c.avatar_image?.generating ? h("div", { class: "muted small" }, "✨ Nouvelle image en préparation…") : null,
      c.avatar_image?.error && !c.avatar_image?.generating ? h("div", { class: "muted small" }, c.avatar_image.error) : null),
    h("div", { class: "hero-txt" },
      h("div", { class: "lvl-big" }, `Niveau ${c.level}`), h("div", { class: "rank" }, c.rank),
      h("div", { class: "xpbar", role: "progressbar", "aria-valuenow": c.pct, "aria-valuemin": 0, "aria-valuemax": 100 }, bar),
      h("div", { class: "muted small" }, `${fr(c.xp)} XP · encore ${fr(c.next_at - c.xp)} XP pour le niveau ${c.level + 1}`),
      h("div", { class: "muted small" }, `${c.count} trophées sur ${c.total}`),
      h("button", { class: "btn small", onclick: openAvatarEditor }, c.avatar_set ? "Modifier mon avatar" : "Créer mon avatar"),
      c.avatar_image?.next_at && !c.avatar_image?.generating ? h("div", { class: "muted small" }, `Nouvelle image possible le ${frDateTime(c.avatar_image.next_at)}`) : null,
      c.avatar_image?.enabled && c.avatar_image?.ready && !c.avatar_image?.generating && !c.avatar_image?.next_at ? h("button", { class: "btn small", onclick: async () => {
        try { await api("/api/career/avatar/regenerate", { method: "POST" }); toast("Nouvelle image en préparation…"); renderCareer(); } catch (err) { toast(err.message); } } }, "Nouvelle image") : null));
  if (c.avatar_image?.generating) setTimeout(() => { if (state.view === "carriere") renderCareer(); else loadCareer(true); }, 25000);
  const banners = [];
  if (c.first_visit) banners.push(h("div", { class: "card banner" }, h("b", {}, "Ta carrière est reconstruite ! "),
    `J'ai relu ton historique : ${c.count} trophées, ${fr(c.xp)} XP, niveau ${c.level} d'un coup. La suite se gagne à l'entraînement.`));
  else if (c.level_up) banners.push(h("div", { class: "card banner pop" }, h("b", {}, `Niveau ${c.level} ! `), `Tu es ${c.rank.toLowerCase()}.` +
    (c.gear.find((g) => g.level === c.level) ? ` Nouvel équipement : ${c.gear.find((g) => g.level === c.level).label.toLowerCase()}.` : "")));
  if (c.new?.length) banners.push(h("div", { class: "card banner pop" }, h("b", {}, c.new.length > 1 ? `${c.new.length} nouveaux trophées : ` : "Nouveau trophée : "),
    c.new.slice(0, 4).map((t) => `${t.label} (+${t.xp} XP)`).join(", ")));
  // prochains objectifs : le plus petit palier encore à gagner dans chaque famille
  const order = { route: ["vitesse", "jalons", "regularite", "force", "intelligence", "endurance"],
                  trail: ["endurance", "jalons", "force", "regularite", "intelligence", "vitesse"] }[c.discipline] || Object.keys(c.families);
  const next = order.map((f) => c.trophies.filter((t) => t.family === f && !t.day).sort((a, b) => a.tier - b.tier || a.xp - b.xp)[0]).filter(Boolean);
  const goals = h("div", { class: "card" }, h("h3", {}, "Prochains objectifs"),
    next.map((t) => h("div", { class: "goal-row" }, medal(t.tier, false), h("div", {}, h("div", { class: "t" }, t.label), h("div", { class: "muted small" }, t.progress || t.desc)), h("span", { class: "xp" }, `+${t.xp} XP`))));
  const fams = order.map((f) => [f, c.families[f]]).map(([f, label]) => {
    const list = c.trophies.filter((t) => t.family === f);
    const n = list.filter((t) => t.day).length;
    return h("details", { class: "card fam", open: f === "jalons" ? true : null },
      h("summary", {}, h("b", {}, label), h("span", { class: "muted small" }, ` ${n}/${list.length}`)),
      h("div", { class: "tro-grid" }, list.map((t) => h("div", { class: "tro" + (t.day ? " on" : "") + (newIds.has(t.id) ? " new" : ""), title: t.desc },
        medal(t.tier, !!t.day), h("div", { class: "t" }, t.label),
        h("div", { class: "muted small" }, t.day ? dayLabel(t.day) + " " + t.day.slice(0, 4) : (t.progress || TIER[t.tier][0])),
        h("div", { class: "xp" }, `${t.day ? "" : "+"}${t.xp} XP`)))));
  });
  const events = [...c.levels.map((l) => ({ day: l.day, text: `Niveau ${l.level} · ${l.rank}`, lvl: true })), ...c.timeline.map((t) => ({ day: t.day, text: `${t.label}`, xp: t.xp }))]
    .sort((a, b) => (a.day < b.day ? 1 : a.day > b.day ? -1 : a.lvl ? -1 : 1)).slice(0, 25);
  const timeline = h("details", { class: "card" }, h("summary", {}, h("b", {}, "Ta frise")),
    h("ol", { class: "frise" }, events.map((e) => h("li", { class: e.lvl ? "lvl" : "" }, h("span", { class: "muted small" }, `${dayLabel(e.day)} ${e.day.slice(0, 4)}`), h("span", {}, e.text), e.xp ? h("span", { class: "xp" }, `+${e.xp}`) : null))));
  const gear = h("div", { class: "card" }, h("h3", {}, "Équipement et décors"),
    h("div", { class: "muted small" }, "Les niveaux et certains trophées débloquent des accessoires et des décors pour ton avatar."),
    h("div", { class: "gear-list" }, c.gear.map((g) => h("span", { class: "chip" + (g.unlocked ? " on" : "") }, `${g.unlocked ? "✓" : "🔒"} ${g.label} · ${g.how}`))),
    h("div", { class: "gear-list" }, (c.scenes || []).map((x) => h("span", { class: "chip" + (x.unlocked ? " on" : "") }, `${x.unlocked ? "✓" : "🔒"} Décor ${x.label.toLowerCase()} · ${x.how}`))));
  const sf = h("form", { class: "strength-form" },
    h("select", { name: "exercise", "aria-label": "Exercice" }, [["box_squat", "Box squat"], ["squat", "Squat"], ["trap_bar", "Trap bar"], ["deadlift", "Soulevé de terre"]].map(([v, l]) => h("option", { value: v }, l))),
    h("input", { name: "kg", type: "number", inputmode: "decimal", min: 1, max: 500, step: "0.5", placeholder: "Charge (kg)", required: true }),
    h("input", { name: "reps", type: "number", inputmode: "numeric", min: 1, max: 12, value: 5, "aria-label": "Répétitions", required: true }),
    h("input", { name: "bodyweight", type: "number", inputmode: "decimal", min: 20, max: 200, step: "0.1", placeholder: "Ton poids (kg)", required: true, value: c.strength_log?.[0]?.bodyweight || "" }),
    h("button", { class: "btn small primary", type: "submit" }, "Ajouter"));
  sf.addEventListener("submit", async (e) => {
    e.preventDefault(); const f = new FormData(sf);
    try {
      await api("/api/career/strength", { json: { exercise: f.get("exercise"), kg: +f.get("kg"), reps: +f.get("reps"), bodyweight: +f.get("bodyweight") } });
      toast("Charge enregistrée ✓"); renderCareer();
    } catch (err) { toast(err.message); }
  });
  const strength = h("div", { class: "card" }, h("h3", {}, "Mes charges"),
    h("div", { class: "muted small" }, "Strava n'enregistre pas les charges : note ta meilleure série (ex. 170 kg × 5). Le maximum est estimé (formule d'Epley)."),
    sf, (c.strength_log || []).slice(0, 5).map((s) => h("div", { class: "muted small" }, `${dayLabel(s.day)} · ${s.exercise.replace("_", " ")} ${s.kg} kg × ${s.reps} → max ~${s.one_rm} kg (${(s.one_rm / s.bodyweight).toFixed(2).replace(".", ",")} × ton poids)`)));
  const rules = h("details", { class: "card" }, h("summary", {}, h("b", {}, "Les règles du jeu")),
    h("ul", { class: "small" },
      h("li", {}, "Chaque trophée rapporte de l'XP une seule fois. Une séance du plan validée : +3 XP, +6 pour une séance clé (seuil, fractionné, sortie longue)."),
      h("li", {}, `Niveaux : 50 XP pour le niveau 2, puis 100, 200, 400… (le double à chaque fois), sans niveau maximum : au-delà du 12, une étoile de plus par niveau. Prochains paliers : ${c.next_levels.map((l) => `niv. ${l.level} à ${fr(l.at)} XP`).join(", ")}.`),
      h("li", {}, "Séance bonus facultative faite : +4 XP. Sautée : rien ne se perd."),
      h("li", {}, "Rien n'est gagné en faisant plus que le plan, et la régularité se compte en semaines, pas en jours d'affilée : le repos ne fait jamais perdre."),
      h("li", {}, "Les chronos ne comptent que sur une sortie entière, à plat, de la bonne distance.")));
  fill(root, banners, hero, goals, fams, timeline, gear, strength, rules);
  if (c.first_visit || c.new?.length || c.level_up) api("/api/career/seen", { method: "POST" }).then(() => { state.career.new = []; state.career.level_up = false; paintMeAvatar(); }).catch(() => {});
}

/* éditeur d'avatar */
let avatarDraft = null;
function openAvatarEditor() {
  const c = state.career; if (!c) return;
  avatarDraft = { ...c.avatar, gear: c.avatar.gear === null ? null : [...c.avatar.gear] };
  paintAvatarEditor(); $("#avatar-sheet").showModal();
}
function paintAvatarEditor() {
  const c = state.career, d = avatarDraft;
  fill($("#avatar-preview"), avatarSvg(d, c.level, c.gear, d.scene || c.auto_scene || c.scene, { size: 130 }));
  const swatches = (key, colors) => h("div", { class: "swatches" }, colors.map((col, i) => {
    const b = h("button", { type: "button", class: "sw" + (d[key] === i ? " on" : ""), "aria-label": `${key} ${i + 1}`, onclick: () => { d[key] = i; paintAvatarEditor(); } });
    b.append(sv("svg", { viewBox: "0 0 20 20", width: 26, height: 26 }, sv("circle", { cx: 10, cy: 10, r: 9, fill: col, stroke: "#0003" }))); return b;
  }));
  const row = (label, el) => h("div", { class: "av-row" }, h("div", { class: "label" }, label), el);
  const worn = wornGear(d, c.gear);
  fill($("#avatar-options"),
    row("Peau", swatches("skin", AV.skin)),
    row("Coiffure", h("div", { class: "seg" }, AV.hair.map(([v, l]) => h("button", { type: "button", class: "chip" + (d.hair === v ? " on" : ""), onclick: () => { d.hair = v; paintAvatarEditor(); } }, l)),
      h("button", { type: "button", class: "chip" + (d.beard ? " on" : ""), onclick: () => { d.beard = !d.beard; paintAvatarEditor(); } }, "Barbe"))),
    row("Cheveux", swatches("hair_color", AV.hairColor)),
    row("Maillot", swatches("shirt", AV.shirt)),
    row("Short", swatches("shorts", AV.shorts)),
    row("Chaussures", swatches("shoes", AV.shoes)),
    row("Équipement", h("div", { class: "seg" }, c.gear.map((g) => h("button", {
      type: "button", class: "chip" + (worn.has(g.id) ? " on" : ""), disabled: !g.unlocked,
      onclick: () => { const cur = new Set(wornGear(d, c.gear)); cur.has(g.id) ? cur.delete(g.id) : cur.add(g.id); d.gear = [...cur]; paintAvatarEditor(); } },
      g.unlocked ? g.label : `🔒 ${g.label} (${g.how})`)))),
    row("Décor", h("div", { class: "seg" },
      h("button", { type: "button", class: "chip" + (!d.scene ? " on" : ""), onclick: () => { d.scene = null; paintAvatarEditor(); } }, "Auto (selon le niveau)"),
      (c.scenes || []).map((x) => h("button", { type: "button", class: "chip" + (d.scene === x.id ? " on" : ""), disabled: !x.unlocked,
        onclick: () => { d.scene = x.id; paintAvatarEditor(); } }, x.unlocked ? x.label : `🔒 ${x.label} (${x.how})`)))),
    c.avatar_image?.enabled ? h("div", { class: "muted small" }, `L'image est redessinée au plus une fois toutes les ${c.avatar_image.every_h || 48} h. `
      + (c.avatar_image.next_at ? `Prochaine image possible le ${frDateTime(c.avatar_image.next_at)} : en attendant, tes changements s'affichent sur l'avatar dessiné.` : "")) : null);
}
$("#avatar-cancel").addEventListener("click", () => $("#avatar-sheet").close());
$("#avatar-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  try { const r = await api("/api/career/avatar", { json: avatarDraft }); state.career.avatar = r.avatar; state.career.avatar_set = true; $("#avatar-sheet").close(); toast("Avatar enregistré ✓"); paintMeAvatar(); if (state.view === "carriere") renderCareer(); }
  catch (err) { toast(err.message); }
});

async function boot() {
  try { state.me = await api("/api/me"); } catch (e) { return; }
  $("#login").hidden = true; $("#app").hidden = false;
  header(state.me);
  if (state.me.onboarding) {
    const ob = await api("/api/onboarding").catch(() => ({ step: "connect" }));
    if (ob.step === "connect" || ob.step === "syncing") { tabs(false); state.view = "onboarding"; go("onboarding"); return; }
    tabs(true); state.chatLoaded = false;
    if (location.hash !== "#coach") { location.hash = "#coach"; return; }
    go("coach"); return;
  }
  tabs(true);
  go(location.hash.slice(1) || "aujourdhui");
  loadCareer(true);
}
if ("serviceWorker" in navigator) navigator.serviceWorker.register("/sw.js").catch(() => {});
document.addEventListener("visibilitychange", () => { if (!document.hidden && state.me) { state.chatLoaded = false; go(state.view); } });
boot();
