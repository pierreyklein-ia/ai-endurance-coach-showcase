import { cleanName, coachOf, DEFAULT_COACH, defaultName, personalityLabel, withGender, yourCoach } from "./coach";

describe("coach personnalisable", () => {
  it("prénom suggéré selon le genre", () => {
    expect(defaultName("femme")).toBe("Léa");
    expect(defaultName("homme")).toBe("Marc");
  });
  it("le prénom suggéré suit le genre, pas un prénom tapé", () => {
    expect(withGender(DEFAULT_COACH, "homme").name).toBe("Marc");
    expect(withGender({ ...DEFAULT_COACH, name: "" }, "homme").name).toBe("Marc");
    expect(withGender({ ...DEFAULT_COACH, name: "Zoé" }, "homme")).toMatchObject({ gender: "homme", name: "Zoé" });
  });
  it("accord des libellés", () => {
    expect(personalityLabel("motivateur", "femme")).toBe("Motivatrice");
    expect(personalityLabel("exigeant", "femme")).toBe("Exigeante");
    expect(personalityLabel("sante", "femme")).toBe("Santé d'abord");
    expect(personalityLabel("bienveillant", "homme")).toBe("Bienveillant");
    expect(yourCoach("femme")).toBe("ta coach");
  });
  it("nettoie le prénom comme le serveur", () => {
    expect(cleanName("  <b>Jo</b>; ")).toBe("bJob");
    expect(cleanName("Jean-Luc O'Neil")).toBe("Jean-Luc O'Neil");
    expect(cleanName("x".repeat(30))).toHaveLength(20);
  });
  it("valeurs par défaut si le serveur n'envoie rien", () => {
    expect(coachOf(undefined)).toEqual(DEFAULT_COACH);
  });
});
