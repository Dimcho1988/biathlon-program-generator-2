import type { Metadata, Viewport } from "next";
import "./globals.css";
import { WakeMarkerCleaner } from "../components/wake-marker-cleaner";
import { Suspense } from "react";
import { AppShell } from "../components/app-shell";
import { AthleteNavigation } from "../components/athlete-navigation";
import "./workspace.css";
import "./auth.css";
import "./install.css";
import "./readability.css";

export const metadata: Metadata = {
  title: "Тренировъчен статус · onFlows",
  description: "Зонален тренировъчен статус за биатлон и спортове за издръжливост",
  applicationName: "onFlows",
  appleWebApp: { capable: true, title: "onFlows", statusBarStyle: "default" },
};

export const viewport: Viewport = {
  width: "device-width", initialScale: 1, viewportFit: "cover", themeColor: [{ media: "(prefers-color-scheme: light)", color: "#ffffff" }, { media: "(prefers-color-scheme: dark)", color: "#07111d" }],
};

const themeScript = `(function(){try{var k='onflows-theme',s=localStorage.getItem(k),t=s==='light'||s==='dark'?s:(matchMedia('(prefers-color-scheme: dark)').matches?'dark':'light');document.documentElement.dataset.theme=t;document.documentElement.style.colorScheme=t}catch(e){document.documentElement.dataset.theme=matchMedia('(prefers-color-scheme: dark)').matches?'dark':'light'}})()`;

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return <html lang="bg" suppressHydrationWarning><head><script dangerouslySetInnerHTML={{ __html: themeScript }} /></head><body><WakeMarkerCleaner /><AppShell athlete={<Suspense fallback={<p className="workspace-profile-name">Зареждане на профила…</p>}><AthleteNavigation /></Suspense>}>{children}</AppShell></body></html>;
}
