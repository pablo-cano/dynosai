// SPDX-License-Identifier: MIT
// Copyright (c) 2026 Pablo Cano

import Link from "next/link";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";

export default function StudioPage() {
  const surfaces = [
    ["Project onboarding", "Detect Git, stacks and project validation commands before initializing governed state."],
    ["Workflow overview", "See current work, pending human decisions, blockers and recent durable work without reading SQLite directly."],
    ["Validation discovery", "Inspect inferred unit, lint, type-check and build profiles, then explicitly approve the commands you want DynosAI to govern."],
    ["Risk assessment", "Use a deterministic advisory score to increase review intensity around security, migrations, dependencies, CI and large blast radius."],
  ];
  return (
    <div className="mx-auto max-w-6xl px-5 py-16 lg:px-8">
      <Badge>0.14 · Alpha</Badge>
      <h1 className="mt-5 max-w-4xl text-4xl font-bold tracking-tight sm:text-5xl">A local control plane for people who do not want to live in the CLI.</h1>
      <p className="mt-6 max-w-3xl text-lg leading-8 text-muted-foreground">DynosAI Studio is served only on loopback and talks to the same provider-neutral application API used by the rest of DynosAI. Git and durable DynosAI state remain authoritative.</p>
      <div className="mt-8 rounded-2xl border border-border bg-[#0d1117] p-6 font-mono text-sm leading-7 text-[#e6edf3]">
        <div><span className="text-[#8b949e]">$</span> cd /path/to/your/project</div>
        <div><span className="text-[#8b949e]">$</span> dynosai studio</div>
        <div className="mt-3 text-[#8b949e]"># Opens http://127.0.0.1:8765/</div>
      </div>
      <div className="mt-12 grid gap-4 md:grid-cols-2">
        {surfaces.map(([title, description]) => <Card key={title}><CardHeader><CardTitle>{title}</CardTitle></CardHeader><CardContent><CardDescription>{description}</CardDescription></CardContent></Card>)}
      </div>
      <div className="mt-10 flex flex-wrap gap-3"><Button asChild><Link href="/docs/getting-started/">Install and launch Studio</Link></Button><Button variant="outline" asChild><Link href="/roadmap/">See what comes next</Link></Button></div>
    </div>
  );
}
