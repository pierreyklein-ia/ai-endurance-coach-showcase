import type { ReactElement } from "react";

export function Metric({ k, v, hint, cls = "" }: { k: string; v: string | number | null | undefined; hint?: string | null; cls?: string }): ReactElement {
  return (
    <div className={`metric ${cls}`}>
      <div className="k">{k}</div>
      <div className="v">{v ?? "n/d"}</div>
      {hint ? <div className="h">{hint}</div> : null}
    </div>
  );
}
