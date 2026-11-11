/* v0.37 : « Mes objectifs » — principal, secondaires (par date), projets ; changement via le chat. */
import type { ReactElement } from "react";
import type { GoalItem } from "../api/types";
import { fmtDur } from "../lib/format";
import { countdown, goalFacts, goalWhen, groupGoals } from "../lib/goals";

function GoalLine({ g }: { g: GoalItem }): ReactElement {
  const facts = goalFacts(g);
  const cd = countdown(g);
  return (
    <li className={"goal-line" + (g.done ? " done" : "")}>
      <div>
        <div className="t">{g.name}</div>
        <div className="muted small">{[goalWhen(g), facts].filter(Boolean).join(" · ")}</div>
        {g.estimate ? <div className="small">{`≈ ${fmtDur(g.estimate[1])}`}</div> : null}
      </div>
      {cd ? <span className={"badge" + (g.done ? "" : " cd")}>{cd}</span> : null}
    </li>
  );
}

export function GoalsCard({ goals }: { goals: GoalItem[] | undefined }): ReactElement | null {
  if (!goals || !goals.length) return null;
  const { main, secondary, projects } = groupGoals(goals);
  return (
    <section className="card goals" aria-labelledby="goals-title">
      <h3 id="goals-title">Mes objectifs</h3>
      {main ? (
        <div className={"goal-main" + (main.done ? " done" : "")}>
          <div className="card-head"><span className="eyebrow">Objectif principal</span>
            {countdown(main) ? <span className="badge key">{countdown(main)}</span> : null}</div>
          <h2>{main.name}</h2>
          <div className="muted">{[goalWhen(main), goalFacts(main)].filter(Boolean).join(" · ")}</div>
          {main.estimate ? <div className="goal-est"><span className="big">{`≈ ${fmtDur(main.estimate[1])}`}</span>
            <span className="muted">{` entre ${fmtDur(main.estimate[0])} et ${fmtDur(main.estimate[2])}`}</span></div> : null}
          {!main.done ? <a className="btn small sim-link" href="#course">Simuler ma course</a> : null}
        </div>
      ) : null}
      {secondary.length ? <>
        <div className="eyebrow goal-group">{secondary.length > 1 ? "Objectifs secondaires" : "Objectif secondaire"}</div>
        <ul className="goal-list">{secondary.map((g) => <GoalLine key={g.role + g.name} g={g} />)}</ul>
      </> : null}
      {projects.length ? <>
        <div className="eyebrow goal-group">{projects.length > 1 ? "Projets" : "Projet"}</div>
        <ul className="goal-list">{projects.map((g) => <GoalLine key={g.role + g.name} g={g} />)}</ul>
      </> : null}
      <div className="muted small goal-hint">Pour changer un objectif, <a href="#coach">dis-le au coach</a>.</div>
    </section>
  );
}
