/* éditeur d'avatar */
import { useEffect, useState, type FormEvent, type ReactElement, type ReactNode } from "react";
import { api } from "../api/client";
import type { AvatarCfg, Career } from "../api/types";
import { AV, wornGear } from "../lib/avatar";
import { frDateTime } from "../lib/format";
import { sceneParts } from "../lib/scene";
import { toast } from "../lib/toast";
import { AvatarSvg } from "./AvatarSvg";
import { Sheet } from "./Sheet";

type ColorKey = "skin" | "hair_color" | "shirt" | "shorts" | "shoes";

export function AvatarEditor({ career, open, onClose, onSaved }: {
  career: Career | null; open: boolean; onClose: () => void; onSaved: (avatar: AvatarCfg) => void;
}): ReactElement {
  const [d, setD] = useState<AvatarCfg | null>(null);
  useEffect(() => {
    if (open && career) setD({ ...career.avatar, gear: career.avatar.gear === null ? null : [...career.avatar.gear] });
  }, [open]);   // brouillon repris à chaque ouverture seulement

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    try {
      // v0.47 : lieu + ambiance ; on n'envoie pas l'ancien `scene` (le serveur le recalcule)
      const body = { ...d, scene: undefined, place: d?.place ?? null, ambiance: d?.ambiance ?? null };
      const r = await api<{ avatar: AvatarCfg }>("/api/career/avatar", { json: body });
      onSaved(r.avatar);
      toast("Avatar enregistré ✓");
    } catch (err) { toast((err as Error).message); }
  };

  let preview: ReactNode = null, options: ReactNode = null;
  const c = career;
  if (c && d) {
    const set = (patch: Partial<AvatarCfg>) => setD({ ...d, ...patch });
    const draft = sceneParts(d.scene, d.place, d.ambiance);              // brouillon (ancien `scene` réparti)
    const placeSel = d.place !== undefined ? d.place : (d.scene && draft.place === d.scene ? d.scene : null);
    const ambSel = d.ambiance !== undefined ? d.ambiance : (d.scene && draft.ambiance === d.scene ? d.scene : null);
    preview = <AvatarSvg cfg={d} level={c.level} gear={c.gear} scene={c.scene}
      place={placeSel || c.auto_place || c.place} ambiance={ambSel || c.auto_ambiance || c.ambiance} size={130} />;
    const swatches = (key: ColorKey, colors: string[]) => (
      <div className="swatches">
        {colors.map((col, i) => (
          <button key={i} type="button" className={"sw" + (d[key] === i ? " on" : "")} aria-label={`${key} ${i + 1}`} onClick={() => set({ [key]: i })}>
            <svg viewBox="0 0 20 20" width={26} height={26}><circle cx={10} cy={10} r={9} fill={col} stroke="#0003" /></svg>
          </button>
        ))}
      </div>
    );
    const row = (label: string, el: ReactNode) => <div className="av-row"><div className="label">{label}</div>{el}</div>;
    const worn = wornGear(d, c.gear);
    const img = c.avatar_image;
    options = <>
      {row("Peau", swatches("skin", AV.skin))}
      {row("Coiffure", (
        <div className="seg">
          {AV.hair.map(([v, l]) => <button key={v} type="button" className={"chip" + (d.hair === v ? " on" : "")} onClick={() => set({ hair: v })}>{l}</button>)}
          <button type="button" className={"chip" + (d.beard ? " on" : "")} onClick={() => set({ beard: !d.beard })}>Barbe</button>
        </div>
      ))}
      {row("Cheveux", swatches("hair_color", AV.hairColor))}
      {row("Maillot", swatches("shirt", AV.shirt))}
      {row("Short", swatches("shorts", AV.shorts))}
      {row("Chaussures", swatches("shoes", AV.shoes))}
      {row("Équipement", (
        <div className="seg">
          {c.gear.map((g) => (
            <button key={g.id} type="button" className={"chip" + (worn.has(g.id) ? " on" : "")} disabled={!g.unlocked}
              onClick={() => { const cur = new Set(wornGear(d, c.gear)); if (cur.has(g.id)) cur.delete(g.id); else cur.add(g.id); set({ gear: [...cur] }); }}>
              {g.unlocked ? g.label : `🔒 ${g.label} (${g.how})`}
            </button>
          ))}
        </div>
      ))}
      {row("Lieu", (
        <div className="seg" id="avatar-places">
          <button type="button" className={"chip" + (!placeSel ? " on" : "")} onClick={() => set({ place: null, ambiance: ambSel })}>Auto (selon le niveau)</button>
          {(c.places || []).map((x) => (
            <button key={x.id} type="button" data-place={x.id} className={"chip" + (placeSel === x.id ? " on" : "")} disabled={!x.unlocked}
              onClick={() => set({ place: x.id, ambiance: ambSel })}>
              {x.unlocked ? x.label : `🔒 ${x.label} (${x.how})`}
            </button>
          ))}
        </div>
      ))}
      {row("Ambiance", (
        <div className="seg" id="avatar-ambiances">
          <button type="button" className={"chip" + (!ambSel ? " on" : "")} onClick={() => set({ ambiance: null, place: placeSel })}>Auto (la saison)</button>
          {(c.ambiances || []).map((x) => (
            <button key={x.id} type="button" data-ambiance={x.id} className={"chip" + (ambSel === x.id ? " on" : "")} disabled={!x.unlocked}
              onClick={() => set({ ambiance: x.id, place: placeSel })}>
              {x.unlocked ? x.label : `🔒 ${x.label} (${x.how})`}
            </button>
          ))}
        </div>
      ))}
      {img?.enabled ? (
        <div className="muted small">
          {`L'image est redessinée au plus une fois toutes les ${img.every_h || 48} h. `
            + (img.next_at ? `Prochaine image possible le ${frDateTime(img.next_at)} : en attendant, tes changements s'affichent sur l'avatar dessiné.` : "")}
        </div>
      ) : null}
    </>;
  }

  return (
    <Sheet id="avatar-sheet" className="sheet avatar-sheet" open={open} onClose={onClose}>
      <form method="dialog" id="avatar-form" onSubmit={submit}>
        <h2>Ton avatar</h2>
        <div id="avatar-preview" className="avatar-preview">{preview}</div>
        <div id="avatar-options">{options}</div>
        <div className="sheet-actions">
          <button type="button" id="avatar-cancel" className="btn" onClick={onClose}>Annuler</button>
          <button type="submit" id="avatar-save" className="btn primary">Enregistrer</button>
        </div>
      </form>
    </Sheet>
  );
}
