// SPDX-License-Identifier: MIT
// Copyright (c) 2026 Pablo Cano

import Link from "next/link";
import { getDocsByGroup } from "@/lib/docs";
import { cn } from "@/lib/utils";

export function DocsSidebar({ activeSlug }: { activeSlug?: string }) {
  const content = (
    <nav className="space-y-7" aria-label="Documentation navigation">
      {getDocsByGroup().map(([group, entries]) => (
        <div key={group}>
          <p className="mb-2 text-xs font-semibold uppercase tracking-[0.16em] text-muted-foreground">{group}</p>
          <div className="space-y-1">
            {entries.map((doc) => (
              <Link
                key={doc.slug}
                href={`/docs/${doc.slug}/`}
                className={cn(
                  "block rounded-lg px-3 py-2 text-sm transition-colors hover:bg-muted",
                  activeSlug === doc.slug ? "bg-muted font-medium text-foreground" : "text-muted-foreground",
                )}
              >
                {doc.title}
              </Link>
            ))}
          </div>
        </div>
      ))}
    </nav>
  );

  return (
    <>
      <aside className="hidden w-64 shrink-0 border-r border-border pr-6 lg:block">{content}</aside>
      <details className="mb-6 rounded-xl border border-border bg-card p-4 lg:hidden">
        <summary className="cursor-pointer text-sm font-medium">Documentation navigation</summary>
        <div className="mt-5">{content}</div>
      </details>
    </>
  );
}
