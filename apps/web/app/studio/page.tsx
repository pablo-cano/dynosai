// SPDX-License-Identifier: MIT
// Copyright (c) 2026 Pablo Cano

import Link from "next/link";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";

export default function StudioPage() {
  const surfaces = [
    ["Project hub first", "Studio opens on a neutral project hub unless you explicitly launch it with --project. Returning Home leaves the active project context and keeps it in Recent projects."],
    ["Project-scoped workflow", "Overview, New change, Work, Approvals and Project checks appear together under the selected project instead of competing with global Studio pages."],
    ["Folder browser", "Create and Open actions use a contextual in-Studio directory browser. Path fields are read-only, and you can navigate folders or create a new directory without leaving Studio."],
    ["Natural-language changes", "New change contains only the request. Agent, workspace and model routing are configured once in Project settings and reused automatically."],
    ["Visible agent execution", "Creating a change starts Codex through app-server or Cursor through ACP without blocking Studio. Work now includes a bounded live activity trace so users can see provider progress without opening raw diagnostics."],
    ["Project configuration", "Project settings choose Codex or Cursor, Interactive branch versus Isolated worktree, Strict/Balanced/Autonomous execution profiles, optional harness optimizations, and the model routes for the selected agent."],
    ["Approvals you can actually review", "Specification gates show requirements and acceptance criteria; plan gates show tasks, files and risks; code/merge reviews can expand the resulting diff, validation results, integrity risks and execution-policy evidence."],
    ["English and Spanish", "English remains the default. Spanish is included and the translation dictionary is designed so more languages can be added without duplicating the UI."],
    ["Contextual Fibonacci walkthrough", "The tutorial no longer creates or performs the workflow for you. It highlights the exact screen, field and button while you create the greenfield project and complete each step yourself."],
  ];
  return (
    <div className="mx-auto max-w-6xl px-5 py-16 lg:px-8">
      <Badge>1.0.0-rc.1 · Contract freeze</Badge>
      <h1 className="mt-5 max-w-4xl text-4xl font-bold tracking-tight sm:text-5xl">A project-first Studio that teaches the workflow while you use it.</h1>
      <p className="mt-6 max-w-3xl text-lg leading-8 text-muted-foreground">DynosAI Studio is designed for people managing small local software projects who should not need to understand SQLite, MCP events or internal workflow-state names before they can safely use coding agents.</p>
      <div className="mt-8 rounded-2xl border border-border bg-[#0d1117] p-6 font-mono text-sm leading-7 text-[#e6edf3]">
        <div><span className="text-[#8b949e]">$</span> dynosai studio</div>
        <div className="mt-3 text-[#8b949e]"># Opens the project hub with no project selected.</div>
        <div className="text-[#8b949e]"># Use --project only when you want a project selected at launch.</div>
      </div>
      <div className="mt-12 grid gap-4 md:grid-cols-2 lg:grid-cols-3">
        {surfaces.map(([title, description]) => <Card key={title}><CardHeader><CardTitle>{title}</CardTitle></CardHeader><CardContent><CardDescription>{description}</CardDescription></CardContent></Card>)}
      </div>
      <div className="mt-12 rounded-2xl border border-border bg-muted/45 p-7">
        <p className="text-sm font-semibold uppercase tracking-[0.14em] text-muted-foreground">Normal Studio path</p>
        <p className="mt-3 text-lg font-semibold">Home → choose a project → Overview → New change → Work / Approvals → Project settings</p>
        <p className="mt-3 max-w-3xl leading-7 text-muted-foreground">Before initialization, only Overview is interactive. Initialization shows progress and sends you to Project checks only when detected checks need approval. Risk internals, diagnostics and the activity stream stay optional.</p>
      </div>
      <div className="mt-12 grid gap-4 md:grid-cols-2">
        <Card><CardHeader><CardTitle>Learn by doing, not by automation</CardTitle></CardHeader><CardContent><CardDescription>The Fibonacci walkthrough is contextual. It asks you to create a neutral project, initialize it, enter the Fibonacci request and make the approvals yourself while Studio highlights exactly where to act. Project checks remain available, but the tutorial no longer depends on pre-existing pytest configuration and stays with you through repeated approvals until the work is actually complete.</CardDescription></CardContent></Card>
        <Card><CardHeader><CardTitle>Consistent controls</CardTitle></CardHeader><CardContent><CardDescription>Core choices and confirmations use Studio-native controls styled to match the shadcn/ui interaction language used on dynosai.com. Browser-native select and confirm dialogs are not part of the normal Studio flow.</CardDescription></CardContent></Card>
      </div>
      <div className="mt-10 flex flex-wrap gap-3"><Button asChild><Link href="/docs/getting-started/">Install and launch Studio</Link></Button><Button variant="outline" asChild><Link href="/docs/studio/">Read the Studio guide</Link></Button><Button variant="outline" asChild><Link href="/roadmap/">See what comes next</Link></Button></div>
    </div>
  );
}
