/* Œil du coach (verrouillé, offert, en cours, rapport) et feuille de retour de séance (avec bilan nutrition). */
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { EyeStatus, Food, NutritionFoods } from "../api/types";
import { onToast } from "../lib/toast";
import { reply, session, stubFetch } from "../test/fakes";
import { EyeCard } from "./EyeCard";
import { FeedbackSheet } from "./FeedbackSheet";
import { fromLog, nutritionTouched } from "./NutritionBlock";

afterEach(() => { vi.unstubAllGlobals(); vi.useRealTimers(); onToast(null); });

const eye = (o: Partial<EyeStatus> = {}): EyeStatus => ({ required_level: 7, level: 4, unlocked: false, free_available: false,
  xp: 1200, xp_required: 3000, progress_pct: 40, cooldown_days: 28, running: false, next_at: null, available: false, ...o });

const report = {
  at: "2026-11-01T10:00:00", day: "2026-11-01", cost_eur: 0.042, latency_s: 38.5, free: true,
  agents: [{ name: "charge", label: "Charge", icon: "📈", tools: [{ name: "load", ms: 1 }], risk_level: "low", veto: false, evidence_ids: [], cost_eur: 0.01, ms: 900 }],
  critic: { verdict: "ok", reasons: [], revised: false },
  report: { summary: "Bonne régularité.", strengths: ["Régularité"], weaknesses: ["Vitesse"], risks: [], nutrition: "Teste 70 g/h.",
    race: "Pars prudemment.", priorities: [{ action: "Une séance de VMA", why: "vitesse peu travaillée", cards: ["kb_vma"] }],
    next_review: "dans 4 semaines", to_measure: ["FC au seuil"] },
} as unknown as EyeStatus["last"];

describe("EyeCard", () => {
  it("ne charge rien tant que la page Progrès n'est pas affichée", () => {
    const calls = stubFetch({ "GET /api/eye": eye() });
    expect(render(<EyeCard active={false} />).container.innerHTML).toBe("");
    expect(calls.length).toBe(0);
  });

  it("verrouillé : progression vers le niveau 7, et l'analyse offerte déjà faite", async () => {
    stubFetch({ "GET /api/eye": eye({ last: report }) });
    const { container } = render(<EyeCard active />);
    await screen.findByText(/Niveau 7 : l'Œil du coach/);
    expect(screen.getByRole("progressbar").getAttribute("aria-valuenow")).toBe("40");
    expect(container.textContent?.replace(/\s/g, " ")).toContain("1 200 / 3 000 XP (40 %)");
    expect(screen.getByText("Ton analyse offerte")).toBeTruthy();
    expect(container.querySelector(".eye-summary")?.textContent).toBe("Bonne régularité.");
  });

  it("analyse offerte : lancement, puis relecture toutes les 3 s pendant l'analyse", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const toasts: (string | null)[] = [];
    onToast((m) => toasts.push(m));
    let st = eye({ free_available: true, available: true });
    const calls = stubFetch({ "GET /api/eye": () => st, "POST /api/eye": () => { st = eye({ free_available: true, running: true }); return { ...st, typing: true }; } });
    render(<EyeCard active />);
    fireEvent.click(await screen.findByText("Lancer mon analyse offerte"));
    await screen.findByText("Analyse en cours…");
    expect(toasts).toContain("L'équipe analyse tes 8 dernières semaines…");
    const before = calls.filter((c) => c.method === "GET").length;
    st = eye({ unlocked: true, level: 8, next_at: "2026-12-01", last: report, history: [{ day: "2026-10-01", summary: "Ancienne" }, { day: "2026-11-01", summary: "Bonne" }] });
    await act(async () => { vi.advanceTimersByTime(3100); });
    await waitFor(() => expect(calls.filter((c) => c.method === "GET").length).toBeGreaterThan(before));
    await screen.findByText(/Prochaine analyse possible le/);
    expect(screen.getByText("Analyses précédentes (1)")).toBeTruthy();
    expect(screen.getByText("Une séance de VMA")).toBeTruthy();
    expect(screen.getByText(/Qui a contribué · critique : validé/)).toBeTruthy();
  });

  it("débloqué sans analyse ; refus du serveur affiché", async () => {
    const toasts: (string | null)[] = [];
    onToast((m) => toasts.push(m));
    stubFetch({ "GET /api/eye": eye({ unlocked: true, level: 9, available: true }), "POST /api/eye": reply(409, { error: "une analyse par mois" }) });
    const { container } = render(<EyeCard active />);
    fireEvent.click(await screen.findByText("Lancer l'analyse"));
    await waitFor(() => expect(toasts).toContain("une analyse par mois"));
    expect(container.textContent).toContain("Une analyse tous les 28 jours.");
  });
});

const food = (o: Pick<Food, "id" | "name" | "family" | "portion" | "carbs_g" | "sodium_mg" | "fluid_ml">): Food =>
  ({ caffeine_mg: 0, fructose: false, digest: "", note: "", ...o }) as Food;
const foods: NutritionFoods = {
  foods: [food({ id: "gel", name: "Gel (classique)", family: "gel", portion: "1 gel", carbs_g: 25, sodium_mg: 50, fluid_ml: 0 }),
    food({ id: "barre", name: "Barre", family: "solide", portion: "1 barre", carbs_g: 30, sodium_mg: 80, fluid_ml: 0 }),
    food({ id: "eau", name: "Eau", family: "boisson", portion: "", carbs_g: 0, sodium_mg: 0, fluid_ml: 500 })],
  families: [{ id: "gel", label: "Gels" }, { id: "solide", label: "Solide" }],
  symptoms: [{ id: "nausee", label: "Nausées" }], digestion: [{ v: 1, label: "parfaite" }, { v: 3, label: "moyenne" }],
  trained_max_g_h: 60, min_duration_min: 90,
};

describe("FeedbackSheet", () => {
  it("valide une séance simple ; prévient le coach dès qu'une note est écrite", async () => {
    const toasts: (string | null)[] = [];
    onToast((m) => toasts.push(m));
    const calls = stubFetch({ "POST /api/sessions/s1/feedback": { feedback: { status: "modified", rpe: 7, note: "" }, messages: [{ role: "coach" }] } });
    const onClose = vi.fn(), onSaved = vi.fn();
    render(<FeedbackSheet session={session()} onClose={onClose} onSaved={onSaved} />);
    expect(document.getElementById("fb-title")?.textContent).toBe("Seuil 3 × 10 min");
    fireEvent.click(screen.getByText("Modifiée"));
    fireEvent.click(screen.getByLabelText("RPE 7"));
    expect(document.getElementById("fb-rpe-val")?.textContent).toBe("7/10 dur");
    fireEvent.click(screen.getByLabelText("RPE 7"));                      // re-toucher : efface
    expect(document.getElementById("fb-rpe-val")?.textContent).toBe("");
    fireEvent.click(screen.getByLabelText("RPE 7"));
    fireEvent.change(screen.getByLabelText("Note pour le coach"), { target: { value: "jambes lourdes" } });
    expect((document.getElementById("fb-notify") as HTMLInputElement).checked).toBe(true);
    fireEvent.submit(document.getElementById("fb-form")!);
    await waitFor(() => expect(onSaved).toHaveBeenCalledWith(true));
    expect(calls[0].body).toEqual({ status: "modified", rpe: 7, note: "jambes lourdes", notify_coach: true });
    expect(onClose).toHaveBeenCalled();
    expect(toasts).toContain("Séance validée, le coach a répondu");
  });

  it("erreur du coach et erreur de l'API", async () => {
    const toasts: (string | null)[] = [];
    onToast((m) => toasts.push(m));
    stubFetch({ "POST /api/sessions/s1/feedback": { feedback: {}, messages: [], coach_error: "quota" } });
    const onSaved = vi.fn();
    const { rerender } = render(<FeedbackSheet session={session({ feedback: { status: "skipped", rpe: null, note: "malade" } })} onClose={() => {}} onSaved={onSaved} />);
    expect(screen.getByText("Sautée").className).toBe("on");
    fireEvent.submit(document.getElementById("fb-form")!);
    await waitFor(() => expect(onSaved).toHaveBeenCalledWith(false));
    expect(toasts.some((t) => t?.includes("n'a pas pu répondre"))).toBe(true);
    stubFetch({ "POST /api/sessions/s1/feedback": reply(422, { error: "statut inconnu" }) });
    fireEvent.submit(document.getElementById("fb-form")!);
    await waitFor(() => expect(toasts).toContain("statut inconnu"));
    rerender(<FeedbackSheet session={null} onClose={() => {}} onSaved={onSaved} />);
    expect(document.getElementById("fb-title")?.textContent).toBe("");
  });

  it("sortie longue : bilan nutrition (digestion obligatoire), envoyé avec la séance", async () => {
    const toasts: (string | null)[] = [];
    onToast((m) => toasts.push(m));
    Element.prototype.scrollIntoView = vi.fn();
    const calls = stubFetch({ "GET /api/nutrition/foods": foods,
      "POST /api/sessions/long/feedback": { feedback: {}, messages: [] }, "POST /api/nutrition": { ok: true } });
    const onSaved = vi.fn();
    render(<FeedbackSheet session={session({ session_id: "long", duration_min: 150, actual_min: 180, nutrition_eligible: true })}
      onClose={() => {}} onSaved={onSaved} />);
    await screen.findByText("Gels");
    expect(screen.getByText("Touche + sur ce que tu as pris.")).toBeTruthy();
    fireEvent.click(screen.getByLabelText("Plus : Gel (classique)"));
    fireEvent.click(screen.getByLabelText("Plus : Gel (classique)"));
    fireEvent.click(screen.getByLabelText("Moins : Gel (classique)"));
    fireEvent.click(screen.getByLabelText("Plus : Flasques d'eau"));
    fireEvent.click(screen.getByText("Solide"));
    fireEvent.click(screen.getByLabelText("Plus : Barre"));
    fireEvent.click(screen.getByText("mon produit a un autre grammage"));
    const own = document.querySelector(".ni-own input") as HTMLInputElement;
    fireEvent.change(own, { target: { value: "40" } });
    fireEvent.blur(own);
    expect(screen.getByText("mon produit : 40 g")).toBeTruthy();
    fireEvent.change(screen.getByLabelText("Eau en ml"), { target: { value: "750" } });
    fireEvent.click(screen.getByText("Nausées"));
    fireEvent.change(screen.getByLabelText("Commentaire nutrition"), { target: { value: "trop sucré" } });
    expect(document.querySelector(".nutri-chosen")?.textContent).toContain("Gel ×1 · Barre ×1");
    fireEvent.submit(document.getElementById("fb-form")!);
    expect(toasts).toContain("Nutrition : indique ta digestion (1 à 5).");
    fireEvent.click(screen.getByText("moyenne"));
    fireEvent.submit(document.getElementById("fb-form")!);
    await waitFor(() => expect(onSaved).toHaveBeenCalledWith(true));
    const n = calls.find((c) => c.path === "/api/nutrition")!.body as Record<string, unknown>;
    expect(n).toMatchObject({ session_id: "long", water_ml: 750, digestion: 3, symptoms: ["nausee"], comment: "trop sucré" });
    expect(n.items).toEqual([{ id: "gel", qty: 1, carbs_g: null }, { id: "barre", qty: 1, carbs_g: 40 }]);
    expect(calls.find((c) => c.path.endsWith("/feedback"))!.body).toMatchObject({ notify_coach: false });
  });

  it("bilan existant relu ; état « touché »", () => {
    const v = fromLog({ date: "", session_id: null, activity_id: null, duration_h: 2, items: [{ id: "gel", qty: 2, carbs_g: null }],
      water_ml: 500, carbs_g_h: 25, sodium_mg_h: 0, fluid_ml_h: 0, digestion: 2, symptoms: [], comment: "" });
    expect(v.picks.gel.qty).toBe(2);
    expect(nutritionTouched(fromLog(null))).toBe(false);
    expect(nutritionTouched(v)).toBe(true);
  });
});
