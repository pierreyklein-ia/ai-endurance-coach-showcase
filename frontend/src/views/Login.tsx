import { useEffect, useRef, useState, type FormEvent, type ReactElement } from "react";
import { api } from "../api/client";

export function Login({ visible, onLogged }: { visible: boolean; onLogged: () => Promise<void> }): ReactElement {
  const [error, setError] = useState("");
  const input = useRef<HTMLInputElement>(null);
  useEffect(() => { if (visible) input.current?.focus(); }, [visible]);
  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setError("");
    try {
      await api("/api/login", { json: { passcode: input.current?.value ?? "" } });
      if (input.current) input.current.value = "";
      await onLogged();
    } catch (err) { setError((err as Error).message); }
  };
  return (
    <section id="login" className="login" hidden={!visible}>
      <img src="/static/icon.svg" alt="" className="login-logo" />
      <h1>AI Endurance Coach</h1>
      <p className="muted">Entre ton code d'accès.</p>
      <form id="login-form" className="login-form" onSubmit={submit}>
        <input id="passcode" ref={input} type="password" autoComplete="current-password" placeholder="Code d'accès" required />
        <button type="submit" className="btn primary">Entrer</button>
        <p id="login-error" className="error" role="alert">{error}</p>
      </form>
    </section>
  );
}
