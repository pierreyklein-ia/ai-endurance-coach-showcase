import { clockAdd, elapsedAtKm, fmtHM, fmtLeg, fmtMargin, fmtPaceKm, parseTarget, profileGeom, profileMarks, shareText, targetText } from "./race";
import type { SimPoint } from "../api/types";

describe("simulateur de course", () => {
  it("formate les durées, allures et marges", () => {
    expect(fmtHM(33300)).toBe("9 h 15");
    expect(fmtHM(35399.9)).toBe("9 h 50");
    expect(fmtLeg(1920)).toBe("32 min");
    expect(fmtLeg(3900)).toBe("1 h 05");
    expect(fmtPaceKm(372)).toBe("6:12/km");
    expect(fmtPaceKm(null)).toBe("—");
    expect(fmtMargin(182)).toBe("+3 h 02");
    expect(fmtMargin(45)).toBe("+45 min");
    expect(fmtMargin(-12)).toBe("−12 min");
  });

  it("lit le temps visé", () => {
    expect(parseTarget("9:00")).toBe(540);
    expect(parseTarget("8h45")).toBe(525);
    expect(parseTarget(" 9 h ")).toBe(540);
    expect(parseTarget("555")).toBe(555);
    expect(parseTarget("9:75")).toBeNull();
    expect(parseTarget("demain")).toBeNull();
    expect(parseTarget("0:10")).toBeNull();
    expect(targetText(555)).toBe("9:15");
    expect(targetText(540)).toBe("9:00");
  });

  it("construit le profil d'altitude dans le cadre", () => {
    const g = profileGeom([[0, 510], [37, 1465], [85, 510]]);
    expect(g.line.startsWith("M40.0 ")).toBe(true);
    expect(g.x(85)).toBeCloseTo(632);
    expect(g.y(1465)).toBeGreaterThanOrEqual(g.T);
    expect(g.y(510)).toBeLessThanOrEqual(g.H - g.B);
    expect(g.area.endsWith("Z")).toBe(true);
    expect(g.yTicks.every((v) => v % 250 === 0 || v % 100 === 0)).toBe(true);
    expect(g.xTicks).toEqual([0, 10, 20, 30, 40, 50, 60, 70, 80]);
    expect(g.altAt(18.5)).toBeCloseTo(510 + (18.5 / 37) * 955);
  });

  it("marque les ravitos et les barrières, pas le départ", () => {
    const p = (kind: SimPoint["kind"], cutoff = false) => ({ kind, cutoff: cutoff ? {} : null }) as unknown as SimPoint;
    expect(profileMarks([p("depart"), p("ravito"), p("eau"), p("repere"), p("repere", true), p("arrivee", true)]).length).toBe(3);
    expect(shareText("VVX", "Arrivée 15:15", ["a", "b"])).toBe("VVX\nArrivée 15:15\n\na\nb");
  });

  it("donne l'heure de passage n'importe où sur le profil (survol)", () => {
    expect(clockAdd("06:00", 4 * 3600 + 2 * 60)).toBe("10:02");
    expect(clockAdd("22:00", 3 * 3600)).toBe("01:00");
    const a = (s: number) => ({ cible: { s, clock: "" }, ambitieux: { s, clock: "" }, prudent: { s, clock: "" } });
    const pts = [
      { name: "Départ", km: 0, stop_min: 0, arrival: a(0) },
      { name: "Chut", km: 14, stop_min: 2, arrival: a(5400) },
      { name: "Arrivée", km: 28, stop_min: 0, arrival: a(11520) },
    ] as unknown as SimPoint[];
    expect(elapsedAtKm(pts, "cible", 7)).toBe(2700);
    expect(elapsedAtKm(pts, "cible", 14)).toBe(5400);
    expect(elapsedAtKm(pts, "cible", 21)).toBe(5520 + 3000);   // reparti après 2 min d'arrêt
    expect(elapsedAtKm(pts, "cible", 99)).toBe(11520);
    expect(profileGeom([[0, 500], [80, 600]]).kmAt(-50)).toBe(0);
  });
});
