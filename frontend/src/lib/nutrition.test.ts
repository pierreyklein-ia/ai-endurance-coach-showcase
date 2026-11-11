import type { Food } from "../api/types";
import { carbsVerdict, fmtHours, liveTotals, targetCarbs, toItems } from "./nutrition";

const F = (o: Partial<Food>): Food => ({ id: "gel", name: "Gel", family: "gel", portion: "1", carbs_g: 25, sodium_mg: 50, fluid_ml: 0,
  caffeine_mg: 0, fructose: true, digest: "rapide", note: "", ...o });
const foods = [F({}), F({ id: "iso", family: "boisson", carbs_g: 30, sodium_mg: 250, fluid_ml: 500 }), F({ id: "pate_fruits", carbs_g: 18, sodium_mg: 0 })];

describe("bilan nutrition : calculs", () => {
  it("calcule g/h, sodium/h et ml/h comme le serveur", () => {
    const p = { gel: { qty: 3, carbs: null }, iso: { qty: 1, carbs: null }, pate_fruits: { qty: 2, carbs: 22 }, x: { qty: 0, carbs: null } };
    const t = liveTotals(foods, p, 500, 130 / 60);
    expect(t.carbs_g).toBe(149);
    expect(t.carbs_g_h).toBe(69);                     // même valeur que le test serveur (tests/test_v038.py)
    expect(t.fluid_ml_h).toBe(462);
    expect(t.sodium_mg_h).toBe(Math.round(400 / (130 / 60)));
    expect(toItems(p)).toHaveLength(3);
  });
  it("donne la cible selon la durée et la tolérance", () => {
    expect(targetCarbs(2, null)).toEqual([30, 60]);
    expect(targetCarbs(3, null)).toEqual([60, 90]);
    expect(targetCarbs(3, 70)).toEqual([60, 80]);
    expect(targetCarbs(5, 115)).toEqual([60, 115]);
    expect(targetCarbs(5, 130)).toEqual([60, 120]);
    expect(carbsVerdict(20, [30, 60])).toBe("bas");
    expect(carbsVerdict(95, [60, 90])).toBe("haut");
    expect(fmtHours(130 / 60)).toBe("2 h 10");
  });
});
