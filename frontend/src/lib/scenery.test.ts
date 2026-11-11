import { describe, expect, it } from "vitest";
import { applyScenery, sceneryPos } from "./scenery";

describe("panorama du cel-shading", () => {
  it("va de la montagne (Aujourd'hui) à la mer (Course)", () => {
    expect(sceneryPos("aujourdhui")).toBe(0);
    expect(sceneryPos("course")).toBe(1);
    expect(sceneryPos("carriere")).toBe(0);
  });
  it("publie la position et le sens", () => {
    const el = document.createElement("div");
    applyScenery("coach", el);
    expect(el.style.getPropertyValue("--scenery")).toBe("0.4");
    expect(el.dataset.navDir).toBe("right");
    applyScenery("semaine", el);
    expect(el.dataset.navDir).toBe("left");
  });
});
