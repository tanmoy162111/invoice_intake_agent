"use client";

import { Monitor, Moon, Sun } from "lucide-react";
import * as React from "react";

import { Button } from "@/components/ui/button";

type Theme = "system" | "light" | "dark";
const NEXT: Record<Theme, Theme> = { system: "light", light: "dark", dark: "system" };
const EVENT = "themechange";

function read(): Theme {
  const m = /(?:^|; )theme=(light|dark)/.exec(document.cookie);
  return (m?.[1] as Theme | undefined) ?? "system";
}

function subscribe(onChange: () => void) {
  window.addEventListener(EVENT, onChange);
  return () => window.removeEventListener(EVENT, onChange);
}

/** Light, dark, or follow the device. The choice is remembered in a cookie so the server can render it. */
export function ThemeToggle() {
  const theme = React.useSyncExternalStore<Theme>(subscribe, read, () => "system");

  function cycle() {
    const next = NEXT[theme];
    document.cookie = `theme=${next}; path=/; max-age=31536000; samesite=lax`;
    document.documentElement.classList.remove("light", "dark");
    if (next !== "system") document.documentElement.classList.add(next);
    window.dispatchEvent(new Event(EVENT));
  }

  const Icon = theme === "dark" ? Moon : theme === "light" ? Sun : Monitor;
  return (
    <Button variant="ghost" size="icon" onClick={cycle} aria-label={`Theme: ${theme}. Change theme`}>
      <Icon />
    </Button>
  );
}
