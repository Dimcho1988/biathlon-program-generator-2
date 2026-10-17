"use client";

import { useEffect, useId, useRef, useState, useSyncExternalStore } from "react";

type InstallChoice = { outcome: "accepted" | "dismissed"; platform: string };
type InstallPromptEvent = Event & { prompt: () => Promise<InstallChoice | void>; userChoice: Promise<InstallChoice> };
type InstallPlatform = "ios" | "safari-mac" | "firefox-windows" | "chromium" | "other";

const displayQuery = "(display-mode: standalone), (display-mode: fullscreen), (display-mode: minimal-ui)";
function isStandalone() {
  return typeof window !== "undefined" && (window.matchMedia?.(displayQuery).matches ||
    (navigator as Navigator & { standalone?: boolean }).standalone === true);
}
function subscribeDisplayMode(listener: () => void) {
  const query = window.matchMedia?.(displayQuery);
  if (!query) return () => {};
  if (typeof query.addEventListener === "function") {
    query.addEventListener("change", listener);
    return () => query.removeEventListener("change", listener);
  }
  query.addListener(listener);
  return () => query.removeListener(listener);
}
function platform(): InstallPlatform {
  const ua = navigator.userAgent;
  if (/iPhone|iPad|iPod/i.test(ua) || navigator.platform === "MacIntel" && navigator.maxTouchPoints > 1) return "ios";
  if (/Mac/i.test(ua) && /Safari/i.test(ua) && !/Chrome|Chromium|Edg|Firefox|OPR/i.test(ua)) return "safari-mac";
  if (/Windows/i.test(ua) && /Firefox/i.test(ua)) return "firefox-windows";
  if (/Chrome|Chromium|Edg|SamsungBrowser|OPR/i.test(ua)) return "chromium";
  return "other";
}
const noSubscription = () => () => {};
const serverPlatform = (): InstallPlatform => "other";
const serverStandalone = () => false;

function Instructions({ device }: { device: InstallPlatform }) {
  if (device === "ios") return <>
    <p>На iPhone или iPad отвори onFlows в Safari. Влез в акаунта си, след което избери <strong>Споделяне → Добави към началния екран → Добави</strong>.</p>
    <p>Ако има опция „Отваряне като уеб приложение“, включи я. След това отваряй onFlows от новата икона.</p>
  </>;
  if (device === "safari-mac") return <p>В Safari на Mac избери <strong>Файл → Добави към Dock</strong>, потвърди името onFlows и натисни „Добави“. Опцията е налична в macOS Sonoma или по-нова версия.</p>;
  if (device === "firefox-windows") return <p>В актуален Firefox за Windows натисни иконата за уеб приложения в адресната лента. Ако тя липсва, отвори onFlows в Chrome или Edge и избери инсталиране от менюто им.</p>;
  if (device === "chromium") return <p>Отвори менюто на браузъра <strong>⋮</strong> и избери <strong>„Инсталиране на onFlows“</strong>, „Инсталиране на приложение“ или „Добавяне към началния екран“. На компютър можеш да използваш и иконата за инсталиране в адресната лента, когато е налична.</p>;
  return <p>На компютър отвори onFlows в Chrome или Edge и избери инсталиране от менюто на браузъра. На iPhone/iPad използвай Safari → Споделяне → Добави към началния екран. На Android потърси „Инсталиране“ или „Добавяне към началния екран“ в менюто на браузъра.</p>;
}

export function InstallApp({ compact = false }: { compact?: boolean }) {
  const helpId = useId();
  const standalone = useSyncExternalStore(subscribeDisplayMode, isStandalone, serverStandalone);
  const device = useSyncExternalStore(noSubscription, platform, serverPlatform);
  const [deferred, setDeferred] = useState<InstallPromptEvent | null>(null);
  const [installed, setInstalled] = useState(false);
  const [showHelp, setShowHelp] = useState(false);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const prompting = useRef(false);

  useEffect(() => {
    const available = (event: Event) => {
      event.preventDefault();
      setDeferred(event as InstallPromptEvent);
    };
    const completed = () => { setInstalled(true); setDeferred(null); };
    window.addEventListener("beforeinstallprompt", available);
    window.addEventListener("appinstalled", completed);
    return () => {
      window.removeEventListener("beforeinstallprompt", available);
      window.removeEventListener("appinstalled", completed);
    };
  }, []);

  async function install() {
    if (prompting.current) return;
    if (!deferred) { setShowHelp(value => !value); return; }
    const event = deferred;
    setDeferred(null); setBusy(true); setMessage(""); prompting.current = true;
    try {
      // This is called only from the explicit button click. Each browser event
      // may be prompted once; acceptance alone does not prove installation.
      await event.prompt();
      const choice = await event.userChoice;
      setMessage(choice.outcome === "accepted" ? "Потвърждението е прието. Следвай указанията на браузъра." : "Можеш да добавиш onFlows по-късно от менюто на браузъра.");
      setShowHelp(true);
    } catch {
      setMessage("Инсталирането не започна. Използвай менюто на браузъра според указанията по-долу.");
      setShowHelp(true);
    } finally { prompting.current = false; setBusy(false); }
  }

  if (standalone || installed) return null;
  return <section className={`install-app${compact ? " install-app-compact" : ""}`} aria-label="onFlows като приложение">
    {!compact && <div className="install-app-heading"><strong>onFlows на телефона и компютъра</strong><p>Отваряй тренировъчния си дневник от отделна икона.</p></div>}
    <button type="button" className="install-app-button" onClick={() => void install()} disabled={busy} aria-controls={helpId} aria-expanded={showHelp}>
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M12 3v12m-4-4 4 4 4-4M5 16v4h14v-4" /></svg>
      {busy ? "Отваряне…" : deferred ? "Инсталирай onFlows" : "Добави onFlows като приложение"}
    </button>
    <div className="install-app-message" role="status" aria-live="polite">{message && <p>{message}</p>}</div>
    <div id={helpId} className="install-app-help" hidden={!showHelp}>
      <Instructions device={device}/>
      <p className="install-app-connection">Приложението използва същите данни и се нуждае от интернет.</p>
      <p className="install-app-connection">Браузърът и приложението могат да използват отделни сесии. При нужда влез отново в приложението.</p>
    </div>
  </section>;
}
