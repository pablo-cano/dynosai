// SPDX-License-Identifier: MIT
// Copyright (c) 2026 Pablo Cano

import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";

const releases = [
  ["0.14", "Control Plane & Guided Studio", "Local App Server, guided setup, task timelines, Review Center, Cursor ACP, Codex app-server, project checks, themes, help, validation discovery and deterministic risk assessment."],
  ["0.15", "Verified Agent Harness", "Context handles, ExecutionRuntime, validation integrity, eval registry v0, execution policy outside the model, and measurable skill/handle comparisons."],
  ["0.16", "Governed Agent Teams", "Plan-DAG serial/parallel waves, file-disjoint leases, claim/fan-in and reviewer/tester as contracts. DynosAI does not spawn extra provider processes."],
  ["0.17", "Eval Intelligence", "Failure attribution, local-trace mining, inbox-only improvement tasks and offline regression evidence. Predictive routing stays in shadow mode. No live-provider leaderboard."],
  ["0.18", "Secure Autonomous Runtime", "Strict/Balanced/Autonomous host-owned profiles, runtime-only secret vault and policy evidence. Human gates stay required. Docker/VM and OS-level network interception are not shipped."],
  ["0.19", "Ecosystem", "Capability manifests for the existing Cursor ACP and Codex app-server transports. Additional clients and extension packs are refused, not silently certified."],
  ["1.0", "Stable Agentic Development Control Plane", "Stable APIs, installers, compatibility contracts, certification matrix and upgrade guarantees."],
];

export default function RoadmapPage() {
  return (
    <div className="mx-auto max-w-6xl px-5 py-16 lg:px-8">
      <Badge>Product roadmap</Badge>
      <h1 className="mt-5 text-4xl font-bold tracking-tight sm:text-5xl">From governed SDD core to a local agentic development control plane.</h1>
      <p className="mt-6 max-w-3xl text-lg leading-8 text-muted-foreground">The roadmap deliberately keeps workflow authority, evidence and human governance ahead of autonomy. Later capabilities must preserve the stable core rather than bypass it.</p>
      <div className="mt-12 grid gap-4">
        {releases.map(([version,title,description]) => <Card key={version}><CardHeader className="md:flex-row md:items-center md:gap-6"><div className="min-w-20 font-mono text-xl font-bold">{version}</div><div><CardTitle>{title}</CardTitle><CardContent className="px-0 pb-0 pt-2 text-muted-foreground">{description}</CardContent></div></CardHeader></Card>)}
      </div>
    </div>
  );
}
