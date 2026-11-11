import type { ReactElement } from "react";
import { TIER } from "../lib/avatar";

export function Medal({ tier, done }: { tier: number; done: boolean }): ReactElement {
  const col = TIER[tier][1];
  return (
    <svg viewBox="0 0 24 24" width={30} height={30} className={"medal" + (done ? "" : " off")} aria-hidden="true">
      <path d="M7 2h4l2 6-4 1z M17 2h-4l-2 6 4 1z" fill={done ? "#ef4444" : "currentColor"} opacity={done ? 1 : 0.35} />
      <circle cx={12} cy={15} r={7} fill={done ? col : "none"} stroke={done ? "#0003" : "currentColor"} strokeWidth={1.5} opacity={done ? 1 : 0.45} />
      {done ? <path d="M12 11.2l1.2 2.4 2.6.4-1.9 1.8.5 2.6-2.4-1.3-2.4 1.3.5-2.6-1.9-1.8 2.6-.4z" fill="#fff" opacity=".9" /> : null}
    </svg>
  );
}
