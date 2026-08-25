// SPDX-License-Identifier: MIT
// Copyright (c) 2026 Pablo Cano

import type { Metadata } from "next";
import Link from "next/link";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";

export const metadata: Metadata = {
  title: "Why DynosAI",
  description: "Why DynosAI exists, the problems it solves around coding agents, and the engineering principles behind its governance model.",
};

const failures = [
  ["Chat is ephemeral", "A long-lived engineering workflow should not depend on one provider conversation remaining intact."],
  ["Claims are not evidence", "An agent saying that tests pass or that only two files changed is not the same as independently checking Git and exit codes."],
  ["Scope drifts", "Agents often discover adjacent work and silently widen the change. DynosAI makes scope extension an explicit governed event."],
  ["Validation is easy to skip", "Project-specific build, test, lint, type-check, integration, or security checks need to be executable gates, not prompt suggestions."],
  ["Model cost is opaque", "More context should not automatically mean a more expensive model. DynosAI separates context pressure from capability evidence."],
  ["Brownfield truth is ambiguous", "Existing code proves current behavior, not necessarily original business intent. DynosAI keeps inferred baselines marked as inferred."],
];

export default function WhyPage() {
  return (
    <div className="mx-auto max-w-6xl px-5 py-20 lg:px-8">
      <div className="max-w-3xl">
        <p className="text-sm font-semibold uppercase tracking-[0.16em] text-muted-foreground">Why DynosAI</p>
        <h1 className="mt-3 text-4xl font-bold tracking-[-0.04em] sm:text-5xl">Coding agents are powerful. Engineering authority still needs a home.</h1>
        <p className="mt-6 text-lg leading-8 text-muted-foreground">
          DynosAI exists to move the durable parts of software delivery—requirements, scope, evidence, validation, approvals, recovery, and auditability—out of the provider chat and into a local governance runtime.
        </p>
      </div>

      <div className="mt-12 grid gap-4 md:grid-cols-2 lg:grid-cols-3">
        {failures.map(([title, description]) => (
          <Card key={title}>
            <CardHeader><CardTitle>{title}</CardTitle></CardHeader>
            <CardContent><p className="text-sm leading-6 text-muted-foreground">{description}</p></CardContent>
          </Card>
        ))}
      </div>

      <div className="mt-16 grid gap-8 rounded-3xl border border-border bg-muted/40 p-7 lg:grid-cols-2 lg:p-10">
        <div>
          <h2 className="text-2xl font-bold tracking-tight">The authority model</h2>
          <p className="mt-4 leading-7 text-muted-foreground">DynosAI coordinates three sources of truth with deliberately different authority.</p>
        </div>
        <div className="space-y-4 text-sm">
          <div className="rounded-xl border border-border bg-background p-4"><strong>SQLite</strong><p className="mt-1 text-muted-foreground">Workflow, requirements, tasks, decisions, evidence, scopes, validations, and audit history.</p></div>
          <div className="rounded-xl border border-border bg-background p-4"><strong>Git</strong><p className="mt-1 text-muted-foreground">Source-code truth and change history.</p></div>
          <div className="rounded-xl border border-border bg-background p-4"><strong>Provider session</strong><p className="mt-1 text-muted-foreground">Temporary reasoning and generation context. Useful, but not authoritative.</p></div>
        </div>
      </div>

      <div className="mt-16 max-w-3xl">
        <h2 className="text-2xl font-bold tracking-tight">What DynosAI is not</h2>
        <p className="mt-4 leading-7 text-muted-foreground">
          It is not a claim that AI can replace software engineering judgment. It is not a universal SAST platform, a universal compiler, or a guarantee of perfect architecture. It is a governance layer designed to make agent-assisted work persistent, bounded, testable, recoverable, and auditable.
        </p>
        <div className="mt-7 flex flex-wrap gap-3">
          <Button asChild><Link href="/docs/getting-started/">Get started</Link></Button>
          <Button variant="outline" asChild><Link href="/docs/architecture/">Read the architecture</Link></Button>
        </div>
      </div>
    </div>
  );
}
