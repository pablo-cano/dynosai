// SPDX-License-Identifier: MIT
// Copyright (c) 2026 Pablo Cano

import { cn } from "@/lib/utils";

/**
 * DynosAI's brand mark: a governed flow inside a D-shaped boundary.
 *
 * The outer form represents the durable control plane. The connected nodes
 * represent specification, execution, and evidence moving through one
 * governed workflow rather than living only in an agent chat.
 */
export function DynosAILogo({ className }: { className?: string }) {
  return (
    <svg
      aria-hidden="true"
      className={cn("shrink-0", className)}
      viewBox="0 0 64 64"
      fill="none"
      xmlns="http://www.w3.org/2000/svg"
    >
      <rect x="4" y="4" width="56" height="56" rx="17" className="fill-foreground" />
      <path
        d="M19 16V48H29.5C40.8 48 48 41.7 48 32C48 22.3 40.8 16 29.5 16H19Z"
        className="stroke-background"
        strokeWidth="4.5"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
      <path
        d="M20 23.5H29L35 32L29 40.5H20"
        className="stroke-accent"
        strokeWidth="3.5"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
      <circle cx="20" cy="23.5" r="2.5" className="fill-accent" />
      <circle cx="35" cy="32" r="2.5" className="fill-accent" />
      <circle cx="20" cy="40.5" r="2.5" className="fill-accent" />
    </svg>
  );
}
