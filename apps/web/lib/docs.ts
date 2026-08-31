// SPDX-License-Identifier: MIT
// Copyright (c) 2026 Pablo Cano

import fs from "node:fs";
import path from "node:path";

export type DocDefinition = {
  slug: string;
  title: string;
  description: string;
  source: string;
  group: string;
  order: number;
};

function findRepositoryRoot(start: string) {
  let current = path.resolve(start);
  for (;;) {
    const hasProject = fs.existsSync(path.join(current, "pyproject.toml"));
    const hasDocs = fs.existsSync(path.join(current, "docs", "ARCHITECTURE.md"));
    if (hasProject && hasDocs) return current;
    const parent = path.dirname(current);
    if (parent === current) break;
    current = parent;
  }
  throw new Error("Unable to locate the DynosAI repository root from the current working directory.");
}

const repoRoot = findRepositoryRoot(process.cwd());

export const docs: DocDefinition[] = [
  { slug: "getting-started", title: "Getting started", description: "Install DynosAI, configure a provider, initialize a project, and run your first governed workflow.", source: "GETTING_STARTED.md", group: "Start", order: 10 },
  { slug: "user-guide", title: "User guide", description: "The day-to-day DynosAI workflow, commands, scope changes, recovery, and greenfield/brownfield usage.", source: "docs/USER_GUIDE.md", group: "Start", order: 20 },
  { slug: "studio", title: "Studio", description: "Use the guided project hub, project-scoped workflow, approvals, checks, themes and multilingual interface.", source: "docs/STUDIO.md", group: "Start", order: 25 },
  { slug: "quality-and-validation", title: "Quality and validation", description: "Exactly what DynosAI verifies, how validation profiles work, and what Quality 100 does—and does not—mean.", source: "docs/QUALITY_AND_VALIDATION.md", group: "Core concepts", order: 30 },
  { slug: "architecture", title: "Architecture", description: "Authority boundaries, workflow state, Git, MCP, memory, verification, and the local-first runtime model.", source: "docs/ARCHITECTURE.md", group: "Core concepts", order: 40 },
  { slug: "model-control", title: "Model control", description: "Phase-aware budgets, complexity signals, failure classification, route lifecycle, and predictive shadow mode.", source: "docs/MODEL_CONTROL.md", group: "Core concepts", order: 50 },
  { slug: "brownfield", title: "Brownfield workflows", description: "How DynosAI indexes existing code and builds an evidence-backed inferred baseline without inventing business truth.", source: "docs/BROWNFIELD.md", group: "Core concepts", order: 60 },
  { slug: "providers", title: "Providers", description: "Codex and Cursor integration, managed runtime behavior, and transport compatibility decisions.", source: "docs/PROVIDERS.md", group: "Operations", order: 70 },
  { slug: "observability", title: "Observability", description: "MCP telemetry, tokens, context strategy, validation evidence, routing decisions, retries, and audit events.", source: "docs/OBSERVABILITY.md", group: "Operations", order: 80 },
  { slug: "testing-strategy", title: "Testing strategy", description: "The deterministic, migration, provider-native, real-provider, Oracle, wheel, and replay validation layers.", source: "docs/TESTING_STRATEGY.md", group: "Operations", order: 90 },
  { slug: "reference/cli", title: "CLI reference", description: "Public command overview for projects, work, diagnostics, setup, model control, and acceptance.", source: "docs/reference/CLI.md", group: "Reference", order: 100 },
  { slug: "reference/configuration", title: "Configuration", description: "Runtime and environment configuration for DynosAI projects and providers.", source: "docs/reference/CONFIGURATION.md", group: "Reference", order: 110 },
  { slug: "reference/modules", title: "Module reference", description: "Responsibility of every production Python module in the DynosAI package.", source: "docs/reference/MODULES.md", group: "Reference", order: 120 },
  { slug: "evolution", title: "Project evolution", description: "How DynosAI evolved from persistent workflow state to provider-certified governance.", source: "docs/EVOLUTION.md", group: "Project", order: 130 },
  { slug: "code-quality", title: "Code quality", description: "Maintainability review, codebase size, hotspots, and the refactoring policy for the current beta baseline.", source: "docs/CODE_QUALITY.md", group: "Project", order: 140 },
  { slug: "release-0-16-0", title: "0.16.0 release notes", description: "Governed Agent Teams: DAG waves, file-disjoint leases, claim/fan-in and no extra provider spawn.", source: "docs/RELEASE_0.16.0.md", group: "Project", order: 141 },
  { slug: "release-0-15-0", title: "0.15.0 release notes", description: "Verified Agent Harness: context handles, ExecutionRuntime, validation integrity, eval registry v0 and execution policy.", source: "docs/RELEASE_0.15.0.md", group: "Project", order: 142 },
  { slug: "release-0-14-1", title: "0.14.1 release notes", description: "Guided Studio UX refresh: setup, reviews, themes, project checks, help and compatibility boundaries.", source: "docs/RELEASE_0.14.1.md", group: "Project", order: 144 },
  { slug: "release-0-14-0", title: "0.14.0 release notes", description: "Install, validate and understand compatibility and limitations of the Control Plane & Studio Alpha release.", source: "docs/RELEASE_0.14.0.md", group: "Project", order: 145 },
  { slug: "release-process", title: "Release process", description: "Versioning, changelog discipline, tests, acceptance matrices, and release-candidate and promotion policy.", source: "docs/RELEASE_PROCESS.md", group: "Project", order: 150 },
  { slug: "security", title: "Security", description: "Security policy, responsible reporting, and project security boundaries.", source: "SECURITY.md", group: "Project", order: 170 },
  { slug: "roadmap", title: "Roadmap", description: "The product roadmap from Local Studio through harness loops, governed agent teams, eval intelligence, secure runtime and 1.0.", source: "ROADMAP.md", group: "Project", order: 180 },
  { slug: "changelog", title: "Changelog", description: "Release-by-release evolution, fixes, compatibility work, validation, and release and promotion history.", source: "CHANGELOG.md", group: "Project", order: 190 },
].sort((a, b) => a.order - b.order);

export function getDoc(slug: string) {
  const definition = docs.find((doc) => doc.slug === slug);
  if (!definition) return null;
  const absolutePath = path.join(repoRoot, definition.source);
  return { ...definition, content: fs.readFileSync(absolutePath, "utf8") };
}

export function getDocsByGroup() {
  const groups = new Map<string, DocDefinition[]>();
  for (const doc of docs) {
    const entries = groups.get(doc.group) ?? [];
    entries.push(doc);
    groups.set(doc.group, entries);
  }
  return [...groups.entries()];
}

const hrefMap: Record<string, string> = {
  "docs/validation/": "/validation/",
  "validation/final-matrix-0.13.0.json": "/validation/",
  "validation/predictive-router-0.13.0.json": "/validation/",
  "USER_GUIDE.md": "/docs/user-guide/",
  "ARCHITECTURE.md": "/docs/architecture/",
  "STUDIO.md": "/docs/studio/",
  "QUALITY_AND_VALIDATION.md": "/docs/quality-and-validation/",
  "MODEL_CONTROL.md": "/docs/model-control/",
  "BROWNFIELD.md": "/docs/brownfield/",
  "PROVIDERS.md": "/docs/providers/",
  "OBSERVABILITY.md": "/docs/observability/",
  "TESTING_STRATEGY.md": "/docs/testing-strategy/",
  "EVOLUTION.md": "/docs/evolution/",
  "CODE_QUALITY.md": "/docs/code-quality/",
  "RELEASE_0.16.0.md": "/docs/release-0-16-0/",
  "RELEASE_0.15.0.md": "/docs/release-0-15-0/",
  "RELEASE_0.14.1.md": "/docs/release-0-14-1/",
  "RELEASE_0.14.0.md": "/docs/release-0-14-0/",
  "RELEASE_PROCESS.md": "/docs/release-process/",
  "CLI.md": "/docs/reference/cli/",
  "CONFIGURATION.md": "/docs/reference/configuration/",
  "MODULES.md": "/docs/reference/modules/",
  "GETTING_STARTED.md": "/docs/getting-started/",
  "README.md": "/",
  "SECURITY.md": "/docs/security/",
  "ROADMAP.md": "/docs/roadmap/",
  "CHANGELOG.md": "/docs/changelog/",
  "docs/USER_GUIDE.md": "/docs/user-guide/",
  "docs/ARCHITECTURE.md": "/docs/architecture/",
  "docs/STUDIO.md": "/docs/studio/",
  "docs/QUALITY_AND_VALIDATION.md": "/docs/quality-and-validation/",
  "docs/MODEL_CONTROL.md": "/docs/model-control/",
  "docs/BROWNFIELD.md": "/docs/brownfield/",
  "docs/PROVIDERS.md": "/docs/providers/",
  "docs/OBSERVABILITY.md": "/docs/observability/",
  "docs/TESTING_STRATEGY.md": "/docs/testing-strategy/",
  "docs/EVOLUTION.md": "/docs/evolution/",
  "docs/CODE_QUALITY.md": "/docs/code-quality/",
  "docs/RELEASE_0.16.0.md": "/docs/release-0-16-0/",
  "docs/RELEASE_0.15.0.md": "/docs/release-0-15-0/",
  "docs/RELEASE_0.14.1.md": "/docs/release-0-14-1/",
  "docs/RELEASE_0.14.0.md": "/docs/release-0-14-0/",
  "docs/RELEASE_PROCESS.md": "/docs/release-process/",
  "docs/reference/CLI.md": "/docs/reference/cli/",
  "docs/reference/CONFIGURATION.md": "/docs/reference/configuration/",
  "docs/reference/MODULES.md": "/docs/reference/modules/",
};

export function rewriteMarkdownHref(href?: string) {
  if (!href) return href;
  const withoutAnchor = href.split("#")[0];
  const anchor = href.includes("#") ? `#${href.split("#").slice(1).join("#")}` : "";
  if (hrefMap[withoutAnchor]) return `${hrefMap[withoutAnchor]}${anchor}`;

  const normalized = withoutAnchor.replace(/^\.\//, "").replace(/^\.\.\//, "docs/");
  if (hrefMap[normalized]) return `${hrefMap[normalized]}${anchor}`;
  return href;
}
