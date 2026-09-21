"use client";

import Image from "next/image";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useRef, useState, type ReactNode } from "react";
import { ThemeToggle } from "./theme-toggle";
import { rememberedNavigationHref } from "../lib/dashboard-navigation";

const sections = [
  { href: "/", label: "Общ статус", icon: "overview" },
  { href: "/activities", label: "Активности", icon: "calendar" },
  { href: "/trainability", label: "Индекс на тренираност", icon: "trend" },
  { href: "/speed", label: "Скорост и време", icon: "speed" },
  { href: "/response", label: "Стрес и възстановяване", icon: "response" },
  { href: "/planning", label: "Профил за планиране", icon: "overview" },
  { href: "/management", label: "Седмична програма", icon: "calendar" },
  { href: "/management/outlook", label: "Дългосрочен план", icon: "trend" },
] as const;

function NavIcon({ name }: { name: string }) {
  const paths: Record<string, ReactNode> = {
    overview: <><rect x="3" y="3" width="7" height="7" rx="1.5" /><rect x="14" y="3" width="7" height="7" rx="1.5" /><rect x="3" y="14" width="7" height="7" rx="1.5" /><rect x="14" y="14" width="7" height="7" rx="1.5" /></>,
    calendar: <><rect x="3" y="5" width="18" height="16" rx="2" /><path d="M7 3v4m10-4v4M3 11h18m-13 4h2m4 0h2" /></>,
    trend: <><path d="M3 4v16h18M6 15l5-5 4 3 6-8m-5 0h5v5" /></>,
    speed: <><path d="M5 19a9 9 0 1 1 14 0M12 12l4-4M7 17h10" /><circle cx="12" cy="12" r="1" /></>,
    response: <><path d="M3 12h4l3-7 4 14 3-7h4" /></>,
  };
  return <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">{paths[name]}</svg>;
}

export function AppShell({ children, athlete }: { children: ReactNode; athlete: ReactNode }) {
  const pathname = usePathname();
  const router = useRouter();
  const [menuOpen, setMenuOpen] = useState(false);
  const toggleRef = useRef<HTMLButtonElement>(null);
  if (pathname === "/login" || pathname.startsWith("/auth/")) return <>{children}</>;
  const closeMenu = () => setMenuOpen(false);
  const navigate = (href: string, event: { preventDefault: () => void }) => {
    closeMenu();
    try {
      const prefix = "onflows-view:";
      const current = `${window.location.pathname}${window.location.search}`;
      sessionStorage.setItem(`${prefix}${pathname}`, rememberedNavigationHref(pathname, current));
      const destination = rememberedNavigationHref(href, sessionStorage.getItem(`${prefix}${href}`));
      if (destination !== href) { event.preventDefault(); router.push(destination); }
    } catch { /* Navigation also works when browser storage is disabled. */ }
  };
  return <div className="workspace-shell">
    <a className="skip-to-content" href="#workspace-content">Към съдържанието</a>
    <aside className="workspace-sidebar" onKeyDown={(event) => {
      if (event.key === "Escape" && menuOpen) { closeMenu(); toggleRef.current?.focus(); }
    }}>
      <div className="workspace-brand-row">
        <Link className="brand" href="/" onClick={closeMenu} aria-label="onFlows начало"><Image src="/brand/onflows-mark.png" width={33} height={40} alt="onFlows лого" priority /><span>onFlows</span></Link>
        <button ref={toggleRef} className="menu-toggle" type="button" aria-expanded={menuOpen} aria-controls="workspace-navigation" onClick={() => setMenuOpen(!menuOpen)}>
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" aria-hidden="true">{menuOpen ? <path d="m6 6 12 12M6 18 18 6" /> : <path d="M4 6h16M4 12h16M4 18h16" />}</svg><span>{menuOpen ? "Затвори" : "Меню"}</span>
        </button>
      </div>
      <div id="workspace-navigation" className={`workspace-navigation${menuOpen ? " is-open" : ""}`}>
        <p className="workspace-label">Тренировъчен дневник</p>
        <nav className="workspace-links" aria-label="Основна навигация">
          {sections.map(({ href, label, icon }) => <Link key={href} href={href} prefetch={false} aria-current={pathname === href || (href !== "/" && href !== "/management" && pathname.startsWith(`${href}/`)) ? "page" : undefined} onNavigate={(event) => navigate(href, event)}><NavIcon name={icon} /><span>{label}</span></Link>)}
        </nav>
        <div className="workspace-athlete" onClick={(event) => { if ((event.target as HTMLElement).closest("a")) closeMenu(); }}>{athlete}</div>
        <div className="workspace-tools"><Link href="/account" prefetch={false} onClick={closeMenu} aria-current={pathname === "/account" ? "page" : undefined}>Акаунт и спортисти</Link><ThemeToggle /></div>
      </div>
    </aside>
    <div id="workspace-content" className="workspace-content" tabIndex={-1}>{children}</div>
  </div>;
}
