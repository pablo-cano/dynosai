// SPDX-License-Identifier: MIT
// Copyright (c) 2026 Pablo Cano

"use client";

import { useSyncExternalStore } from "react";
import { useTheme } from "next-themes";
import { Button } from "@/components/ui/button";

const subscribe = () => () => {};

export function ThemeToggle() {
  const { resolvedTheme, setTheme } = useTheme();

  const mounted = useSyncExternalStore(
    subscribe,
    () => true,
    () => false,
  );

  if (!mounted) {
    return <span className="h-9 w-9" aria-hidden="true" />;
  }

  const dark = resolvedTheme === "dark";

  return (
    <Button
      variant="ghost"
      size="sm"
      aria-label={`Switch to ${dark ? "light" : "dark"} theme`}
      onClick={() => setTheme(dark ? "light" : "dark")}
      className="w-9 px-0 text-base"
    >
      {dark ? "☀" : "☾"}
    </Button>
  );
}