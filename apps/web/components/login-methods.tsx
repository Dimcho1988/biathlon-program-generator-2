"use client";

import { FormEvent, useState } from "react";
import { MagicLinkForm } from "./magic-link-form";

export function LoginMethods({ callbackError = false, reauthenticate = false }: { callbackError?: boolean; reauthenticate?: boolean }) {
  const [method, setMethod] = useState<"password" | "email">(callbackError || reauthenticate ? "email" : "password");
  const [state, setState] = useState<"idle" | "sending" | "invalid" | "rate-limited" | "error">("idle");
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (state === "sending") return;
    setState("sending");
    const form = new FormData(event.currentTarget);
    try {
      const response = await fetch("/api/auth/password", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email: String(form.get("email") ?? "").trim(), password: String(form.get("password") ?? "") }),
        signal: AbortSignal.timeout(15_000),
      });
      // A full navigation discards any pre-login RSC snapshot after cookies change.
      // eslint-disable-next-line @next/next/no-location-assign-relative-destination
      if (response.ok) { window.location.assign("/account"); return; }
      setState(response.status === 429 ? "rate-limited" : response.status === 401 || response.status === 400 ? "invalid" : "error");
    } catch { setState("error"); }
  }
  return <div className="login-methods">
    <div className="login-method-options" aria-label="Начин на вход">
      <button type="button" aria-pressed={method === "password"} onClick={() => setMethod("password")}>С парола</button>
      <button type="button" aria-pressed={method === "email"} onClick={() => setMethod("email")}>С email линк</button>
    </div>
    {method === "email" ? <MagicLinkForm callbackError={callbackError} /> : <form className="account-form" onSubmit={submit}>
      <label><span>Email</span><input name="email" type="email" autoComplete="username" maxLength={254} required /></label>
      <label><span>Парола</span><input name="password" type="password" autoComplete="current-password" maxLength={1024} required /></label>
      <button className="action-button" type="submit" disabled={state === "sending"}>{state === "sending" ? "Влизане…" : "Влез с парола"}</button>
      {state === "invalid" && <p className="form-error" role="alert">Email-ът или паролата са невалидни. Ако още нямаш парола, влез с email линк.</p>}
      {state === "rate-limited" && <p className="form-error" role="alert">Твърде много опити. Изчакай малко и опитай отново.</p>}
      {state === "error" && <p className="form-error" role="alert">Входът временно не е достъпен. Опитай отново.</p>}
      <button className="auth-text-button" type="button" onClick={() => setMethod("email")}>Първи вход или забравена парола? Използвай email линк.</button>
    </form>}
  </div>;
}
