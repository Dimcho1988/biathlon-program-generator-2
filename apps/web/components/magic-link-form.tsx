"use client";

import { FormEvent, useState } from "react";
import { createClient } from "../lib/supabase/client";

export function MagicLinkForm() {
  const [state, setState] = useState<"idle" | "sending" | "sent" | "error">("idle");

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setState("sending");
    const form = new FormData(event.currentTarget);
    const email = String(form.get("email") ?? "").trim();
    const supabase = createClient();
    const { error } = await supabase.auth.signInWithOtp({
      email,
      options: { emailRedirectTo: `${window.location.origin}/auth/callback` },
    });
    setState(error ? "error" : "sent");
  }

  return <form className="account-form" onSubmit={submit}>
    <label><span>Email</span><input name="email" type="email" autoComplete="email" required /></label>
    <button className="action-button" type="submit" disabled={state === "sending" || state === "sent"}>
      {state === "sending" ? "Изпращане…" : state === "sent" ? "Линкът е изпратен" : "Изпрати защитен линк"}
    </button>
    {state === "sent" && <p className="form-success" role="status">Провери email-а си и отвори линка от същото устройство.</p>}
    {state === "error" && <p className="form-error" role="alert">Линкът не беше изпратен. Провери адреса и опитай отново.</p>}
  </form>;
}
