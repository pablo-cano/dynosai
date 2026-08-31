// SPDX-License-Identifier: MIT
// Copyright (c) 2026 Pablo Cano

import Link from "next/link";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { DynosAILogo } from "@/components/dynosai-logo";
import { FlowDiagram } from "@/components/flow-diagram";
import { Separator } from "@/components/ui/separator";
import { siteConfig } from "@/lib/site";

const capabilities = [
  ["Durable workflow state", "Requirements, acceptance criteria, tasks, decisions, evidence, scopes, and gates live outside the provider chat."],
  ["Governed scope", "DynosAI compares planned files and actions with the actual Git diff instead of trusting the agent's description."],
  ["Independent verification", "An agent cannot simply mark its own work verified. Repository state and recorded evidence are checked by the Core."],
  ["Validation profiles", "Run governed unit, lint, type-check, build, integration, security, or project-specific commands and store real outcomes."],
  ["Brownfield support", "Index existing code, symbols, tests, and relationships to build an evidence-backed inferred baseline."],
  ["Governed teams", "Approved plans become serial or parallel leases only when files do not overlap. Extra coding agents are not spawned."],
  ["Model control", "Track phase budgets, complexity, failures, context pressure, route candidates, and historical recommendations without reckless escalation."],
];

const evidence = [
  ["4 / 4", "provider × project matrix"],
  ["100", "workflow quality score"],
  ["0", "MCP failures"],
  ["0", "scope leaks"],
];

export default function HomePage() {
  return (
    <>
      <section className="relative overflow-hidden border-b border-border">
        <div className="hero-grid absolute inset-0 -z-10 opacity-70" />
        <div className="mx-auto grid max-w-7xl gap-12 px-5 py-20 lg:grid-cols-[1fr_0.42fr] lg:items-center lg:px-8 lg:py-28">
          <div className="max-w-4xl">
            <div className="mb-6 flex flex-wrap items-center gap-2">
              <Badge>Open source · MIT</Badge>
              <Badge>β Public beta</Badge>
              <Badge>Runs locally</Badge>
            </div>
            <h1 className="max-w-5xl text-5xl font-bold tracking-[-0.055em] sm:text-6xl lg:text-7xl">
              Governed software development for coding agents.
            </h1>
            <p className="mt-7 max-w-3xl text-lg leading-8 text-muted-foreground sm:text-xl">
              The agent writes code. DynosAI governs what may change, what must be proven, and when the work is actually done.
            </p>
            <div className="mt-9 flex flex-wrap gap-3">
              <Button size="lg" asChild><Link href="/docs/getting-started/">Get started</Link></Button>
              <Button size="lg" variant="outline" asChild><a href={siteConfig.github} target="_blank" rel="noreferrer">View on GitHub ↗</a></Button>
            </div>
            <p className="mt-5 text-sm text-muted-foreground">
              v{siteConfig.version} · Python + MCP · Local Studio · Codex and Cursor
            </p>
          </div>

          <div className="hidden justify-self-end lg:block" aria-hidden="true">
            <div className="relative grid h-64 w-64 place-items-center rounded-[3rem] border border-border bg-background/75 shadow-2xl shadow-foreground/5 backdrop-blur">
              <div className="absolute inset-5 rounded-[2.25rem] border border-dashed border-border" />
              <DynosAILogo className="h-36 w-36" />
            </div>
          </div>
        </div>
      </section>

      <section className="border-b border-border bg-muted/35">
        <div className="mx-auto max-w-7xl px-5 py-14 lg:px-8">
          <div className="grid gap-8 lg:grid-cols-[0.72fr_1.28fr] lg:items-center">
            <div>
              <p className="text-sm font-semibold uppercase tracking-[0.16em] text-muted-foreground">Quick start</p>
              <h2 className="mt-3 text-3xl font-bold tracking-tight">From source to a governed agent workflow.</h2>
              <p className="mt-4 leading-7 text-muted-foreground">Clone the beta, install it locally, configure Codex or Cursor once, then work with the provider normally.</p>
            </div>
            <div className="overflow-x-auto rounded-2xl border border-border bg-[#0d1117] p-6 font-mono text-sm leading-7 text-[#e6edf3] shadow-sm">
              <div><span className="text-[#8b949e]">$</span> git clone https://github.com/pablo-cano/dynosai.git</div>
              <div><span className="text-[#8b949e]">$</span> cd dynosai</div>
              <div><span className="text-[#8b949e]">$</span> python -m pip install -e .</div>
              <div><span className="text-[#8b949e]">$</span> dynosai setup --provider all</div>
              <div className="mt-4 text-[#8b949e]"># Then describe normal engineering work to the coding agent.</div>
            </div>
          </div>
        </div>
      </section>

      <section className="mx-auto max-w-7xl px-5 py-20 lg:px-8">
        <div className="grid gap-12 lg:grid-cols-[0.8fr_1.2fr] lg:items-start">
          <div>
            <p className="text-sm font-semibold uppercase tracking-[0.16em] text-muted-foreground">Why DynosAI</p>
            <h2 className="mt-3 text-3xl font-bold tracking-tight sm:text-4xl">A chat transcript is not an engineering control plane.</h2>
            <p className="mt-5 text-base leading-7 text-muted-foreground">
              Coding agents can forget context, overstate changes, skip validation, silently broaden scope, or lose state when a session restarts. DynosAI turns the session into a persistent, evidence-driven workflow where Git and durable project state—not the chat—are authoritative.
            </p>
            <Button variant="outline" className="mt-6" asChild><Link href="/why/">Read why DynosAI exists</Link></Button>
          </div>
          <FlowDiagram />
        </div>
      </section>

      <Separator />

      <section className="mx-auto max-w-7xl px-5 py-20 lg:px-8">
        <div className="max-w-2xl">
          <p className="text-sm font-semibold uppercase tracking-[0.16em] text-muted-foreground">What it provides</p>
          <h2 className="mt-3 text-3xl font-bold tracking-tight sm:text-4xl">Governance around the agent, not another agent wrapper.</h2>
        </div>
        <div className="mt-10 grid gap-4 md:grid-cols-2 lg:grid-cols-3">
          {capabilities.map(([title, description]) => (
            <Card key={title} className="h-full">
              <CardHeader><CardTitle>{title}</CardTitle></CardHeader>
              <CardContent><CardDescription>{description}</CardDescription></CardContent>
            </Card>
          ))}
        </div>
      </section>

      <section className="border-y border-border bg-muted/45">
        <div className="mx-auto grid max-w-7xl gap-10 px-5 py-20 lg:grid-cols-[0.9fr_1.1fr] lg:items-center lg:px-8">
          <div>
            <Badge>New in 0.17.0</Badge>
            <h2 className="mt-4 text-3xl font-bold tracking-tight sm:text-4xl">Eval Intelligence: failures become cases, not autonomous routing.</h2>
            <p className="mt-5 leading-7 text-muted-foreground">0.17 keeps Studio, the 0.15 harness and 0.16 team leases. Local failures are attributed and mined into bounded eval cases. Improvement work stays in inbox. Predictive routing stays in shadow mode.</p>
            <div className="mt-6 flex flex-wrap gap-3"><Button asChild><Link href="/studio/">Explore Local Studio</Link></Button><Button variant="outline" asChild><Link href="/roadmap/">View roadmap</Link></Button></div>
          </div>
          <div className="rounded-2xl border border-border bg-background p-6 shadow-sm">
            <div className="grid gap-3 sm:grid-cols-2">
              {[["Project hub","Create + open"],["Languages","English + Spanish"],["Providers","Codex + Cursor"],["Tutorial","Contextual Fibonacci"]].map(([label,value]) => <div key={label} className="rounded-xl border border-border bg-muted/35 p-4"><div className="text-xs text-muted-foreground">{label}</div><div className="mt-1 text-lg font-semibold">{value}</div></div>)}
            </div>
            <div className="mt-4 rounded-xl border border-border p-4"><div className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">Simple path</div><div className="mt-2 font-medium">Home → choose project → Overview → New change → Work / Approvals</div></div>
          </div>
        </div>
      </section>

      <section className="border-y border-border bg-muted/50">
        <div className="mx-auto max-w-7xl px-5 py-20 lg:px-8">
          <div className="grid gap-10 lg:grid-cols-2 lg:items-center">
            <div>
              <p className="text-sm font-semibold uppercase tracking-[0.16em] text-muted-foreground">What DynosAI actually checks</p>
              <h2 className="mt-3 text-3xl font-bold tracking-tight">Evidence before completion.</h2>
              <p className="mt-5 leading-7 text-muted-foreground">
                DynosAI verifies deterministic facts: Git files and status versus the plan, path scope, planned actions, requirement-to-evidence traceability, validation command exit codes, unresolved gates and scopes, recovery integrity, provider isolation, and acceptance Oracle results.
              </p>
              <p className="mt-4 leading-7 text-muted-foreground">
                A Quality 100 score is a workflow/governance result. It does not claim universally perfect architecture, security, performance, or maintainability.
              </p>
              <Button variant="outline" className="mt-6" asChild><Link href="/docs/quality-and-validation/">Read quality & validation</Link></Button>
            </div>
            <div className="rounded-2xl border border-border bg-background p-6 font-mono text-sm leading-7 shadow-sm">
              <div>Agent claim</div>
              <div className="text-muted-foreground">↓</div>
              <div>Actual Git diff</div>
              <div className="text-muted-foreground">↓</div>
              <div>Approved scope + planned actions</div>
              <div className="text-muted-foreground">↓</div>
              <div>Task → Requirement → Acceptance Criteria</div>
              <div className="text-muted-foreground">↓</div>
              <div>Validation evidence</div>
              <div className="text-muted-foreground">↓</div>
              <div className="font-semibold">Verified result</div>
            </div>
          </div>
        </div>
      </section>

      <section className="mx-auto max-w-7xl px-5 py-20 lg:px-8">
        <div className="grid gap-10 lg:grid-cols-[1fr_1fr] lg:items-start">
          <div>
            <p className="text-sm font-semibold uppercase tracking-[0.16em] text-muted-foreground">Beta evidence</p>
            <h2 className="mt-3 text-3xl font-bold tracking-tight">Tested with both providers and both project modes.</h2>
            <p className="mt-5 leading-7 text-muted-foreground">
              The current beta baseline is backed by a real-provider matrix covering Codex and Cursor across greenfield and brownfield scenarios. The matrix passed without infrastructure retries.
            </p>
            <Button variant="outline" className="mt-6" asChild><Link href="/validation/">See validation evidence</Link></Button>
          </div>
          <div className="grid grid-cols-2 gap-4">
            {evidence.map(([value, label]) => (
              <Card key={label}>
                <CardContent className="pt-6">
                  <div className="text-3xl font-bold tracking-tight">{value}</div>
                  <div className="mt-1 text-sm text-muted-foreground">{label}</div>
                </CardContent>
              </Card>
            ))}
          </div>
        </div>
      </section>

      <section className="mx-auto max-w-7xl px-5 pb-24 lg:px-8">
        <div className="rounded-3xl border border-border bg-foreground px-6 py-12 text-background sm:px-10 lg:px-14">
          <div className="grid gap-8 lg:grid-cols-[1fr_auto] lg:items-center">
            <div>
              <div className="mb-4 inline-flex rounded-full border border-background/20 px-3 py-1 text-xs font-semibold uppercase tracking-[0.14em]">Public beta</div>
              <h2 className="text-3xl font-bold tracking-tight">Try it on one real feature.</h2>
              <p className="mt-4 max-w-2xl leading-7 opacity-75">DynosAI is open source under MIT, but still pre-1.0. Use normal engineering review and project-specific validation while the beta hardens through real repositories.</p>
            </div>
            <div className="flex flex-wrap gap-3">
              <Button size="lg" variant="outline" className="border-background/25 bg-background text-foreground hover:bg-background/90" asChild>
                <Link href="/docs/getting-started/">Get started</Link>
              </Button>
              <Button size="lg" variant="outline" className="border-background/25 bg-transparent text-background hover:bg-background/10" asChild>
                <a href={siteConfig.github} target="_blank" rel="noreferrer">GitHub ↗</a>
              </Button>
            </div>
          </div>
        </div>
      </section>
    </>
  );
}
