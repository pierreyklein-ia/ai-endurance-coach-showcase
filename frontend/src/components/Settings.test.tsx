/* Réglages v0.50 : « Mes zones », « Ma semaine », cible FC sur la carte de séance ; récap (repli sans récap récent). */
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { SessionTarget, WeekStartView, ZonesView, ZoneValue } from "../api/types";
import { recapToShow } from "../lib/recap";
import { onToast } from "../lib/toast";
import { reply, session, stubFetch } from "../test/fakes";
import { SessionCard } from "./SessionCard";
import { WeekStartCard } from "./WeekStartCard";
import { ZonesCard } from "./ZonesCard";

afterEach(() => { vi.unstubAllGlobals(); onToast(null); });

const zv = (value: number, o: Partial<ZoneValue> = {}): ZoneValue =>
  ({ value, source: "estimée", source_id: "estimee", override: null, auto: value, auto_source: "estimée", ...o });
const target: SessionTarget = { zone: "Z4", hr: [165, 172], hr_text: "165-172 bpm", pace: "4:10/km", rpe: "7/10", lead: "hr",
  short_reps: false, note: "Les 2 premières minutes, la FC monte : fie-toi à l'allure.", text: "Cible : FC 165-172 bpm (Z4)" };

const zones = (o: Partial<ZonesView> = {}): ZonesView => ({
  hr_max: zv(190, { source_id: "strava", source: "Strava" }), hr_rest: zv(48), lthr: zv(172, { source_id: "saisie", source: "saisie", override: 172 }),
  zones: [{ zone: "Z1", label: "Récupération", lo: null, hi: 140, pct: "< 81 %" }, { zone: "Z5", label: "VMA", lo: 180, hi: null, pct: "> 105 %" }],
  paces: [{ key: "seuil", label: "Seuil", value: "4:10/km" }],
  targets: [{ ...target, intensity: "threshold", label: "Seuil" }],
  strava: { connected: true, api: true, scope_ok: true, needs_reconnect: false, zones: [{ min: 0, max: 140 }, { min: 180, max: -1 }], at: null, custom: true },
  model: "Friel", ...o });

describe("Mes zones", () => {
  it("rien hors de la page ; valeurs, sources, zones, allures, cibles, zones Strava", async () => {
    const calls = stubFetch({ "GET /api/zones": zones() });
    const { container, rerender } = render(<ZonesCard active={false} nonce={0} />);
    expect(calls.length).toBe(0);
    rerender(<ZonesCard active nonce={0} />);
    await screen.findByText("Mes zones");
    expect(container.querySelectorAll(".zone-val").length).toBe(3);
    expect(container.querySelectorAll(".zones-table tr").length).toBe(2);
    expect(container.textContent).toContain("modèle Friel");
    expect(container.querySelector(".pace-chip")?.textContent).toContain("4:10/km");
    expect(container.textContent).toContain("Zones Strava (personnalisées) : 0-140 · 180+");
    expect((screen.getByLabelText(/au seuil lactique/) as HTMLInputElement).value).toBe("172");
  });

  it("refuse une saisie incohérente, enregistre, affiche l'erreur du serveur", async () => {
    const toasts: (string | null)[] = [];
    onToast((m) => toasts.push(m));
    const calls = stubFetch({ "GET /api/zones": zones({ paces: [] }), "POST /api/zones": zones({ hr_max: zv(195, { override: 195 }) }) });
    const { container } = render(<ZonesCard active nonce={1} />);
    await screen.findByText(/pas encore de record/);
    const max = screen.getByLabelText(/maximale/);
    fireEvent.change(max, { target: { value: "300" } });
    fireEvent.submit(container.querySelector("form")!);
    expect(screen.getByRole("alert").textContent).toContain("entre 120 et 230");
    fireEvent.change(max, { target: { value: "195" } });
    fireEvent.submit(container.querySelector("form")!);
    await waitFor(() => expect(toasts.some((t) => t?.startsWith("Zones enregistrées"))).toBe(true));
    expect(calls.find((c) => c.method === "POST")?.body).toMatchObject({ hr_max: 195 });
    stubFetch({ "POST /api/zones": reply(422, { error: "valeur refusée" }) });
    fireEvent.submit(container.querySelector("form")!);
    await screen.findByText("valeur refusée");
  });

  it("autorisation Strava manquante : reconnexion", async () => {
    const toasts: (string | null)[] = [];
    onToast((m) => toasts.push(m));
    const calls = stubFetch({ "GET /api/zones": zones({ strava: { connected: true, api: true, scope_ok: false, needs_reconnect: true, zones: null, at: null, custom: null } }),
      "POST /api/strava/connect": { status: "connected" } });
    render(<ZonesCard active nonce={1} />);
    fireEvent.click(await screen.findByText("Reconnecter Strava"));
    await waitFor(() => expect(toasts).toContain("Strava connecté ✓"));
    expect(calls.at(-1)?.body).toMatchObject({ force: true });
    stubFetch({ "POST /api/strava/connect": reply(500, { error: "Strava indisponible" }) });
    fireEvent.click(screen.getByText("Reconnecter Strava"));
    await waitFor(() => expect(toasts).toContain("Strava indisponible"));
  });
});

const ws = (day: number): WeekStartView => {
  const days = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"];
  return { day, label: days[day], end_label: days[(day + 6) % 7], days };
};

describe("Ma semaine", () => {
  it("change le premier jour de la semaine ; erreur affichée", async () => {
    const toasts: (string | null)[] = [];
    onToast((m) => toasts.push(m));
    const calls = stubFetch({ "GET /api/week-start": ws(0), "POST /api/week-start": (b: unknown) => ws((b as { day: number }).day) });
    const { container, rerender } = render(<WeekStartCard active={false} nonce={0} />);
    expect(container.innerHTML).toBe("");
    rerender(<WeekStartCard active nonce={0} />);
    const sel = await screen.findByLabelText("Premier jour de la semaine");
    expect(container.textContent).toContain("Semaine du lundi au dimanche");
    fireEvent.change(sel, { target: { value: "6" } });
    await waitFor(() => expect(toasts).toContain("Ta semaine va du dimanche au samedi ✓"));
    expect(calls.at(-1)?.body).toEqual({ day: 6 });
    stubFetch({ "POST /api/week-start": reply(422, { error: "jour invalide" }) });
    fireEvent.change(sel, { target: { value: "2" } });
    await waitFor(() => expect(toasts).toContain("jour invalide"));
  });
});

describe("cible de la séance", () => {
  it("zone, ligne de cible et note en version complète ; pas de note en version courte", () => {
    const { container } = render(<SessionCard s={session({ target })} onFeedback={() => {}} today="2026-11-05" />);
    expect(container.querySelector(".session-target .tgt-zone")?.textContent).toBe("Z4");
    expect(container.querySelector(".tgt-note")?.textContent).toContain("fie-toi à l'allure");
    const short = render(<SessionCard s={session({ target })} full={false} onFeedback={() => {}} />).container;
    expect(short.querySelector(".tgt-note")).toBeNull();
  });

  it("récap : rien à montrer sans récap ni aperçu", () => {
    expect(recapToShow({ last: null, preview: null }, "2026-11-09")).toBeNull();
  });
});
