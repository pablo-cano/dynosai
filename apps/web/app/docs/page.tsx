// SPDX-License-Identifier: MIT
// Copyright (c) 2026 Pablo Cano

import type { Metadata } from "next";
import Link from "next/link";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { DocsSearch } from "@/components/docs-search";
import { docs, getDocsByGroup } from "@/lib/docs";

export const metadata: Metadata = {
  title: "Documentation",
  description: "DynosAI documentation: getting started, user guide, architecture, validation, model control, brownfield, providers, observability, and reference.",
};

export default function DocsHomePage() {
  return (
    <div className="mx-auto max-w-7xl px-5 py-16 lg:px-8">
      <div className="max-w-3xl">
        <p className="text-sm font-semibold uppercase tracking-[0.16em] text-muted-foreground">Documentation</p>
        <h1 className="mt-3 text-4xl font-bold tracking-[-0.04em] sm:text-5xl">Understand the workflow, then trust the evidence.</h1>
        <p className="mt-5 text-lg leading-8 text-muted-foreground">Installation, workflow, architecture, validation, provider behavior, model control, observability, and reference material for the current public beta.</p>
        <div className="mt-7"><DocsSearch docs={docs} /></div>
      </div>

      <div className="mt-12 space-y-12">
        {getDocsByGroup().map(([group, entries]) => (
          <section key={group}>
            <h2 className="mb-4 text-sm font-semibold uppercase tracking-[0.16em] text-muted-foreground">{group}</h2>
            <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-3">
              {entries.map((doc) => (
                <Link key={doc.slug} href={`/docs/${doc.slug}/`} className="group">
                  <Card className="h-full transition group-hover:-translate-y-0.5 group-hover:shadow-md">
                    <CardHeader><CardTitle>{doc.title}</CardTitle></CardHeader>
                    <CardContent><p className="text-sm leading-6 text-muted-foreground">{doc.description}</p></CardContent>
                  </Card>
                </Link>
              ))}
            </div>
          </section>
        ))}
      </div>
    </div>
  );
}
