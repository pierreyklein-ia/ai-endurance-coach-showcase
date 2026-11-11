import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { withFeeling } from "../lib/recap";
import { onToast } from "../lib/toast";
import { reply, stubFetch } from "../test/fakes";
import { WeekFeelingForm } from "./WeekFeelingForm";

afterEach(() => { vi.unstubAllGlobals(); onToast(null); });

describe("WeekFeelingForm", () => {
  it("envoie fatigue, difficulté, plaisir et la note une fois les trois notes données", async () => {
    const calls = stubFetch({ "POST /api/recap/feeling": { fatigue: 4, difficulty: 3, fun: 5, note: "top" } });
    const toasts: (string | null)[] = []; onToast((m) => toasts.push(m));
    const saved = vi.fn();
    render(<WeekFeelingForm weekStart="2026-11-02" onSaved={saved} />);
    const send = screen.getByText("Envoyer au coach") as HTMLButtonElement;
    expect(send.disabled).toBe(true);
    fireEvent.click(screen.getByLabelText("Fatigue 4 sur 5"));
    fireEvent.click(screen.getByLabelText("Difficulté 3 sur 5"));
    fireEvent.click(screen.getByLabelText("Plaisir 5 sur 5"));
    fireEvent.change(screen.getByPlaceholderText("Un mot pour le coach (facultatif)"), { target: { value: "top" } });
    expect(send.disabled).toBe(false);
    fireEvent.click(send);
    await waitFor(() => expect(saved).toHaveBeenCalled());
    expect(calls[0].path).toContain("/api/recap/feeling");
    expect(calls[0].body).toEqual({ week_start: "2026-11-02", fatigue: 4, difficulty: 3, fun: 5, note: "top" });
    expect(toasts[0]).toContain("le coach te répond");
  });

  it("pré-remplit un ressenti déjà donné et affiche l'erreur du serveur", async () => {
    stubFetch({ "POST /api/recap/feeling": reply(422, { error: "fatigue entre 1 et 5" }) });
    const toasts: (string | null)[] = []; onToast((m) => toasts.push(m));
    render(<WeekFeelingForm weekStart="2026-11-02" initial={{ fatigue: 2, difficulty: 2, fun: 4, note: "ok" }} />);
    expect(screen.getByLabelText("Fatigue 2 sur 5").getAttribute("aria-checked")).toBe("true");
    fireEvent.click(screen.getByText("Mettre à jour"));
    await waitFor(() => expect(toasts.length).toBe(1));
  });

  it("withFeeling met le ressenti sur la bonne semaine", () => {
    const it0 = { week_start: "2026-11-02", week_end: "2026-11-08", text: "", sections: [] };
    const f = { fatigue: 3, difficulty: 3, fun: 3, note: "" };
    const v = withFeeling({ last: { ...it0, week_start: "2026-10-26" }, preview: it0 }, "2026-11-02", f);
    expect(v?.preview?.feeling).toEqual(f);
    expect(v?.last?.feeling).toBeUndefined();
    expect(withFeeling(null, "2026-11-02", f)).toBeNull();
  });
});
