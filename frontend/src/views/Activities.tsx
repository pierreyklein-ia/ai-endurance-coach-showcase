/* v0.23 : liste des activités avec recherche, filtre par sport et « voir plus » */
import { useEffect, useRef, useState, type ReactElement } from "react";
import { api } from "../api/client";
import type { Activity, ActivitiesPage } from "../api/types";
import { ActRow } from "../components/ActRow";

// filtre et recherche gardés d'un affichage de Progrès à l'autre (comme state.actsSport / actsQuery)
const kept = { sport: "", query: "" };

export function Activities(): ReactElement {
  const [rows, setRows] = useState<Activity[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [sports, setSports] = useState<string[] | null>(null);
  const [sport, setSport] = useState(kept.sport);
  const [query, setQuery] = useState(kept.query);
  const [more, setMore] = useState<{ next: number | null; total: number }>({ next: null, total: 0 });
  const offset = useRef(0);
  const ticket = useRef(0);
  const debounce = useRef<ReturnType<typeof setTimeout>>();

  const load = async (first: boolean, sp = sport, q = query) => {
    if (first) offset.current = 0;
    const params = new URLSearchParams({ limit: String(first ? 5 : 20), offset: String(offset.current) });   // v0.23 : 5 par défaut
    if (sp) params.set("sport", sp);
    if (q) params.set("q", q);
    const t = ++ticket.current;
    let r: ActivitiesPage;
    try { r = await api<ActivitiesPage>(`/api/activities?${params}`); } catch { return; }
    if (t !== ticket.current) return;                              // une recherche plus récente est partie
    setSports((cur) => cur ?? r.sports);
    setRows((cur) => (first ? r.activities : [...cur, ...r.activities]));
    setLoaded(true);
    offset.current = r.next_offset ?? offset.current + r.activities.length;
    setMore({ next: r.next_offset ?? null, total: r.total });
  };
  useEffect(() => { void load(true); }, []);   // une liste neuve à chaque affichage de Progrès
  useEffect(() => () => clearTimeout(debounce.current), []);

  return (
    <div id="acts-list">
      {sports !== null ? <>
        <div className="acts-tools">
          <input type="search" className="acts-search" placeholder="Rechercher : nom, mois, « 50 km »…" aria-label="Rechercher une activité"
            defaultValue={query} onInput={(e) => {
              const v = e.currentTarget.value;
              clearTimeout(debounce.current);
              debounce.current = setTimeout(() => { const q = v.trim(); kept.query = q; setQuery(q); void load(true, sport, q); }, 250);
            }} />
          {sports.length > 1 ? (
            <div className="seg">
              {["", ...sports].map((s) => (
                <button key={s} className={"chip" + (s === sport ? " on" : "")} data-sport={s}
                  onClick={() => { kept.sport = s; setSport(s); void load(true, s, query); }}>{s || "Toutes"}</button>
              ))}
            </div>
          ) : null}
        </div>
        <div className="acts-rows">
          {loaded && !rows.length ? <div className="muted small">Aucune activité trouvée.</div> : null}
          {rows.map((a, i) => <ActRow key={i} a={a} />)}
        </div>
        {more.next !== null ? <button className="btn small more" onClick={() => void load(false)}>{`Voir plus (${more.total - offset.current} autres)`}</button>
          : more.total > 5 ? <div className="muted small acts-count">{`${more.total} activités`}</div> : null}
      </> : null}
    </div>
  );
}
