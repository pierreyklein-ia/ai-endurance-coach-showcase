import type { GearItem } from "../api/types";
import { wornGear } from "./avatar";

const gear = (ids: [string, boolean][]): GearItem[] => ids.map(([id, unlocked]) => ({ id, label: id, unlocked, how: "" }));

describe("wornGear", () => {
  const g = gear([["bandeau", true], ["casquette", true], ["montre", true], ["aura", false]]);
  it("porte tout ce qui est débloqué quand l'athlète n'a rien choisi", () => {
    expect([...wornGear({ gear: null }, g)].sort()).toEqual(["casquette", "montre"]);   // la casquette remplace le bandeau
  });
  it("ne garde que les choix débloqués", () => {
    expect([...wornGear({ gear: ["bandeau", "aura"] }, g)]).toEqual(["bandeau"]);
  });
  it("casquette et bandeau ne se portent pas ensemble", () => {
    expect([...wornGear({ gear: ["bandeau", "casquette"] }, g)]).toEqual(["casquette"]);
  });
  it("accessoires de saison : portés d'office seulement en saison, un seul chapeau", () => {
    const s: GearItem[] = [...gear([["casquette", true], ["montre", true]]),
      { id: "bonnet_noel", label: "", unlocked: true, how: "", seasonal: true, in_season: true },
      { id: "chapeau_sorciere", label: "", unlocked: true, how: "", seasonal: true, in_season: false }];
    expect([...wornGear({ gear: null }, s)]).toEqual(["montre", "bonnet_noel"]);
    expect([...wornGear({ gear: ["casquette", "chapeau_sorciere", "bonnet_noel"] }, s)]).toEqual(["chapeau_sorciere"]);
  });
  it("supporte une liste d'équipement absente", () => {
    expect(wornGear({ gear: null }, undefined).size).toBe(0);
  });
});
