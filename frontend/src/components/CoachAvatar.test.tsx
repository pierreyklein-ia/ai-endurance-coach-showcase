import { renderToStaticMarkup } from "react-dom/server";
import { CoachAvatar } from "./CoachAvatar";

describe("CoachAvatar", () => {
  it("dessine les 8 variantes en buste et en médaillon, sans valeur invalide", () => {
    for (const g of ["femme", "homme"] as const) for (const p of ["bienveillant", "exigeant", "motivateur", "sante"] as const) {
      const bust = renderToStaticMarkup(<CoachAvatar gender={g} personality={p} />);
      const head = renderToStaticMarkup(<CoachAvatar gender={g} personality={p} size={32} head />);
      expect(bust).toContain('viewBox="0 0 120 142"');
      expect(head).toContain('width="32"');
      expect(bust + head).not.toMatch(/NaN|undefined/);
    }
  });
});
