import { describe, expect, it } from "vitest";
import { transcriptFrom } from "./dictation";

const r = (transcript: string, isFinal = true) => ({ isFinal, 0: { transcript }, length: 1 });

describe("transcriptFrom (v0.48 : dictée sans doublons, Chrome Android)", () => {
  it("résultats normaux : finals puis interim", () => {
    expect(transcriptFrom([r("Je cours au club le mardi."), r(" et le vendredi", false)]))
      .toBe("Je cours au club le mardi. et le vendredi");
  });

  it("Android : la même phrase finale répétée n'est gardée qu'une fois", () => {
    const seq = [r("Je cours au club en trail le mardi"), r("Je cours au club en trail le mardi"),
      r("je cours au club en trail le mardi."), r("Je cours au club en trail le mardi", false)];
    expect(transcriptFrom(seq)).toBe("Je cours au club en trail le mardi");
  });

  it("Android : résultats cumulatifs (chaque final reprend le précédent)", () => {
    const seq = [r("je cours"), r("je cours au club"), r("je cours au club le mardi"), r("je cours au club le mardi 1h15", false)];
    expect(transcriptFrom(seq)).toBe("je cours au club le mardi 1h15");
  });

  it("séquence d'événements simulée : le texte affiché ne grossit jamais par copie", () => {
    // ce que Chrome Android envoie à chaque onresult (resultIndex repart de 0, la liste grandit)
    const events = [
      [r("je cours", false)],
      [r("je cours au club", true)],
      [r("je cours au club", true), r("je cours au club le mardi", false)],
      [r("je cours au club", true), r("je cours au club le mardi", true)],
      [r("je cours au club", true), r("je cours au club le mardi", true), r("et le vendredi", false)],
    ];
    const shown = events.map((e) => transcriptFrom(e));
    expect(shown[shown.length - 1]).toBe("je cours au club le mardi et le vendredi");
    for (const t of shown) expect(t.split("je cours").length - 1).toBeLessThanOrEqual(1);
  });

  it("une phrase plus courte déjà contenue est ignorée, les vides aussi", () => {
    expect(transcriptFrom([r("bonjour coach"), r("bonjour"), r("   ")])).toBe("bonjour coach");
  });
});
