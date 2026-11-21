"use client";

import { useEffect, useState } from "react";
import { SunMoon } from "lucide-react";

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
        <SunMoon size={17} strokeWidth={1.7} aria-hidden="true" />
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
