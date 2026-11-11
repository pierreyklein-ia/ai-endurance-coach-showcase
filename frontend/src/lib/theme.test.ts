import { applyTheme, currentTheme, initTheme } from "./theme";

describe("thèmes", () => {
  beforeEach(() => {
    document.head.innerHTML = '<meta name="theme-color" content="x" media="(prefers-color-scheme: light)"><meta name="theme-color" content="y">';
    localStorage.clear();
    delete document.documentElement.dataset.theme;
  });
  it("pose data-theme, la couleur de barre et retient le choix", () => {
    expect(applyTheme("toon")).toBe("toon");
    expect(document.documentElement.dataset.theme).toBe("toon");
    expect(localStorage.getItem("theme")).toBe("toon");
    document.querySelectorAll('meta[name="theme-color"]').forEach((m) => expect(m.getAttribute("content")).toBe("#4bb9ec"));
    expect(currentTheme()).toBe("toon");
  });
  it("revient au thème classique pour une valeur inconnue", () => {
    expect(applyTheme("néon")).toBe("classique");
    expect(applyTheme(null)).toBe("classique");
  });
  it("reprend le thème gardé sur l'appareil", () => {
    localStorage.setItem("theme", "crepuscule");
    expect(initTheme()).toBe("crepuscule");
    expect(document.documentElement.dataset.theme).toBe("crepuscule");
  });
});
