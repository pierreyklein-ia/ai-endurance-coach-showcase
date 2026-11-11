/* Cartes : objectifs, barre du haut (anneau de carrière), volume maximum, petits composants. */
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { Activity, GoalItem, WeekMaxView } from "../api/types";
import { HINT_KEY } from "../lib/careerUi";
import { onToast, toast } from "../lib/toast";
import { career, reply, stubFetch } from "../test/fakes";
import { ActRow } from "./ActRow";
import { GoalsCard } from "./GoalsCard";
import { Medal } from "./Medal";
import { Metric } from "./Metric";
import { Tabs } from "./Tabs";
import { Toast } from "./Toast";
import { TopBar } from "./TopBar";
import { WeekMaxCard } from "./WeekMaxCard";

afterEach(() => { vi.unstubAllGlobals(); vi.useRealTimers(); onToast(null); });

const goal = (o: Partial<GoalItem>): GoalItem => ({ role: "A", label: "", name: "VVX 85 km", date: "2027-05-07", year: 2027,
  days_left: 180, km: 85, dplus: 2750, kind: "trail", note: "", done: false, ...o });

describe("GoalsCard", () => {
  it("rien sans objectif", () => {
    expect(render(<GoalsCard goals={[]} />).container.innerHTML).toBe("");
    expect(render(<GoalsCard goals={undefined} />).container.innerHTML).toBe("");
  });

  it("principal avec estimation, secondaires et projets", () => {
    const { container } = render(<GoalsCard goals={[
      goal({ estimate: [50000, 54000, 59000] }),
      goal({ role: "B", name: "SaintéLyon", date: "2026-11-28", days_left: 23, estimate: [30000, 32000, 34000] }),
      goal({ role: "B", name: "Trail d'hiver", date: "2027-01-10", days_left: 66 }),
      goal({ role: "C", name: "UTMB un jour", date: null, year: null, days_left: null, km: null, dplus: null }),
    ]} />);
    expect(container.querySelector(".goal-main h2")?.textContent).toBe("VVX 85 km");
    expect(container.querySelector(".goal-est")?.textContent).toContain("entre");
    expect(screen.getByText("Simuler ma course").getAttribute("href")).toBe("#course");
    expect(screen.getByText("Objectifs secondaires")).toBeTruthy();
    expect(screen.getByText("Projet")).toBeTruthy();
    expect(container.querySelectorAll(".goal-line").length).toBe(3);
  });

  it("objectif fait : pas de simulateur, une seule ligne secondaire au singulier", () => {
    render(<GoalsCard goals={[goal({ done: true, days_left: -3 }), goal({ role: "B", name: "10 km", kind: "route", done: true })]} />);
    expect(screen.queryByText("Simuler ma course")).toBeNull();
    expect(screen.getByText("Objectif secondaire")).toBeTruthy();
  });
});

describe("TopBar", () => {
  beforeEach(() => localStorage.clear());

  it("accueil d'un nouvel athlète, sans avatar", () => {
    const { container } = render(<TopBar info={{ race_name: null, race_date: null, days_to_race: null, first_name: "Alex" }} career={null} onboarding />);
    expect(container.querySelector("#race-name")?.textContent).toBe("Bienvenue Alex");
    expect(container.querySelector("#race-sub")?.textContent).toBe("Configurons ton coach");
    expect((container.querySelector("#me-avatar") as HTMLElement).hidden).toBe(true);
  });

  it("compte à rebours et jour J", () => {
    const info = { race_name: "VVX", race_date: "2027-05-07", days_to_race: 12, first_name: "" };
    const { container, rerender } = render(<TopBar info={info} career={null} onboarding={false} />);
    expect(container.querySelector("#race-countdown")?.textContent).toBe("J-12");
    rerender(<TopBar info={{ ...info, days_to_race: 0 }} career={null} onboarding={false} />);
    expect(container.querySelector("#race-countdown")?.textContent).toBe("Jour J");
    rerender(<TopBar info={{ ...info, days_to_race: -2, race_date: null }} career={null} onboarding={false} />);
    expect(container.querySelector("#race-countdown")?.textContent).toBe("");
    rerender(<TopBar info={null} career={null} onboarding={false} />);
    expect(container.querySelector("#race-name")?.textContent).toBe("");
  });

  it("anneau d'XP, pastille de niveau, point de nouveauté, bulle d'aide une seule fois", () => {
    vi.useFakeTimers();
    const c = career({ pct: 25, level_up: true });
    const { container, unmount } = render(<TopBar info={null} career={c} onboarding={false} />);
    const btn = container.querySelector("#me-avatar") as HTMLAnchorElement;
    expect(btn.hidden).toBe(false);
    expect(btn.className).toContain("ping");
    expect(btn.getAttribute("aria-label")).toContain("Nouveau niveau");
    expect(container.querySelector(".me-pill")?.textContent).toBe("Niv. 7");
    const fg = container.querySelector(".ring-fg")!;
    expect(Number(fg.getAttribute("stroke-dashoffset"))).toBeCloseTo(Number(fg.getAttribute("stroke-dasharray")) * 0.75, 1);
    act(() => { vi.advanceTimersByTime(700); });
    expect(screen.getByRole("note").textContent).toContain("Touche ton avatar");
    fireEvent.click(screen.getByLabelText("Fermer l'aide"));
    expect(screen.queryByRole("note")).toBeNull();
    expect(localStorage.getItem(HINT_KEY)).toBe("1");
    unmount();
    render(<TopBar info={null} career={career()} onboarding={false} />);
    act(() => { vi.advanceTimersByTime(700); });
    expect(screen.queryByRole("note")).toBeNull();
  });
});

const wm = (o: Partial<WeekMaxView> = {}): WeekMaxView => ({ minutes: 390, km: null, dplus: null, inferred: true,
  text: "6 h 30", best_4w_min: 300, has_profile: true, ...o });

describe("WeekMaxCard", () => {
  it("ne charge rien hors de la page, rien sans profil", async () => {
    const calls = stubFetch({ "GET /api/week-max": wm({ has_profile: false }) });
    const { container, rerender } = render(<WeekMaxCard active={false} nonce={0} />);
    expect(calls.length).toBe(0);
    rerender(<WeekMaxCard active nonce={0} />);
    await waitFor(() => expect(calls.length).toBe(1));
    expect(container.innerHTML).toBe("");
  });

  it("affiche le plafond estimé, refuse une valeur illisible puis enregistre et propose le plan ajusté", async () => {
    const toasts: (string | null)[] = [];
    onToast((m) => toasts.push(m));
    let current = wm();
    const calls = stubFetch({
      "GET /api/week-max": () => current,
      "POST /api/week-max": (body: unknown) => { current = wm({ minutes: 300, km: 50, inferred: false, text: "5 h · 50 km" }); return { ok: true, text: "5 h · 50 km", replan: !!body, messages: [] }; },
    });
    const onChat = vi.fn();
    const { container } = render(<WeekMaxCard active nonce={1} onChat={onChat} />);
    await screen.findByText("6 h 30");
    expect(container.textContent).toContain("estimé d'après ton historique");
    expect(container.textContent).toContain("Ta plus grosse période récente : 5 h par semaine");
    expect((screen.getByLabelText("Temps maximum par semaine (heures)") as HTMLInputElement).value).toBe("6h30");
    fireEvent.change(screen.getByLabelText("Distance maximum par semaine (km)"), { target: { value: "beaucoup" } });
    fireEvent.submit(container.querySelector("form")!);
    expect(screen.getByRole("alert").textContent).toContain("Je n'ai pas compris");
    expect(calls.filter((c) => c.method === "POST").length).toBe(0);
    fireEvent.change(screen.getByLabelText("Temps maximum par semaine (heures)"), { target: { value: "5h" } });
    fireEvent.change(screen.getByLabelText("Distance maximum par semaine (km)"), { target: { value: "50" } });
    fireEvent.change(screen.getByLabelText("Dénivelé positif maximum par semaine (m)"), { target: { value: "1200,4" } });
    fireEvent.submit(container.querySelector("form")!);
    await waitFor(() => expect(onChat).toHaveBeenCalled());
    expect(calls.find((c) => c.method === "POST")?.body).toEqual({ hours: 5, km: 50, dplus: 1200 });
    expect(toasts.some((t) => t?.includes("plan ajusté"))).toBe(true);
    await screen.findByText("5 h · 50 km");
  });

  it("enregistrement sans plafond, et erreur du serveur", async () => {
    const toasts: (string | null)[] = [];
    onToast((m) => toasts.push(m));
    stubFetch({ "GET /api/week-max": wm({ minutes: null, text: "", inferred: false, best_4w_min: null }),
      "POST /api/week-max": { ok: true, text: "", replan: false, messages: [] } });
    const { container } = render(<WeekMaxCard active nonce={1} />);
    await screen.findByText("Pas de plafond");
    fireEvent.submit(container.querySelector("form")!);
    await waitFor(() => expect(toasts).toContain("Enregistré ✓ Pas de plafond"));
    stubFetch({ "GET /api/week-max": wm(), "POST /api/week-max": reply(500, { error: "base verrouillée" }) });
    fireEvent.submit(container.querySelector("form")!);
    await waitFor(() => expect(toasts).toContain("base verrouillée"));
  });
});

describe("petits composants", () => {
  it("médaille, indicateur, onglets", () => {
    expect(render(<Medal tier={2} done />).container.querySelector("svg.medal:not(.off)")).not.toBeNull();
    expect(render(<Medal tier={1} done={false} />).container.querySelector("svg.off")).not.toBeNull();
    const m = render(<Metric k="ACWR" v={null} hint="ratio" cls="warn" />).container;
    expect(m.querySelector(".v")?.textContent).toBe("n/d");
    expect(m.querySelector(".h")?.textContent).toBe("ratio");
    const t = render(<Tabs view="coach" hidden={false} />).container;
    expect(t.querySelectorAll("a").length).toBe(6);
    expect(t.querySelector("a.on")?.getAttribute("href")).toBe("#coach");
  });

  it("message éphémère (toast)", () => {
    vi.useFakeTimers();
    const { container } = render(<Toast />);
    const el = container.querySelector("#toast") as HTMLElement;
    expect(el.hidden).toBe(true);
    act(() => toast("Séance validée ✓", 1000));
    expect(el.hidden).toBe(false);
    expect(el.textContent).toBe("Séance validée ✓");
    act(() => { vi.advanceTimersByTime(1100); });
    expect(el.hidden).toBe(true);
  });

  it("ligne d'activité dépliable (allure, montre oubliée, sortie suspecte)", () => {
    const a: Activity = { day: "2026-11-03", start: "2026-11-03T07:00:00", sport: "Course", name: "Footing", minutes: 50,
      km: 10, dplus: 40, avg_hr: 142, load: 48, load_source: "hrTSS", suspect: true, elapsed_min: 1440 };
    const { container } = render(<ActRow a={a} />);
    expect(container.textContent).toContain("charge 48");
    fireEvent.click(container.querySelector(".act-btn")!);
    const d = container.querySelector(".act-detail")!;
    expect(d.textContent).toContain("allure 5:00/km");
    expect(d.textContent).toContain("la montre a tourné 24 h");
    expect(d.textContent).toContain("Durée invraisemblable");
    expect(d.textContent).toContain("fréquence cardiaque (fichier)");
    fireEvent.click(screen.getByText("Fermer"));
    expect(container.querySelector(".act-detail")).toBeNull();
    const v = render(<ActRow a={{ ...a, sport: "Vélo", name: "", km: 0, dplus: 0, avg_hr: null, suspect: false, elapsed_min: undefined,
      load_source: "autre" }} />).container;
    fireEvent.click(v.querySelector(".act-btn")!);
    expect(v.querySelector(".act-detail")?.textContent).not.toContain("allure");
  });
});
