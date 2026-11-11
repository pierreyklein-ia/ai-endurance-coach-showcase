import { useEffect, useState, type ReactElement } from "react";
import type { Career } from "../api/types";
import { careerLabel, hasNews, hintSeen, markHintSeen, ringDash, xpProgress } from "../lib/careerUi";
import { dayLabel } from "../lib/format";
import { AvatarPic } from "./AvatarSvg";

export interface HeaderInfo { race_name: string | null; race_date: string | null; days_to_race: number | null; first_name: string }

const R = 21;                                     // rayon de l'anneau d'XP (viewBox 48 × 48)

export function TopBar({ info, career, onboarding }: { info: HeaderInfo | null; career: Career | null; onboarding: boolean }): ReactElement {
  let name = "", sub = "", count = "";
  if (info) {
    if (!info.race_name) {
      name = "Bienvenue" + (info.first_name ? " " + info.first_name : "");
      sub = "Configurons ton coach";
    } else {
      name = info.race_name;
      sub = info.race_date ? dayLabel(info.race_date) : "";
      const d = info.days_to_race;
      count = d !== null && d > 0 ? `J-${d}` : d === 0 ? "Jour J" : "";
    }
  }
  // v0.49 : la carrière se voit : anneau d'XP, pastille « Niv. 7 », point qui pulse s'il y a du nouveau, bulle d'aide
  const [hint, setHint] = useState(false);
  const shown = !!career && !onboarding;
  useEffect(() => {
    if (!shown || hintSeen()) return;
    const t = setTimeout(() => setHint(true), 600);
    return () => clearTimeout(t);
  }, [shown]);
  const closeHint = () => { markHintSeen(); setHint(false); };
  const ping = hasNews(career);
  const ring = career ? ringDash(xpProgress(career).pct, R) : null;
  return (
    <header className="topbar">
      <a id="me-avatar" className={"me-btn" + (ping ? " ping" : "")} href="#carriere" hidden={!shown} onClick={closeHint}
        aria-label={career ? careerLabel(career) : "Ma carrière"} aria-describedby={hint ? "me-hint" : undefined}>
        {career && ring ? <>
          <span className="me-ring">
            <svg className="ring" viewBox="0 0 48 48" width="48" height="48" aria-hidden="true">
              <circle className="ring-bg" cx="24" cy="24" r={R} />
              <circle className="ring-fg" cx="24" cy="24" r={R} strokeDasharray={ring.circ.toFixed(2)}
                strokeDashoffset={ring.offset.toFixed(2)} transform="rotate(-90 24 24)" />
            </svg>
            <span className="me-av"><AvatarPic c={career} size={36} head /></span>
            {ping ? <span className="me-dot" aria-hidden="true"></span> : null}
          </span>
          <span className="me-pill" aria-hidden="true">{`Niv. ${career.level}`}</span>
        </> : null}
      </a>
      {hint && shown ? (
        <div id="me-hint" className="me-hint" role="note">
          <span>Touche ton avatar : ta carrière, tes trophées, tes réglages</span>
          <button type="button" className="me-hint-x" aria-label="Fermer l'aide" onClick={closeHint}>×</button>
        </div>
      ) : null}
      <div className="topbar-main">
        <div id="race-name" className="race-name">{name}</div>
        <div id="race-sub" className="race-sub">{sub}</div>
      </div>
      <div id="race-countdown" className="countdown">{count}</div>
    </header>
  );
}
