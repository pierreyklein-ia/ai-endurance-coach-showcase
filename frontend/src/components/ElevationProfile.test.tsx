/* Profil d'altitude du simulateur : survol (heure de passage), fiche d'un point (clavier ou toucher). */
import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { RaceLeg, SimPoint } from "../api/types";
import { ElevationProfile } from "./ElevationProfile";

const arr = (s: number, clock: string) => ({ ambitieux: { s: s * 0.9, clock }, cible: { s, clock }, prudent: { s: s * 1.1, clock } });
const pt = (o: Partial<SimPoint>): SimPoint => ({ name: "Départ", km: 0, km_official: 0, alt: 500, alt_approx: false, kind: "depart",
  services: [], note: "", estimate: false, snapped: false, stop_min: 0, arrival: arr(0, "06:00"), cutoff: null, ...o });
const points = [
  pt({}),
  pt({ name: "Puy de Dôme", km: 37, km_official: 37, alt: 1465, kind: "ravito", stop_min: 5, note: "sommet", alt_approx: true,
    arrival: arr(5 * 3600, "11:00"), cutoff: { limit_s: 7 * 3600, clock: "13:00", label: "", approx: true, edition: 2025,
      margin_min: { ambitieux: 150, cible: 120, prudent: 60 }, status: "ok" } }),
  pt({ name: "Fontaine", km: 50, km_official: 50, alt: 900, kind: "eau", arrival: arr(7 * 3600, "13:00") }),
  pt({ name: "Arrivée", km: 85, km_official: 85, alt: 510, kind: "arrivee", arrival: arr(9 * 3600, "15:00") }),
];
const leg = (o: Partial<RaceLeg>): RaceLeg => ({ from: "Départ", to: "Puy de Dôme", km0: 0, km1: 37, km: 37, duration_s: 18000, start_s: 0,
  carbs_g: 300, carbs_g_h: 60, fluid_ml: 2500, flasks: 2, water_flasks: 1, sodium_mg: 1500, caffeine_mg: 0, at_aid: [], carry: [],
  at_aid_label: "", carry_label: "", late: false, notes: [], line: "", ...o });
const legs = [leg({}), leg({ from: "Puy de Dôme", to: "Fontaine", at_aid: [{ id: "gel" } as never], at_aid_label: "2 gels", carry_label: "1 barre" })];

/** jsdom n'a pas PointerEvent : une version minimale (clientX, pointerType) suffit à React. */
class FakePointerEvent extends MouseEvent {
  pointerType: string;
  constructor(type: string, init: PointerEventInit = {}) { super(type, init); this.pointerType = init.pointerType || "mouse"; }
}

beforeEach(() => vi.stubGlobal("PointerEvent", FakePointerEvent));
afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); });

describe("profil d'altitude", () => {
  it("rien sans profil", () => {
    expect(render(<ElevationProfile profile={[[0, 1]]} points={points} scenario="cible" approx={false} />).container.innerHTML).toBe("");
  });

  it("libellé accessible, survol à la souris puis sortie", () => {
    vi.spyOn(SVGElement.prototype, "getBoundingClientRect").mockReturnValue({ left: 0, width: 600, top: 0, height: 200 } as DOMRect);
    const { container } = render(<ElevationProfile profile={[[0, 500], [37, 1465], [50, 900], [85, 510]]} points={points}
      scenario="cible" approx legs={legs} />);
    const svg = screen.getByRole("img");
    expect(svg.getAttribute("aria-label")).toContain("Profil d'altitude approximatif : 2 points de passage, point culminant Puy de Dôme (1465 m, km 37)");
    expect(container.textContent).toContain("profil approximatif");
    fireEvent.pointerMove(svg, { clientX: 300 });
    expect(container.querySelector(".elev-readout")?.textContent).toMatch(/^km [\d,]+ · \d+ m · \d\d:\d\d/);
    expect(container.querySelector(".elev-cursor")).not.toBeNull();
    fireEvent.pointerLeave(svg, { pointerType: "touch" });
    expect(container.querySelector(".elev-cursor")).not.toBeNull();      // au doigt, le curseur reste
    fireEvent.pointerLeave(svg, { pointerType: "mouse" });
    expect(container.querySelector(".elev-cursor")).toBeNull();
  });

  it("fiche d'un ravito : heures des 3 scénarios, barrière, trajet, ravito ; clavier et fermeture", () => {
    render(<ElevationProfile profile={[[0, 500], [37, 1465], [85, 510]]} points={points} scenario="prudent" approx={false} legs={legs} />);
    const mark = screen.getByRole("button", { name: /Puy de Dôme, km 37/ });
    fireEvent.click(mark);
    const info = screen.getByRole("region", { name: "Fiche : Puy de Dôme" });
    expect(info.textContent).toContain("km 37 · 1465 m (≈) · arrêt prévu 5 min · sommet");
    expect(info.textContent).toContain("Barrière 13:00 (≈, édition 2025) : marge +1 h");
    expect(info.textContent).toContain("Pour y arriver : 5 h 00 depuis le départ, 300 g de glucides.");
    expect(info.textContent).toContain("Au ravito : 2 gels.");
    expect(info.textContent).toContain("Pour repartir : 1 barre, 2 × 500 ml, jusqu'à Fontaine");
    fireEvent.click(screen.getByLabelText("Fermer la fiche"));
    expect(screen.queryByRole("region")).toBeNull();
    fireEvent.keyDown(screen.getByRole("button", { name: /Fontaine/ }), { key: "Enter" });
    expect(screen.getByRole("region", { name: "Fiche : Fontaine" }).textContent).toContain("Pour y arriver");
    fireEvent.keyDown(screen.getByRole("button", { name: /Fontaine/ }), { key: " " });
    expect(screen.queryByRole("region")).toBeNull();
    fireEvent.keyDown(screen.getByRole("button", { name: /Fontaine/ }), { key: "a" });
    expect(screen.queryByRole("region")).toBeNull();
  });
});
