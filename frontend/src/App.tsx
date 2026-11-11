import { useCallback, useEffect, useRef, useState, type ReactElement } from "react";
import { api, onUnauthorized } from "./api/client";
import type { Career as CareerT, CoachPersona, Me, Onboarding as OnboardingT, Session } from "./api/types";
import { AvatarEditor } from "./components/AvatarEditor";
import { FeedbackSheet } from "./components/FeedbackSheet";
import { Tabs } from "./components/Tabs";
import { Toast } from "./components/Toast";
import { TopBar, type HeaderInfo } from "./components/TopBar";
import { afterBack } from "./lib/backClose";
import { applyScenery } from "./lib/scenery";
import { hashView, useHashRoute } from "./lib/useHashRoute";
import { Career } from "./views/Career";
import { Coach } from "./views/Coach";
import { Details } from "./views/Details";
import { Login } from "./views/Login";
import { Onboarding } from "./views/Onboarding";
import { Progress } from "./views/Progress";
import { Simulator } from "./views/Simulator";
import { State } from "./views/State";
import { Today } from "./views/Today";
import { Week } from "./views/Week";

type Screen = "boot" | "login" | "app";

export function App(): ReactElement {
  const [screen, setScreen] = useState<Screen>("boot");
  const [me, setMe] = useState<Me | null>(null);
  const [header, setHeader] = useState<HeaderInfo | null>(null);
  const [tabsOn, setTabsOn] = useState(true);
  const [career, setCareer] = useState<CareerT | null>(null);
  const [chatEpoch, setChatEpoch] = useState(0);
  const [fbSession, setFbSession] = useState<Session | null>(null);
  const [avatarOpen, setAvatarOpen] = useState(false);
  const { view, nonce, go, viewRef } = useHashRoute();
  const meRef = useRef<Me | null>(null);
  meRef.current = me;
  useEffect(() => { applyScenery(view); }, [view]);       // v0.35 : le décor glisse d'un onglet à l'autre

  const invalidateChat = useCallback(() => setChatEpoch((n) => n + 1), []);
  const loadCareer = useCallback(() => { api<CareerT>("/api/career").then(setCareer).catch(() => {}); }, []);

  const boot = useCallback(async () => {
    let m: Me;
    try { m = await api<Me>("/api/me"); } catch { return; }
    setMe(m); setHeader(m); setScreen("app");
    if (m.onboarding) {
      const ob = await api<OnboardingT>("/api/onboarding").catch((): Pick<OnboardingT, "step"> => ({ step: "connect" }));
      if (ob.step === "connect" || ob.step === "syncing") { setTabsOn(false); go("onboarding"); return; }
      setTabsOn(true); invalidateChat();
      if (location.hash !== "#coach") { location.hash = "#coach"; return; }
      go("coach"); return;
    }
    setTabsOn(true);
    go(hashView());
    loadCareer();
  }, [go, invalidateChat, loadCareer]);

  useEffect(() => {
    onUnauthorized(() => setScreen("login"));
    void boot();
    const onVis = () => { if (!document.hidden && meRef.current) { invalidateChat(); go(viewRef.current); } };
    document.addEventListener("visibilitychange", onVis);
    return () => document.removeEventListener("visibilitychange", onVis);
  }, []);

  const logout = async () => { await api("/api/logout", { method: "POST" }).catch(() => {}); setScreen("login"); };
  const afterReset = async () => { invalidateChat(); location.hash = ""; await boot(); };
  const on = (v: string) => screen === "app" && view === v;
  const onMeChanged = (m: Me) => { setMe(m); setHeader(m); };
  const onCoach = (c: CoachPersona) => setMe((m) => (m ? { ...m, coach: c } : m));

  return <>
    <Login visible={screen === "login"} onLogged={boot} />
    <div id="app" className="app" hidden={screen !== "app"}>
      <TopBar info={header} career={career} onboarding={!!me?.onboarding} />
      <main id="main">
        <Onboarding active={on("onboarding")} nonce={nonce} onDone={() => void boot()} onCoach={onCoach} />
        <Today active={on("aujourdhui")} nonce={nonce} me={me} onHeader={setHeader} onFeedback={setFbSession} />
        <Week active={on("semaine")} nonce={nonce} onFeedback={setFbSession} onMoved={invalidateChat} />
        <Coach active={on("coach")} epoch={chatEpoch} me={me} onMeChanged={onMeChanged} />
        <Progress active={on("progres")} nonce={nonce} />
        <Simulator active={on("course")} nonce={nonce} />
        <State active={on("etat")} nonce={nonce} />
        <Career active={on("carriere")} nonce={nonce} onCareer={setCareer}
          onSeen={() => setCareer((c) => (c ? { ...c, new: [], level_up: false } : c))} onEditAvatar={() => setAvatarOpen(true)}
          settings={<Details embedded active={on("carriere")} nonce={nonce} me={me} onCoach={onCoach} onLogout={() => void logout()} onReset={() => void afterReset()} />} />
      </main>
      <Tabs view={view} hidden={!tabsOn} />
    </div>
    <AvatarEditor career={career} open={avatarOpen} onClose={() => setAvatarOpen(false)} onSaved={(avatar) => {
      setCareer((c) => (c ? { ...c, avatar, avatar_set: true } : c));
      setAvatarOpen(false);
      if (viewRef.current === "carriere") go("carriere");
    }} />
    <FeedbackSheet session={fbSession} onClose={() => setFbSession(null)} onSaved={(toChat) => {
      if (toChat) { invalidateChat(); afterBack(() => { location.hash = "#coach"; }); } else go(viewRef.current);
    }} />
    <Toast />
  </>;
}
