import { describe, expect, it } from "vitest";
import type { RecapItem } from "../api/types";
import { addDaysIso, isAlert, recapToShow } from "./recap";

const item = (week_start: string): RecapItem => ({ week_start, week_end: addDaysIso(week_start, 6), text: "", sections: [] });

describe("récap de la semaine", () => {
  it("montre le dernier récap s'il est récent, sinon l'aperçu", () => {
    expect(recapToShow({ last: item("2026-11-02"), preview: item("2026-11-09") }, "2026-11-09")).toEqual({ item: item("2026-11-02"), preview: false });
    expect(recapToShow({ last: item("2026-10-19"), preview: item("2026-11-09") }, "2026-11-09")).toEqual({ item: item("2026-11-09"), preview: true });
    expect(recapToShow({ last: null, preview: item("2026-11-09") }, "2026-11-09")?.preview).toBe(true);
    expect(recapToShow({ last: item("2026-10-19"), preview: null }, "2026-11-09")?.preview).toBe(false);
    expect(recapToShow(null, "2026-11-09")).toBeNull();
  });
  it("calcule les dates et repère les alertes", () => {
    expect(addDaysIso("2026-11-02", 6)).toBe("2026-11-08");
    expect(addDaysIso("2026-03-28", 2)).toBe("2026-03-30");
    expect(isAlert("⚠ deux jours durs d'affilée")).toBe(true);
    expect(isAlert("❓ ta sortie longue est lundi, ça te va ?")).toBe(true);
    expect(isAlert("Total : 5 h")).toBe(false);
  });
});
