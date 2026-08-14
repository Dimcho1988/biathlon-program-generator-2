import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Тренировъчен статус · onFlows",
  description: "Зонален тренировъчен статус за биатлон и спортове за издръжливост",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return <html lang="bg"><body>{children}</body></html>;
}
