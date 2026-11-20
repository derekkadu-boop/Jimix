"use client";

import { useEffect, useState } from "react";

const themeKey = "watchtower.theme";
const themes = [
  ["dark", "Dark"],
  ["light", "Light"],
  ["green-light", "Light green"],
  ["green-dark", "Dark green"],
];

export default function ThemeToggle() {
  const [theme, setTheme] = useState("dark");

  useEffect(() => {
    try {
      const saved = localStorage.getItem(themeKey);
      if (themes.some(([value]) => value === saved)) {
        document.documentElement.dataset.theme = saved;
        setTheme(saved);
      }
    } catch {}
  }, []);

  function selectTheme(event, value) {
    document.documentElement.dataset.theme = value;
    setTheme(value);
    try { localStorage.setItem(themeKey, value); } catch {}
    const menu = event.currentTarget.closest("details");
    menu.open = false;
    menu.querySelector("summary").focus();
  }

  return (
    <details className="theme-picker" onKeyDown={event => {
      if (event.key === "Escape" && event.currentTarget.open) {
        event.currentTarget.open = false;
        event.currentTarget.querySelector("summary").focus();
      }
    }}>
      <summary aria-label="Choose appearance" title="Choose appearance">
        <svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.65" strokeLinecap="round" aria-hidden="true"><circle cx="12" cy="12" r="4" /><path d="M12 2v2m0 16v2M2 12h2m16 0h2M5 5l1.4 1.4m11.2 11.2L19 19m0-14-1.4 1.4M6.4 17.6 5 19" /></svg>
      </summary>
      <div className="theme-options" role="group" aria-label="Appearance themes">
        {themes.map(([value, label]) => (
          <button key={value} type="button" aria-pressed={theme === value}
            onClick={event => selectTheme(event, value)}>
            {label}
          </button>
        ))}
      </div>
    </details>
  );
}
