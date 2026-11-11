import { addDays, dayLabel, eur, fmtDur, fmtMin, fmtPace, fr1, joinBase, niceTicks, signed } from "./format";

describe("formats français", () => {
  it("affiche les jours et les durées comme l'interface historique", () => {
    expect(dayLabel("2026-10-15")).toBe("jeu. 15 oct.");
    expect(fmtMin(0)).toBe("");
    expect(fmtMin(45)).toBe("45 min");
    expect(fmtMin(60)).toBe("1 h");
    expect(fmtMin(95)).toBe("1 h35");
    expect(fmtMin(65)).toBe("1 h05");
    expect(addDays("2026-09-28", 7)).toBe("2026-10-05");
    expect(addDays("2026-03-30", -7)).toBe("2026-03-23");
  });
  it("formate chronos, allures, nombres", () => {
    expect(fmtDur(null)).toBe("n/d");
    expect(fmtDur(2280)).toBe("38:00");
    expect(fmtDur(10908)).toBe("3 h 01");
    expect(fmtPace(338)).toBe("5:38/km");
    expect(fr1(57.84)).toBe("57,8");
    expect(fr1(null)).toBe("n/d");
    expect(signed(5.4)).toBe("+5.4");
    expect(signed(-3)).toBe("-3");
    expect(signed(null)).toBe("n/d");
    expect(eur(0.5)).toBe("0,50 €");
    expect(eur(undefined)).toBe("0,00 €");
  });
  it("choisit des graduations rondes", () => {
    expect(niceTicks(0, 100)).toEqual([0, 25, 50, 75, 100]);
    expect(niceTicks(52.1, 61.3)).toEqual([52.5, 55, 57.5, 60]);
  });
  it("ajoute la dictée au texte déjà écrit", () => {
    expect(joinBase("", " bonjour ")).toBe("bonjour");
    expect(joinBase("J'ai couru  ", "10 km")).toBe("J'ai couru 10 km");
    expect(joinBase("début", "")).toBe("début");
  });
});
