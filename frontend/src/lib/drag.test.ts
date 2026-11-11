import type { Session, WeekView } from "../api/types";
import { canDrag, dropRefusal, dropWarning, edgeScroll, isHard, moveLocal, pickDay, raceDays } from "./drag";

const S = (o: Partial<Session>): Session => ({ session_id: "s", day: "2026-10-01", intensity: "threshold", kind: "seuil",
  title: "Seuil", detail: "", duration_min: 55, elevation_gain_m: 0, key: true, feedback: null, ...o });

const week = (): WeekView => ({
  start: "2026-09-28", today: "2026-09-30", planned_min: 0, done_min: 0, phase: null, focus: null, review: null,
  reviewed_at: null, keys: [],
  days: ["2026-09-28", "2026-09-29", "2026-09-30", "2026-10-01", "2026-10-02", "2026-10-03", "2026-10-04"].map((date) => ({
    date, activities: [],
    sessions: date === "2026-10-01" ? [S({})] : date === "2026-10-03" ? [S({ session_id: "r", day: date, intensity: "rest", title: "Repos" })]
      : date === "2026-10-04" ? [S({ session_id: "race", day: date, intensity: "race", is_race: true })] : [],
  })),
});

describe("glisser-déposer : règles", () => {
  it("seules les séances à venir, non validées, hors course et repos se saisissent", () => {
    const today = "2026-09-30";
    expect(canDrag(S({}), today)).toBe(true);
    expect(canDrag(S({ intensity: "rest", kind: "renfo" }), today)).toBe(true);     // v0.44 : le renfo se déplace
    expect(canDrag(S({ intensity: "rest", kind: "repos" }), today)).toBe(false);
    expect(canDrag(S({ day: "2026-09-30" }), today)).toBe(true);
    expect(canDrag(S({ day: "2026-09-29" }), today)).toBe(true);
    expect(canDrag(S({ feedback: { status: "done", rpe: 5, note: "" } }), today)).toBe(false);
    expect(canDrag(S({ is_race: true }), today)).toBe(false);
    expect(canDrag(S({ intensity: "rest" }), today)).toBe(false);
  });

  it("refuse seulement le même jour et le passé ; le jour de course n'est qu'un avertissement (v0.38)", () => {
    const w = week(), races = raceDays(w), s = S({});
    expect([...races]).toEqual(["2026-10-04"]);
    expect(dropRefusal(s, "2026-10-01", w.today, races)).toBe("même jour");
    expect(dropRefusal(s, "2026-09-29", w.today, races)).toBe("jour passé");
    expect(dropRefusal(s, "2026-10-04", w.today, races)).toBeNull();
    expect(dropRefusal(s, "2026-10-02", w.today, races)).toBeNull();
    expect(dropWarning(s, w.days[6], races)?.kind).toBe("race");
    expect(dropWarning(s, w.days[4], races)).toBeNull();
  });

  it("prévient quand le dépôt crée deux séances dures le même jour", () => {
    const w = week(), races = raceDays(w);
    const day = { ...w.days[4], sessions: [S({ session_id: "sl", day: "2026-10-02", intensity: "long", planned_load: 120 })] };
    expect(dropWarning(S({}), day, races)).toEqual({ kind: "two_hard", label: "2 séances dures le même jour" });
    expect(dropWarning(S({ intensity: "z2" }), day, races)).toBeNull();
    expect(isHard(S({ intensity: "tempo", planned_load: 0 }))).toBe(false);
    expect(isHard(S({ intensity: "long", is_race: true }))).toBe(false);
  });
});

describe("glisser-déposer : géométrie", () => {
  const rects = [
    { day: "a", top: 0, bottom: 100, left: 16, right: 374 },
    { day: "b", top: 108, bottom: 200, left: 16, right: 374 },
  ];
  it("trouve la carte sous le doigt, ou la plus proche dans l'interstice", () => {
    expect(pickDay(rects, 100, 50)).toBe("a");
    expect(pickDay(rects, 100, 150)).toBe("b");
    expect(pickDay(rects, 100, 104)).toBe("a");          // entre deux cartes : la plus proche
    expect(pickDay(rects, 100, 260)).toBeNull();         // trop loin
    expect(pickDay(rects, 5, 50)).toBeNull();            // hors colonne
  });
  it("défile près des bords, plus vite tout au bord", () => {
    expect(edgeScroll(400, 844)).toBe(0);
    expect(edgeScroll(60, 844, 56)).toBeLessThan(0);
    expect(edgeScroll(10, 844)).toBeLessThan(edgeScroll(60, 844));
    expect(edgeScroll(800, 844, 0, 64)).toBeGreaterThan(0);
    expect(edgeScroll(844, 844)).toBe(18);
  });
});

describe("mise à jour optimiste", () => {
  it("déplace la séance et remplace le repos du jour d'arrivée", () => {
    const w = moveLocal(week(), "s", "2026-10-03");
    expect(w.days[3].sessions).toEqual([]);
    expect(w.days[5].sessions.map((x) => [x.session_id, x.day])).toEqual([["s", "2026-10-03"]]);
  });
  it("sans effet si même jour, séance inconnue ou jour hors semaine", () => {
    const w0 = week();
    expect(moveLocal(w0, "s", "2026-10-01")).toBe(w0);
    expect(moveLocal(w0, "zz", "2026-10-02")).toBe(w0);
    expect(moveLocal(w0, "s", "2026-12-25")).toBe(w0);
  });
});
