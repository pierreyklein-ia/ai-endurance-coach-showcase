import { describe, expect, it } from "vitest";
import type { SessionTarget, ZoneValue } from "../api/types";
import { parseBpm, sourceChip, targetLine, zoneRange, zonesError } from "./zones";

const t = (o: Partial<SessionTarget>): SessionTarget => ({ zone: "Z4", hr: [162, 170], hr_text: "162-170 bpm", pace: "4:00/km",
  rpe: "7/10", lead: "hr", short_reps: false, note: "", text: "", ...o });

describe("FC cible", () => {
  it("met la FC d'abord, l'allure ensuite", () => {
    expect(targetLine(t({}))).toBe("FC 162-170 bpm · 4:00/km");
    expect(targetLine(t({ pace: null }))).toBe("FC 162-170 bpm");
  });
  it("fractions courtes : l'allure et l'effort d'abord", () => {
    expect(targetLine(t({ lead: "pace", pace: "3:18/km", rpe: "9/10" }))).toBe("3:18/km · effort 9/10 · FC 162-170 bpm en fin de bloc");
  });
  it("côtes : l'effort d'abord", () => {
    expect(targetLine(t({ lead: "effort", pace: null, rpe: "8/10" }))).toBe("effort 8/10 · FC 162-170 bpm");
    expect(targetLine(null)).toBe("");
  });
});

describe("Mes zones", () => {
  it("lit les bpm", () => {
    expect(parseBpm(" 172 ")).toBe(172);
    expect(parseBpm("172 bpm")).toBe(172);
    expect(parseBpm("")).toBeNull();
    expect(parseBpm("abc")).toBeNaN();
  });
  it("contrôle les bornes et l'ordre repos < seuil < max", () => {
    const cur = { hr_max: 190, hr_rest: 45, lthr: 170 };
    expect(zonesError({ hr_max: null, hr_rest: null, lthr: 172 }, cur)).toBeNull();
    expect(zonesError({ hr_max: null, hr_rest: null, lthr: 195 }, cur)).toMatch(/repos < FC au seuil < FC max/);
    expect(zonesError({ hr_max: 300, hr_rest: null, lthr: null }, cur)).toMatch(/entre 120 et 230/);
    expect(zonesError({ hr_max: NaN, hr_rest: null, lthr: null }, cur)).toMatch(/pas compris/);
  });
  it("affiche la source et les plages", () => {
    const v = (id: ZoneValue["source_id"], source: string): ZoneValue => ({ value: 1, source, source_id: id, override: null, auto: 1, auto_source: source });
    expect(sourceChip(v("reglage", "réglage"))).toBe("par défaut");
    expect(sourceChip(v("strava", "Strava"))).toBe("Strava");
    expect(zoneRange(null, 144)).toBe("< 145");
    expect(zoneRange(145, 152)).toBe("145-152");
    expect(zoneRange(171, null)).toBe("≥ 171");
  });
});
