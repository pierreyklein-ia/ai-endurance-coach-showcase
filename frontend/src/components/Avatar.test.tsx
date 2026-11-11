/* Avatar (dessin, décors, image générée), éditeur d'avatar, choix du coach. */
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { AMBIANCES, PLACES } from "../lib/scene";
import { onToast } from "../lib/toast";
import { career, reply, stubFetch } from "../test/fakes";
import { AvatarEditor } from "./AvatarEditor";
import { sceneLayers } from "./AvatarScene";
import { AvatarPic, AvatarSvg } from "./AvatarSvg";
import { CoachPicker } from "./CoachPicker";

afterEach(() => { vi.unstubAllGlobals(); onToast(null); });

describe("dessin de l'avatar", () => {
  it("chaque lieu × ambiance produit un décor", () => {
    for (const p of PLACES) for (const a of AMBIANCES) expect(sceneLayers(p, a, `${p}-${a}`).length).toBeGreaterThan(0);
  });

  it("silhouette complète ou tête seule, accessoires portés, étoiles au-delà du niveau 20", () => {
    const c = career();
    const cfg = { ...c.avatar, hair: "long", beard: true, gear: ["bandeau", "montre", "casquette", "lunettes", "frontale", "sac", "batons",
      "chapeau_sorciere", "bonnet_noel", "chapeau_fete", "dossard", "medaille"] };
    const gear = cfg.gear.map((id) => ({ id, label: id, unlocked: true, how: "" }));
    const full = render(<AvatarSvg cfg={cfg} level={24} gear={gear} scene="sommet" place="volcan" ambiance="nuit" size={200} />).container;
    expect(full.querySelector("svg")).not.toBeNull();
    const head = render(<AvatarSvg cfg={{ ...cfg, hair: "rase", gear: null }} level={1} gear={[]} scene={null} head />).container;
    expect(head.querySelector("svg")).not.toBeNull();
  });

  it("image générée, puis repli sur le dessin si elle ne charge pas", () => {
    const c = career({ avatar_image: { enabled: true, ready: true, generating: false, error: null, url: "/api/career/avatar.png?v=1", next_at: null } });
    const { container } = render(<AvatarPic c={c} size={36} head />);
    const img = container.querySelector("img")!;
    expect(img.getAttribute("alt")).toBe("Avatar niveau 7");
    fireEvent.error(img);
    expect(container.querySelector("img")).toBeNull();
    expect(container.querySelector("svg")).not.toBeNull();
  });
});

describe("éditeur d'avatar", () => {
  const c = career({
    gear: [{ id: "bandeau", label: "Bandeau", unlocked: true, how: "" }, { id: "frontale", label: "Frontale", unlocked: false, how: "niveau 10" }],
    places: [{ id: "route", label: "Route", unlocked: true, how: "" }, { id: "volcan", label: "Volcan", unlocked: true, how: "" },
      { id: "sommet", label: "Sommet", unlocked: false, how: "niveau 12" }],
    ambiances: [{ id: "neige", label: "Neige", unlocked: true, how: "" }, { id: "nuit", label: "Nuit", unlocked: false, how: "niveau 9" }],
  });

  it("modifie couleurs, coiffure, accessoire, lieu et ambiance puis enregistre", async () => {
    const toasts: (string | null)[] = [];
    onToast((m) => toasts.push(m));
    const calls = stubFetch({ "POST /api/career/avatar": (body: unknown) => ({ avatar: body }) });
    const onSaved = vi.fn();
    const { container } = render(<AvatarEditor career={c} open onClose={() => {}} onSaved={onSaved} />);
    fireEvent.click(screen.getByLabelText("shirt 3"));
    fireEvent.click(screen.getByText("Barbe"));
    fireEvent.click(screen.getByText("Bandeau"));
    expect((screen.getByText("🔒 Frontale (niveau 10)") as HTMLButtonElement).disabled).toBe(true);
    fireEvent.click(container.querySelector("[data-place=volcan]")!);
    fireEvent.click(container.querySelector("[data-ambiance=neige]")!);
    expect((container.querySelector("[data-place=sommet]") as HTMLButtonElement).disabled).toBe(true);
    fireEvent.submit(container.querySelector("form")!);
    await waitFor(() => expect(onSaved).toHaveBeenCalled());
    expect(calls[0].body).toMatchObject({ shirt: 2, beard: true, gear: ["bandeau"], place: "volcan", ambiance: "neige" });
    expect(toasts).toContain("Avatar enregistré ✓");
    fireEvent.click(screen.getByText("Auto (selon le niveau)"));
    fireEvent.click(screen.getByText("Auto (la saison)"));
    stubFetch({ "POST /api/career/avatar": reply(422, { error: "avatar invalide" }) });
    fireEvent.submit(container.querySelector("form")!);
    await waitFor(() => expect(toasts).toContain("avatar invalide"));
  });

  it("fermé ou sans carrière : formulaire vide", () => {
    const { container } = render(<AvatarEditor career={null} open={false} onClose={() => {}} onSaved={() => {}} />);
    expect(container.querySelector("[data-place]")).toBeNull();
  });
});

describe("choix du coach", () => {
  it("genre, personnalité, prénom, vouvoiement puis enregistrement", async () => {
    const calls = stubFetch({ "POST /api/coach": (b: unknown) => b });
    const onSaved = vi.fn();
    render(<CoachPicker value={null} submitLabel="C'est parti" onSaved={onSaved} />);
    fireEvent.click(screen.getByText("Homme"));
    expect((document.getElementById("coach-name") as HTMLInputElement).value).toBe("Marc");
    fireEvent.click(screen.getAllByRole("radio")[1]);
    fireEvent.click(screen.getByText("en vouvoyant"));
    fireEvent.change(document.getElementById("coach-name")!, { target: { value: "  Hugo  " } });
    fireEvent.click(screen.getByText("C'est parti"));
    await waitFor(() => expect(onSaved).toHaveBeenCalled());
    expect(calls[0].body).toMatchObject({ gender: "homme", name: "Hugo", address: "vous", vocabulary: "simple" });
  });

  it("prénom vide : bouton désactivé ; vocabulaire expert ; erreur du serveur affichée", async () => {
    const toasts: (string | null)[] = [];
    onToast((m) => toasts.push(m));
    stubFetch({ "POST /api/coach": reply(500, { error: "panne" }) });
    render(<CoachPicker value={{ gender: "femme", personality: "exigeant", name: "Léa", address: "tu" }} submitLabel="Enregistrer" onSaved={() => {}} />);
    fireEvent.change(document.getElementById("coach-name")!, { target: { value: "   " } });
    expect((screen.getByText("Enregistrer") as HTMLButtonElement).disabled).toBe(true);
    fireEvent.click(screen.getByText("expert"));
    expect(screen.getByText(/sans explication/)).toBeTruthy();
    fireEvent.change(document.getElementById("coach-name")!, { target: { value: "Zoé" } });
    fireEvent.click(screen.getByText("Enregistrer"));
    await waitFor(() => expect(toasts).toContain("panne"));
  });
});
