import { render } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import type { ChatMeta } from "../api/types";
import { agentChip, costChips, criticLabel, TeamStrip, TeamTrace, teamLine } from "./TeamTrace";

const meta: ChatMeta = {
  trace: "…", routing: { specialists: ["nutrition", "prevention"], reason: "Nutrition : troubles digestifs", by: "superviseur" },
  agents: [
    { name: "prevention", label: "Prévention & santé", icon: "🩺", tools: [{ name: "wellbeing_signals", ms: 1 }], risk_level: "high", veto: true, veto_reason: "douleur 6/10", evidence_ids: ["kb_a"], cost_eur: 0.001, ms: 900 },
    { name: "nutrition", label: "Nutrition", icon: "🍌", tools: [{ name: "nutrition_logs", ms: 2 }, { name: "knowledge_search", ms: 3 }], risk_level: "medium", veto: false, evidence_ids: [], cost_eur: 0.002, ms: 1200 },
  ],
  critic: { verdict: "ok", reasons: [], revised: true },
  cost_eur: 0.0123, latency_s: 4.2, cost_by_agent: { coach: 0.008, critique: 0.0011 },
};

describe("équipe d'agents dans les détails d'un message", () => {
  it("résume l'équipe, les outils, le veto et la critique", () => {
    expect(teamLine(meta)).toBe("Équipe consultée : Prévention (1 outil) ⛔ veto · Nutrition (2 outils) · Critique : validé après 1 révision");
    expect(criticLabel({ verdict: "revise", reasons: ["x"], revised: true })).toBe("réserves transmises aux garde-fous");
    expect(teamLine({ routing: { specialists: [], reason: "", by: "règle" }, agents: [] })).toBe("Équipe consultée : coach seul");
  });
  it("affiche une ligne par agent avec ses outils", () => {
    const { container } = render(<TeamTrace meta={meta} />);
    expect(container.querySelectorAll(".agent-row").length).toBe(2);
    expect(container.textContent).toContain("knowledge_search");
    expect(container.textContent).toContain("Veto : douleur 6/10");
    const strip = render(<TeamStrip meta={meta} />).container;
    expect(strip.querySelectorAll(".agent-av").length).toBe(2);
    expect(strip.textContent).toContain("✓ révisé");
  });
  it("puces : agent, outils, coût, latence, verdict du critique", () => {
    expect(agentChip(meta.agents![1])).toBe("🍌 Nutrition · 2 outils · 0,20 c€ · 1,2 s");
    expect(costChips(meta)).toEqual(["🏃 Coach · 0,80 c€", "🧐 Critique · 0,11 c€", "Total · 1,23 c€ · 4,2 s"]);
    const { container } = render(<TeamTrace meta={meta} />);
    expect(container.querySelectorAll(".team-chip").length).toBe(6);
    expect(container.textContent).toContain("🧐 validé après 1 révision");
  });
});
