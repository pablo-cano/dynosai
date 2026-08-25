// SPDX-License-Identifier: MIT
// Copyright (c) 2026 Pablo Cano

"use client";

import { useMemo, useState } from "react";
import Link from "next/link";
import type { DocDefinition } from "@/lib/docs";

export function DocsSearch({ docs }: { docs: DocDefinition[] }) {
  const [query, setQuery] = useState("");
  const results = useMemo(() => {
    const normalized = query.trim().toLowerCase();
    if (!normalized) return [];
    return docs
      .filter((doc) => `${doc.title} ${doc.description} ${doc.group}`.toLowerCase().includes(normalized))
      .slice(0, 6);
  }, [docs, query]);

  return (
    <div className="relative w-full max-w-xl">
      <label className="sr-only" htmlFor="docs-search">Search documentation</label>
      <input
        id="docs-search"
        value={query}
        onChange={(event) => setQuery(event.target.value)}
        placeholder="Search documentation…"
        className="h-11 w-full rounded-xl border border-border bg-background px-4 text-sm outline-none transition focus:ring-2 focus:ring-ring"
      />
      {results.length > 0 && (
        <div className="absolute left-0 right-0 top-12 z-40 overflow-hidden rounded-xl border border-border bg-card shadow-xl">
          {results.map((doc) => (
            <Link
              key={doc.slug}
              href={`/docs/${doc.slug}/`}
              onClick={() => setQuery("")}
              className="block border-b border-border px-4 py-3 last:border-b-0 hover:bg-muted"
            >
              <div className="text-sm font-medium">{doc.title}</div>
              <div className="mt-1 line-clamp-1 text-xs text-muted-foreground">{doc.description}</div>
            </Link>
          ))}
        </div>
      )}
    </div>
  );
}
