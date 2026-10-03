"use client";

import Link from "next/link";
import { FormEvent, useState } from "react";
import { validNewPassword } from "../lib/password-auth";

const messages: Record<string, string> = {
  invalid: "Използвай поне 12 символа. Максимумът е 72 латински или 36 кирилски букви.",
  mismatch: "Двете нови пароли не съвпадат.",
  weak: "Паролата не отговаря на изискванията за сигурност. Избери по-силна парола.",
  same: "Новата парола трябва да е различна от текущата.",
  "current-password": "Въведи правилната текуща парола и опитай отново.",
  "rate-limited": "Твърде много опити. Изчакай малко и опитай отново.",
  unavailable: "Паролата не беше запазена. Провери връзката и опитай отново.",
};

export function PasswordSettings() {
  const [state, setState] = useState("idle");
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (state === "saving") return;
    const element = event.currentTarget;
    const form = new FormData(element);
    const password = String(form.get("password") ?? "");
    if (password !== form.get("confirmation")) { setState("mismatch"); return; }
    if (!validNewPassword(password)) { setState("invalid"); return; }
    setState("saving");
    try {
      const response = await fetch("/api/account/password", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ password, currentPassword: String(form.get("current_password") ?? "") }),
        signal: AbortSignal.timeout(25_000),
      });
      const body = await response.json() as { reason?: string };
      if (response.ok) { element.reset(); setState("saved"); }
      else setState(body.reason && (body.reason in messages || body.reason === "reauthenticate") ? body.reason : "unavailable");
    } catch { setState("unavailable"); }
  }
  return <section className="role-section auth-settings">
    <h2>Вход и сигурност</h2>
    <p>Входът се запазва на това устройство и се подновява автоматично. На лично устройство можеш да отваряш onFlows от иконата. На споделен компютър използвай „Изход от това устройство“.</p>
    <details><summary>Задай или смени парола</summary>
      <form className="account-form" onSubmit={submit}>
        <p>Паролата е незадължителна. При забравена парола влез с email линк и задай нова тук. За промяната е нужен вход през последните 24 часа.</p>
        <label><span>Текуща парола, ако се изисква</span><input name="current_password" type="password" autoComplete="current-password" maxLength={1024} /></label>
        <label><span>Нова парола</span><input name="password" type="password" autoComplete="new-password" minLength={12} maxLength={72} required aria-describedby="password-guidance" /></label>
        <label><span>Повтори новата парола</span><input name="confirmation" type="password" autoComplete="new-password" minLength={12} maxLength={72} required /></label>
        <small id="password-guidance">Поне 12 символа. Използвай уникална парола или мениджър на пароли. Максимум 72 латински или 36 кирилски букви.</small>
        <button className="action-button" type="submit" disabled={state === "saving"}>{state === "saving" ? "Запазване…" : "Запази паролата"}</button>
        {state === "saved" && <p className="form-success" role="status">Паролата е запазена. Вече можеш да влизаш с email и парола. Email линкът остава достъпен.</p>}
        {messages[state] && <p className="form-error" role="alert">{messages[state]}</p>}
        {state === "reauthenticate" && <p className="form-error" role="alert">Потвърди входа си отново, преди да смениш паролата. <Link href="/login?reauth=1" prefetch={false}>Влез с email линк</Link></p>}
      </form>
    </details>
  </section>;
}
