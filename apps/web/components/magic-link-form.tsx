"use client";

import { FormEvent, useState } from "react";

const REQUEST_TIMEOUT_MS = 15_000;

export function MagicLinkForm({ callbackError = false }: { callbackError?: boolean }) {
  const [state, setState] = useState<"idle" | "sending" | "sent" | "error">("idle");

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setState("sending");
    const form = new FormData(event.currentTarget);
    const email = String(form.get("email") ?? "").trim();

    try {
      const response = await fetch("/api/auth/magic-link", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email }),
        signal: AbortSignal.timeout(REQUEST_TIMEOUT_MS),
      });
      setState(response.ok ? "sent" : "error");
    } catch {
      setState("error");
    }
  }

  return <form className="account-form" onSubmit={submit}>
    <label><span>Email</span><input name="email" type="email" autoComplete="email" required /></label>
    <button className="action-button" type="submit" disabled={state === "sending" || state === "sent"}>
      {state === "sending" ? "Изпращане…" : state === "sent" ? "Линкът е изпратен" : "Изпрати защитен линк"}
    </button>
    {state === "sent" && <p className="form-success" role="status">Провери email-а си и отвори линка от същото устройство.</p>}
    {state === "error" && <p className="form-error" role="alert">Линкът не беше изпратен. Провери връзката и опитай отново.</p>}
    {state === "idle" && callbackError && <p className="form-error" role="alert">Линкът е невалиден или е изтекъл. Изпрати си нов линк.</p>}
  </form>;
}
