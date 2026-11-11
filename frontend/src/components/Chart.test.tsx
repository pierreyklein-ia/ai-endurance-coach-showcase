import { render } from "@testing-library/react";
import { LineChart } from "./Chart";
import { RadarChart } from "./RadarChart";

describe("graphiques", () => {
  it("montre un message tant qu'il n'y a pas deux points", () => {
    const { container } = render(<LineChart series={[{ label: "x", color: "accent", points: [["2026-01-01", 1], ["2026-01-02", null]] }]} />);
    expect(container.textContent).toBe("Pas encore assez de données.");
  });
  it("trace une courbe avec légende quand il y a plusieurs séries", () => {
    const pts = (k: number): [string, number][] => [["2026-01-01", k], ["2026-02-01", k + 3], ["2026-03-01", k + 1]];
    const { container } = render(<LineChart series={[{ label: "Forme", color: "accent", points: pts(50) }, { label: "Fatigue", color: "warn", points: pts(40) }]} />);
    expect(container.querySelectorAll("path.ln").length).toBe(2);
    expect(container.querySelector(".legend")?.textContent).toBe("FormeFatigue");
    expect(container.querySelector(".chart-tip")?.hasAttribute("hidden")).toBe(true);
  });
  it("dessine le radar du profil", () => {
    const axes = ["A", "B", "C"].map((label, i) => ({ key: label, label, score: 5 + i, score_3m: null, tip: "" }));
    const { container } = render(<RadarChart axes={axes} />);
    expect(container.querySelectorAll("polygon.ring").length).toBe(5);
    expect(container.querySelector("polygon.rp.now")).not.toBeNull();
    expect(container.querySelector("polygon.rp.old")).toBeNull();
    expect(container.querySelectorAll(".rscore")[2].textContent).toBe("7");
  });
});
