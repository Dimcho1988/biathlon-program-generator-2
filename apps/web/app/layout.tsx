import type { Metadata } from "next";
import "./globals.css";
import { WakeMarkerCleaner } from "../components/wake-marker-cleaner";

export const metadata: Metadata = {
  title: "Тренировъчен статус · onFlows",
  description: "Зонален тренировъчен статус за биатлон и спортове за издръжливост",
};

const themeScript = `(function(){try{var k='onflows-theme',s=localStorage.getItem(k),t=s==='light'||s==='dark'?s:(matchMedia('(prefers-color-scheme: dark)').matches?'dark':'light');document.documentElement.dataset.theme=t;document.documentElement.style.colorScheme=t}catch(e){document.documentElement.dataset.theme=matchMedia('(prefers-color-scheme: dark)').matches?'dark':'light'}})()`;

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return <html lang="bg" suppressHydrationWarning><head><script dangerouslySetInnerHTML={{ __html: themeScript }} /></head><body><WakeMarkerCleaner />{children}</body></html>;
}
