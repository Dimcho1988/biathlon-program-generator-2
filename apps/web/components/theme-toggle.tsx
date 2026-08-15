"use client";

export const THEME_STORAGE_KEY = "onflows-theme";
export type Theme = "light" | "dark";

export function resolveInitialTheme(storedTheme: string | null, systemPrefersDark: boolean): Theme {
  return storedTheme === "light" || storedTheme === "dark"
    ? storedTheme
    : systemPrefersDark ? "dark" : "light";
}

export function applyTheme(theme: Theme, root: Pick<HTMLElement, "dataset" | "style">, storage: Pick<Storage, "setItem">) {
  root.dataset.theme = theme;
  root.style.colorScheme = theme;
  storage.setItem(THEME_STORAGE_KEY, theme);
}

export function ThemeToggle() {
  function toggleTheme() {
    const next = document.documentElement.dataset.theme === "dark" ? "light" : "dark";
    applyTheme(next, document.documentElement, localStorage);
  }

  return (
    <button className="theme-toggle" type="button" onClick={toggleTheme} aria-label="Превключи светла или тъмна тема" title="Превключи тема">
      <svg className="sun-icon" aria-hidden="true" viewBox="0 0 24 24"><circle cx="12" cy="12" r="3.5"/><path d="M12 2v2m0 16v2M4.93 4.93l1.42 1.42m11.3 11.3 1.42 1.42M2 12h2m16 0h2M4.93 19.07l1.42-1.42m11.3-11.3 1.42-1.42"/></svg>
      <svg className="moon-icon" aria-hidden="true" viewBox="0 0 24 24"><path d="M20.2 15.5A8.5 8.5 0 0 1 8.5 3.8 8.5 8.5 0 1 0 20.2 15.5Z"/></svg>
    </button>
  );
}
