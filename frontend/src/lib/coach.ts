/* v0.36 : coach personnalisable — valeurs par défaut et libellés (logique pure, testée) */
import type { CoachGender, CoachPersona, CoachPersonality } from "../api/types";

export const DEFAULT_NAMES: Record<CoachGender, string> = { femme: "Léa", homme: "Marc" };
export const DEFAULT_COACH: CoachPersona = { gender: "femme", personality: "bienveillant", name: DEFAULT_NAMES.femme, address: "tu" };

export const PERSONALITIES: { key: CoachPersonality; label: string; desc: string }[] = [
  { key: "bienveillant", label: "Bienveillant", desc: "Chaleur et encouragements à chaque progrès." },
  { key: "exigeant", label: "Exigeant", desc: "Direct, précis, de la rigueur. Jamais dans la douleur." },
  { key: "motivateur", label: "Motivateur", desc: "De l'énergie pour te donner envie d'y aller." },
  { key: "sante", label: "Santé d'abord", desc: "Sommeil, récup et signaux du corps avant tout." },
];

/** Accord du libellé selon le genre du coach (« Bienveillante », « Motivatrice »…). */
export function personalityLabel(p: CoachPersonality, g: CoachGender): string {
  const base = PERSONALITIES.find((x) => x.key === p)?.label ?? p;
  if (g === "homme") return base;
  return base === "Bienveillant" ? "Bienveillante" : base === "Exigeant" ? "Exigeante" : base === "Motivateur" ? "Motivatrice" : base;
}

export const defaultName = (g: CoachGender): string => DEFAULT_NAMES[g];

/** Changement de genre : le prénom suggéré suit, sauf si l'athlète en a tapé un autre. */
export function withGender(c: CoachPersona, g: CoachGender): CoachPersona {
  const suggested = !c.name.trim() || Object.values(DEFAULT_NAMES).includes(c.name.trim());
  return { ...c, gender: g, name: suggested ? DEFAULT_NAMES[g] : c.name };
}

/** Même nettoyage que le serveur (coach/persona.py) : lettres, chiffres, espace, tiret, apostrophe ; 20 caractères. */
export function cleanName(v: string): string {
  return v.normalize("NFC").replace(/[^\p{L}\p{N}_ \-'’]/gu, "").replace(/\s+/g, " ").replace(/^[ \-'’]+|[ \-'’]+$/g, "").slice(0, 20).trim();
}

export const coachOf = (c: CoachPersona | null | undefined): CoachPersona => c ?? DEFAULT_COACH;
/** « ta coach » / « ton coach » */
export const yourCoach = (g: CoachGender): string => (g === "femme" ? "ta coach" : "ton coach");
