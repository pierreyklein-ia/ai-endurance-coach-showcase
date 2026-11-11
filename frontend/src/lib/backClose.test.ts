import { describe, expect, it, vi } from "vitest";
import { afterBack, openOverlays, pushOverlay } from "./backClose";

describe("backClose (v0.48 : « retour » Android ferme la fiche du dessus)", () => {
  it("retour : ferme la fiche du dessus, puis la suivante ; l'adresse (#onglet) ne change pas", () => {
    location.hash = "#semaine";
    const closed: string[] = [];
    pushOverlay(() => closed.push("detail"));
    pushOverlay(() => closed.push("sheet"));
    expect(openOverlays()).toBe(2);
    expect(location.hash).toBe("#semaine");
    window.dispatchEvent(new PopStateEvent("popstate"));
    expect(closed).toEqual(["sheet"]);
    window.dispatchEvent(new PopStateEvent("popstate"));
    expect(closed).toEqual(["sheet", "detail"]);
    expect(openOverlays()).toBe(0);
  });

  it("fermée par un bouton : retire son entrée d'historique, sans fermer la fiche d'en dessous", () => {
    const back = vi.spyOn(history, "back").mockImplementation(() => { window.dispatchEvent(new PopStateEvent("popstate")); });
    const closed: string[] = [];
    pushOverlay(() => closed.push("detail"));
    const release = pushOverlay(() => closed.push("sheet"));
    release();                                     // ✕ / enregistrer
    expect(back).toHaveBeenCalledTimes(1);
    expect(closed).toEqual([]);                    // le popstate de ce retour est ignoré
    expect(openOverlays()).toBe(1);
    const ran: string[] = [];
    afterBack(() => ran.push("go"));
    expect(ran).toEqual(["go"]);
    window.dispatchEvent(new PopStateEvent("popstate"));
    expect(closed).toEqual(["detail"]);
    back.mockRestore();
  });
});
