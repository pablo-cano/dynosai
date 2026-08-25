// SPDX-License-Identifier: MIT
// Copyright (c) 2026 Pablo Cano

import { cn } from "@/lib/utils";

export function Separator({ className }: { className?: string }) {
  return <div className={cn("h-px w-full bg-border", className)} role="separator" />;
}
