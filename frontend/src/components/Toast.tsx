import { useEffect, useState, type ReactElement } from "react";
import { onToast } from "../lib/toast";

export function Toast(): ReactElement {
  const [msg, setMsg] = useState<string | null>(null);
  const [text, setText] = useState("");
  useEffect(() => {
    onToast((m) => { if (m !== null) setText(m); setMsg(m); });
    return () => onToast(null);
  }, []);
  return <div id="toast" className="toast" role="status" hidden={msg === null}>{text}</div>;
}
