import type { GoalItem } from "../api/types";
import { countdown, goalFacts, goalWhen, groupGoals, sortGoalCards } from "./goals";

const g = (o: Partial<GoalItem>): GoalItem => ({ role: "B", label: "", name: "x", date: null, year: null, days_left: null,
  km: null, dplus: null, kind: "trail", note: "", done: false, ...o });

describe("objectifs", () => {
  it("formate la date, les repères et le compte à rebours", () => {
    expect(goalWhen({ date: "2026-11-08", year: 2026 })).toBe("dimanche 8 nov. 2026");
    expect(goalWhen({ date: null, year: 2028 })).toBe("2028, date à préciser");
    expect(goalWhen({ date: null, year: null })).toBe("date à préciser");
    expect(goalFacts({ km: 21.1, dplus: null, kind: "route" })).toBe("21,1\u00a0km · route");
    expect(goalFacts({ km: 165, dplus: 10000, kind: "trail" })).toBe("165\u00a0km · 10000\u00a0m\u00a0D+");
    expect(countdown({ days_left: 15, done: false })).toBe("J-15");
    expect(countdown({ days_left: 0, done: false })).toBe("aujourd'hui");
    expect(countdown({ days_left: -3, done: true })).toBe("terminé");
    expect(countdown({ days_left: null, done: false })).toBe("");
  });

  it("principal d'abord, puis secondaires et projets du plus proche au plus lointain (passés à la fin)", () => {
    const r = groupGoals([
      g({ name: "C-loin", role: "C", date: "2028-01-01" }), g({ name: "B-dec", date: "2026-12-06" }),
      g({ name: "A", role: "A", date: "2026-10-15" }), g({ name: "B-passé", date: "2026-09-01", done: true }),
      g({ name: "B-nov", date: "2026-11-08" }), g({ name: "B-sans-date" }), g({ name: "C-proche", role: "C", date: "2027-06-20" }),
    ]);
    expect(r.main?.name).toBe("A");
    expect(r.secondary.map((x) => x.name)).toEqual(["B-nov", "B-dec", "B-sans-date", "B-passé"]);
    expect(r.projects.map((x) => x.name)).toEqual(["C-proche", "C-loin"]);
    expect(groupGoals([]).main).toBeNull();
  });

  it("cartes d'estimation de Progrès : principal puis secondaires par date", () => {
    const r = sortGoalCards([{ name: "b2", role: "B", date: "2026-12-06" }, { name: "b1", role: "B", date: "2026-11-08" },
      { name: "a", role: "A", date: "2026-10-15" }, { name: "old", date: "2027-01-01" }]);
    expect(r.map((x) => x.name)).toEqual(["a", "old", "b1", "b2"]);
  });
});
