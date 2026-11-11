import type { Career, Trophy } from "../api/types";
import { careerLabel, hasNews, HINT_KEY, hintSeen, markHintSeen, nextTrophies, ringDash, xpProgress } from "./careerUi";
import { fmtHours, hoursField, parseHours, parseNum, weekMaxError } from "./weekMax";

const tro = (o: Partial<Trophy>): Trophy => ({ id: "x", family: "endurance", label: "x", desc: "", xp: 10, tier: 1, day: null, ...o });

describe("carrière visible (v0.49)", () => {
  it("calcule la progression et le libellé accessible", () => {
    const c = { level: 7, xp: 2602, next_at: 3000, pct: 64, new: [], level_up: false };
    expect(xpProgress(c)).toEqual({ pct: 64, toNext: 398, next: 8 });
    expect(careerLabel(c)).toBe("Ma carrière : niveau 7, 398 XP avant le niveau 8.");
    expect(careerLabel({ ...c, level_up: true })).toContain("Nouveau niveau");
    expect(careerLabel({ ...c, new: [tro({}), tro({})] })).toContain("2 nouveaux trophées");
    expect(xpProgress({ ...c, pct: 140 }).pct).toBe(100);
    expect(xpProgress({ ...c, pct: Number.NaN }).pct).toBe(0);
    expect(xpProgress({ ...c, xp: 3100 }).toNext).toBe(0);
  });

  it("dessine l'anneau d'XP", () => {
    const r = ringDash(25, 10);
    expect(r.circ).toBeCloseTo(62.83, 1);
    expect(r.offset).toBeCloseTo(47.12, 1);
    expect(ringDash(0, 10).offset).toBeCloseTo(r.circ);
    expect(ringDash(120, 10).offset).toBe(0);
  });

  it("pastille seulement s'il y a du nouveau", () => {
    expect(hasNews(null)).toBe(false);
    expect(hasNews({ new: [], level_up: false })).toBe(false);
    expect(hasNews({ new: [tro({})], level_up: false })).toBe(true);
    expect(hasNews({ new: undefined, level_up: true })).toBe(true);
  });

  it("propose les prochains trophées à portée, dans l'ordre de sa discipline", () => {
    const c = {
      discipline: "trail", families: { endurance: "E", jalons: "J", vitesse: "V", evenements: "Ev" } as Record<string, string>,
      trophies: [tro({ id: "e2", tier: 2 }), tro({ id: "e1", tier: 1, xp: 20 }), tro({ id: "e0", tier: 1, day: "2026-01-01" }),
        tro({ id: "j1", family: "jalons" }), tro({ id: "v1", family: "vitesse" }), tro({ id: "ev", family: "evenements" })],
    } as Pick<Career, "discipline" | "families" | "trophies">;
    expect(nextTrophies(c, 2).map((t) => t.id)).toEqual(["e1", "j1"]);
    expect(nextTrophies(c).map((t) => t.id)).toEqual(["e1", "j1", "v1", "ev"]);
    expect(nextTrophies({ ...c, discipline: "route" }, 1)[0].id).toBe("v1");
  });

  it("ne montre la bulle d'aide qu'une fois", () => {
    localStorage.removeItem(HINT_KEY);
    expect(hintSeen()).toBe(false);
    markHintSeen();
    expect(hintSeen()).toBe(true);
  });
});

describe("volume maximum (v0.49)", () => {
  it("lit les heures comme on les écrit", () => {
    expect(parseHours("6")).toBe(6);
    expect(parseHours("6,5")).toBe(6.5);
    expect(parseHours("6h30")).toBe(6.5);
    expect(parseHours("6 h")).toBe(6);
    expect(parseHours("90 min")).toBe(1.5);
    expect(parseHours("")).toBeNull();
    expect(parseHours("beaucoup")).toBeNaN();
    expect(parseNum("1 500")).toBe(1500);
    expect(parseNum("")).toBeNull();
    expect(parseNum("-3")).toBeNaN();
  });

  it("affiche et contrôle", () => {
    expect(fmtHours(390)).toBe("6 h 30");
    expect(fmtHours(360)).toBe("6 h");
    expect(fmtHours(45)).toBe("45 min");
    expect(fmtHours(null)).toBe("");
    expect(hoursField(390)).toBe("6h30");
    expect(hoursField(360)).toBe("6h");
    expect(weekMaxError({ hours: 6, km: 50, dplus: 1500 })).toBeNull();
    expect(weekMaxError({ hours: null, km: null, dplus: null })).toBeNull();
    expect(weekMaxError({ hours: Number.NaN, km: null, dplus: null })).toMatch(/compris/);
    expect(weekMaxError({ hours: 60, km: null, dplus: null })).toMatch(/40 h/);
    expect(weekMaxError({ hours: null, km: 2, dplus: null })).toMatch(/km/);
  });
});
