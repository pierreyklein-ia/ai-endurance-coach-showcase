import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { onToast } from "../lib/toast";
import { reply, session, stubFetch } from "../test/fakes";
import { LinkPanel } from "./LinkPanel";
import { Glossary, SessionCard } from "./SessionCard";

const TODAY = "2026-11-05";
const auto = { activity: "Footing du soir", start: "2026-11-05T18:00:00", minutes: 52, km: 9.4, ratio: 1, status: "done" };

afterEach(() => { vi.unstubAllGlobals(); onToast(null); });

describe("SessionCard", () => {
  it("affiche titre, durée, D+, détail, glossaire et le bouton de validation", () => {
    const onFb = vi.fn();
    const s = session({ elevation_gain_m: 300, terms: [{ term: "Seuil", text: "allure tenue 1 h" }] });
    const { container } = render(<SessionCard s={s} onFeedback={onFb} today={TODAY} />);
    expect(container.querySelector(".session-title")?.textContent).toBe("Seuil 3 × 10 min");
    expect(container.querySelector(".session-meta")?.textContent).toBe("1 h10 · 300 m D+");
    expect(container.querySelector(".badge.key")).not.toBeNull();
    expect(screen.getByText("C'est quoi ?")).toBeTruthy();
    fireEvent.click(screen.getByText("Valider la séance"));
    expect(onFb).toHaveBeenCalledWith(s);
  });

  it("v0.50.1 : séance changée par le coach, séance d'origine faite", () => {
    const s = session({ title: "Footing", orig_title: "Seuil", feedback: { status: "done", rpe: null, note: "", auto: { ...auto,
      type: { verdict: "origine", ok: true, detected: "seuil", label: "seuil", text: "séance prévue au départ faite" } } } });
    const { container } = render(<SessionCard s={s} onFeedback={() => {}} today={TODAY} />);
    expect(container.querySelector(".orig-title")?.textContent).toBe("Changée par le coach · prévue au départ : Seuil");
    expect(container.querySelector(".type-check.origine")?.textContent).toBe("Type détecté : seuil ✓ (séance prévue au départ)");
  });

  it("version courte : ni détail, ni lien, ni bouton", () => {
    const { container } = render(<SessionCard s={session()} full={false} onFeedback={() => {}} />);
    expect(container.querySelector(".session-detail")).toBeNull();
    expect(container.querySelector(".row-actions")).toBeNull();
  });

  it("séance validée automatiquement : invite au ressenti et type détecté", () => {
    const s = session({ feedback: { status: "done", rpe: null, note: "", auto: { ...auto, type: { verdict: "plus_dur", ok: false,
      detected: "vma", label: "VMA", text: "plus dur que prévu" } } } });
    const { container } = render(<SessionCard s={s} onFeedback={() => {}} today={TODAY} />);
    expect(container.querySelector(".auto-valid")?.textContent).toContain("Validée automatiquement avec ta sortie « Footing du soir » (52 min, 9,4 km)");
    expect(container.querySelector(".type-check.plus_dur")?.textContent).toBe("Type détecté : VMA, plus dur que prévu");
    expect(screen.getByText("Ajouter mon ressenti").className).toContain("primary");
  });

  it("séance liée à la main avec ressenti : statut, note, « Modifier mon retour »", () => {
    const s = session({ feedback: { status: "modified", rpe: 7, note: "jambes lourdes", auto: { ...auto, km: 0, manual: true,
      type: { verdict: "conforme", ok: true, detected: "seuil", label: "Seuil", text: "" } } } });
    const { container } = render(<SessionCard s={s} onFeedback={() => {}} today={TODAY} />);
    expect(container.querySelector(".badge.modified")?.textContent).toBe("Modifiée · 7/10");
    expect(container.querySelector(".note")?.textContent).toBe("jambes lourdes");
    expect(container.querySelector(".auto-valid")?.textContent).toContain("Liée avec ta sortie « Footing du soir » (52 min).");
    expect(container.querySelector(".type-check")?.textContent).toBe("Type détecté : Seuil ✓");
    expect(screen.getByText("Modifier mon retour").className).not.toContain("primary");
  });

  it("glossaire seul", () => {
    const { container } = render(<Glossary terms={[{ term: "VMA", text: "vitesse maximale aérobie" }]} />);
    expect(container.querySelector("dt")?.textContent).toBe("VMA");
  });
});

describe("LinkPanel (lier / délier une sortie)", () => {
  it("rien pour une séance lointaine, une course ou un repos", () => {
    expect(render(<LinkPanel s={session({ day: "2026-11-20" })} today={TODAY} />).container.innerHTML).toBe("");
    expect(render(<LinkPanel s={session({ is_race: true })} today={TODAY} />).container.innerHTML).toBe("");
    expect(render(<LinkPanel s={session({ intensity: "rest", kind: "repos" })} today={TODAY} />).container.innerHTML).toBe("");
    expect(render(<LinkPanel s={session({ intensity: "rest", kind: "renfo" })} today={TODAY} />).container.innerHTML).not.toBe("");
  });

  it("liste les sorties candidates, lie et déplace, puis prévient", async () => {
    const toasts: (string | null)[] = [];
    onToast((m) => toasts.push(m));
    const calls = stubFetch({
      "GET /api/sessions/s1/link": { session_id: "s1", day: TODAY, title: "Seuil", candidates: [
        { ...auto, day: "2026-11-04", linked_to: null, linked_title: "Footing", current: false,
          type: { verdict: "plus_facile", ok: false, detected: "ef", label: "Endurance", text: "plus facile" } },
        { ...auto, start: "x2", day: TODAY, km: 0, linked_to: "s1", linked_title: null, current: true,
          type: { verdict: "non_verifie", ok: false, detected: "", label: "", text: "" } },
      ] },
      "POST /api/sessions/s1/link": { moved_from: TODAY, day: "2026-11-04" },
    });
    const changed = vi.fn();
    render(<LinkPanel s={session()} today={TODAY} onChanged={changed} />);
    fireEvent.click(screen.getByText("Lier une sortie"));
    expect(screen.getByText("Recherche de tes sorties…")).toBeTruthy();
    await screen.findByRole("list", { name: "Sorties possibles" });
    const items = screen.getAllByRole("listitem");
    expect(items[0].textContent).toContain("Endurance : plus facile");
    expect(items[0].textContent).toContain("liée à « Footing »");
    expect(items[0].textContent).toContain("la séance passera au");
    expect(items[1].textContent).toContain("type non vérifié");
    expect((items[1] as HTMLButtonElement).disabled).toBe(true);
    fireEvent.click(items[0]);
    await waitFor(() => expect(changed).toHaveBeenCalled());
    expect(calls.at(-1)?.body).toEqual({ activity_start: auto.start });
    expect(toasts.find((t) => t?.includes("déplacée"))).toBeTruthy();
  });

  it("aucune sortie, erreur de chargement, délier, erreur de lien", async () => {
    const toasts: (string | null)[] = [];
    onToast((m) => toasts.push(m));
    stubFetch({ "GET /api/sessions/s1/link": { session_id: "s1", day: TODAY, title: "", candidates: [] } });
    const linked = session({ feedback: { status: "done", rpe: 5, note: "", auto } });
    const changed = vi.fn();
    const { unmount } = render(<LinkPanel s={linked} today={TODAY} onChanged={changed} />);
    fireEvent.click(screen.getByText("Changer la sortie liée"));
    await screen.findByText(/Aucune sortie du même sport/);
    fireEvent.click(screen.getByText("Changer la sortie liée"));            // referme
    expect(screen.queryByRole("list")).toBeNull();
    stubFetch({ "DELETE /api/sessions/s1/link": { ok: true } });
    fireEvent.click(screen.getByText("Délier"));
    await waitFor(() => expect(changed).toHaveBeenCalled());
    expect(toasts).toContain("Séance déliée : elle est de nouveau à valider.");
    unmount();
    stubFetch({ "GET /api/sessions/s1/link": reply(404, { error: "séance inconnue" }) });
    render(<LinkPanel s={session()} today={TODAY} />);
    fireEvent.click(screen.getByText("Lier une sortie"));
    await waitFor(() => expect(toasts).toContain("séance inconnue"));
    expect(screen.queryByRole("list")).toBeNull();
  });

  it("le lien refusé par le serveur est affiché", async () => {
    const toasts: (string | null)[] = [];
    onToast((m) => toasts.push(m));
    stubFetch({
      "GET /api/sessions/s1/link": { session_id: "s1", day: TODAY, title: "", candidates: [{ ...auto, day: TODAY, linked_to: null,
        linked_title: null, current: false }] },
      "POST /api/sessions/s1/link": reply(422, { error: "sport différent" }),
      "DELETE /api/sessions/s1/link": reply(503, { error: "agenda indisponible" }),
    });
    render(<LinkPanel s={session({ feedback: { status: "done", rpe: 5, note: "", auto } })} today={TODAY} />);
    fireEvent.click(screen.getByText("Changer la sortie liée"));
    fireEvent.click(await screen.findByRole("listitem"));
    await waitFor(() => expect(toasts).toContain("sport différent"));
    await act(async () => { fireEvent.click(screen.getByText("Délier")); });
    await waitFor(() => expect(toasts).toContain("agenda indisponible"));
  });
});
