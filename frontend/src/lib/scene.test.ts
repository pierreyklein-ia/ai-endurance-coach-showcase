import { describe, expect, it } from "vitest";
import { AMBIANCES, PLACES, mix, palette, sceneParts } from "./scene";

describe("décor lieu × ambiance (v0.47)", () => {
  it("répartit un ancien décor unique sur le bon axe", () => {
    expect(sceneParts("hiver")).toEqual({ place: "route", ambiance: "hiver" });
    expect(sceneParts("montagne")).toEqual({ place: "montagne", ambiance: "aucune" });
    expect(sceneParts(null)).toEqual({ place: "route", ambiance: "aucune" });
  });
  it("les valeurs explicites gagnent sur l'ancien décor", () => {
    expect(sceneParts("montagne", "bord_de_mer", "neige")).toEqual({ place: "bord_de_mer", ambiance: "neige" });
    expect(sceneParts("nuit", "ville", undefined)).toEqual({ place: "ville", ambiance: "nuit" });
    expect(sceneParts(null, "inconnu", "bizarre")).toEqual({ place: "route", ambiance: "aucune" });
  });
  it("mélange les couleurs", () => {
    expect(mix("#000000", "#ffffff", 0.5)).toBe("#808080");
    expect(mix("#123456", "#abcdef", 0)).toBe("#123456");
  });
  it("chaque combinaison a une palette valide et l'ambiance se voit", () => {
    for (const p of PLACES) for (const a of AMBIANCES) {
      const pal = palette(p, a);
      for (const v of Object.values(pal)) if (typeof v === "string" && v.startsWith("#")) expect(v).toMatch(/^#[0-9a-f]{6}$/);
    }
    expect(palette("bord_de_mer", "neige").sand).toBe("#f8fafc");          // neige sur le sable
    expect(palette("bord_de_mer", "neige").sea).not.toBe("#f8fafc");       // la mer reste de l'eau
    expect(palette("volcan", "neige").caps).toBeGreaterThan(0);            // volcan enneigé
    expect(palette("ville", "nuit").window).toBe("#fde047");               // fenêtres allumées
    expect(palette("bord_de_mer", "nuit").sun).toBe("moon");
    expect(palette("foret", "automne").foliage).toBe("#ea580c");
    expect(palette("montagne", "aucune").caps).toBeGreaterThan(0);         // la montagne a ses sommets blancs
    expect(palette("route", "aucune").caps).toBe(0);
  });
});
