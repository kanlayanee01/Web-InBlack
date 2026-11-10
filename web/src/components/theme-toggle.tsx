"use client";

import { useEffect, useState } from "react";

import { MoonIcon, SunIcon } from "./icons";
import { THEME_KEY } from "@/lib/theme";

type Theme = "light" | "dark";

function currentTheme(): Theme {
  const chosen = document.documentElement.dataset.theme;
  if (chosen === "light" || chosen === "dark") return chosen;
  return window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

export function ThemeToggle({ toDark, toLight }: { toDark: string; toLight: string }) {
  const [theme, setTheme] = useState<Theme | null>(null);

  useEffect(() => {
    setTheme(currentTheme());
  }, []);

  const dark = theme === "dark";
  return (
    <button
      type="button"
      className="theme-toggle"
      aria-label={dark ? toLight : toDark}
      onClick={() => {
        const next: Theme = dark ? "light" : "dark";
        document.documentElement.dataset.theme = next;
        try {
          localStorage.setItem(THEME_KEY, next);
        } catch {
          // Without storage the choice still applies until the page is closed.
        }
        setTheme(next);
      }}
    >
      {dark ? <SunIcon /> : <MoonIcon />}
    </button>
  );
}
