import type { ReactElement, ReactNode } from "react";

export const TABS: [string, string, ReactNode][] = [
  ["aujourdhui", "Aujourd'hui", <><circle cx="12" cy="12" r="4" /><path d="M12 2.5v2.5M12 19v2.5M2.5 12H5M19 12h2.5M5.3 5.3l1.8 1.8M16.9 16.9l1.8 1.8M5.3 18.7l1.8-1.8M16.9 7.1l1.8-1.8" /></>],
  ["semaine", "Semaine", <><rect x="3.5" y="5" width="17" height="15.5" rx="3" /><path d="M3.5 10h17M8 3v4M16 3v4" /></>],
  ["coach", "Coach", <path d="M5 5.5h14a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2h-7l-4.5 3.5V17.5H5a2 2 0 0 1-2-2v-8a2 2 0 0 1 2-2z" />],
  ["progres", "Progrès", <path d="M4 19.5V5M4 19.5h16M8 15l3.5-4 3 2.5L19 7.5" />],
  ["etat", "État", <path d="M3 12h4l2.5-6 4 12 2.5-6H21" />],
  ["course", "Course", <><path d="M5 21V4" /><path d="M5 4.5h12l-2.5 4 2.5 4H5" /></>],   // v0.39 : simulateur de course
];

export function Tabs({ view, hidden }: { view: string; hidden: boolean }): ReactElement {
  return (
    <nav className="tabs" aria-label="Navigation" hidden={hidden}>
      {TABS.map(([id, label, icon]) => (
        <a key={id} href={`#${id}`} data-tab={id} className={view === id ? "on" : undefined}>
          <svg className="ti" viewBox="0 0 24 24" aria-hidden="true">{icon}</svg><span>{label}</span>
        </a>
      ))}
    </nav>
  );
}
