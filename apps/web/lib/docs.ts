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
  { slug: "compatibility", title: "Compatibility", description: "What DynosAI 1.0 freezes: MCP names, public CLI, schema v6, App Server scope, human gates and optional harness features.", source: "docs/COMPATIBILITY.md", group: "Core concepts", order: 35 },
  { slug: "threat-model", title: "Threat model", description: "Enforced vs not-enforced 1.0 controls, including the honest local-browser and loopback surface.", source: "docs/THREAT_MODEL.md", group: "Core concepts", order: 36 },
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
  { slug: "release-1-0-0-rc-8", title: "1.0.0-rc.8 release notes", description: "Provider runtime portability. User-local workspaces, Codex temp-home abort, candidate identity gate. Not production-ready 1.0.", source: "docs/RELEASE_1.0.0-rc.8.md", group: "Project", order: 131 },
  { slug: "release-1-0-0-rc-7", title: "1.0.0-rc.7 release notes", description: "Certification integrity and protocol correctness. Release archive safety, MCP 2026 strict metadata/MRTR, MATRIX runner honesty. Not production-ready 1.0.", source: "docs/RELEASE_1.0.0-rc.7.md", group: "Project", order: 132 },
  { slug: "release-1-0-0-rc-6", title: "1.0.0-rc.6 release notes", description: "Standards and release evidence: MCP 2026 compatibility, unified release gate, eval corpus and distribution ADR. Not production-ready 1.0.", source: "docs/RELEASE_1.0.0-rc.6.md", group: "Project", order: 133 },
  { slug: "release-1-0-0-rc-5", title: "1.0.0-rc.5 release notes", description: "Freeze: threat model, contribution/certification process and loopback honesty. External PRs stay closed. Not production-ready 1.0.", source: "docs/RELEASE_1.0.0-rc.5.md", group: "Project", order: 133 },
  { slug: "release-1-0-0-rc-4", title: "1.0.0-rc.4 release notes", description: "Installation: Studio doctor, verified local wheel, installed-package CI and 0.19.0 upgrade preservation. Not production-ready 1.0.", source: "docs/RELEASE_1.0.0-rc.4.md", group: "Project", order: 134 },
  { slug: "release-1-0-0-rc-3", title: "1.0.0-rc.3 release notes", description: "Eval maturity: acceptance ZIP importer, governed-change cost aggregates and a stable authority prefix. Not production-ready 1.0.", source: "docs/RELEASE_1.0.0-rc.3.md", group: "Project", order: 135 },
  { slug: "release-1-0-0-rc-2", title: "1.0.0-rc.2 release notes", description: "Certification evidence: MATRIX_1.0 not_run cells and two-session lease proof. Historical 0.13 scores are not copied.", source: "docs/RELEASE_1.0.0-rc.2.md", group: "Project", order: 136 },
  { slug: "release-1-0-0-rc-1", title: "1.0.0-rc.1 release notes", description: "Contract freeze: frozen MCP names, optional harness switches, shared release-gate policy. Not production-ready 1.0.", source: "docs/RELEASE_1.0.0-rc.1.md", group: "Project", order: 137 },
  { slug: "release-0-19-0", title: "0.19.0 release notes", description: "Ecosystem: capability manifests for Cursor ACP and Codex app-server; uncertified clients and packs refused.", source: "docs/RELEASE_0.19.0.md", group: "Project", order: 138 },
  { slug: "release-0-18-0", title: "0.18.0 release notes", description: "Secure Autonomous Runtime: Strict/Balanced/Autonomous profiles, runtime-only vault and policy evidence without OS network interception.", source: "docs/RELEASE_0.18.0.md", group: "Project", order: 139 },
  { slug: "release-0-17-0", title: "0.17.0 release notes", description: "Eval Intelligence: failure attribution, local-trace mining, inbox-only improvement loop and offline regression evidence.", source: "docs/RELEASE_0.17.0.md", group: "Project", order: 140 },
  { slug: "release-0-16-0", title: "0.16.0 release notes", description: "Governed Agent Teams: DAG waves, file-disjoint leases, claim/fan-in and no extra provider spawn.", source: "docs/RELEASE_0.16.0.md", group: "Project", order: 141 },
  { slug: "release-0-15-0", title: "0.15.0 release notes", description: "Verified Agent Harness: context handles, ExecutionRuntime, validation integrity, eval registry v0 and execution policy.", source: "docs/RELEASE_0.15.0.md", group: "Project", order: 142 },
  { slug: "release-0-14-1", title: "0.14.1 release notes", description: "Guided Studio UX refresh: setup, reviews, themes, project checks, help and compatibility boundaries.", source: "docs/RELEASE_0.14.1.md", group: "Project", order: 144 },
  { slug: "release-0-14-0", title: "0.14.0 release notes", description: "Install, validate and understand compatibility and limitations of the Control Plane & Studio Alpha release.", source: "docs/RELEASE_0.14.0.md", group: "Project", order: 145 },
  { slug: "code-quality", title: "Code quality", description: "Maintainability review, current module and LOC metrics, hotspots, and characterization-before-extract policy.", source: "docs/CODE_QUALITY.md", group: "Project", order: 146 },
  { slug: "adr-pypi-name", title: "ADR: PyPI distribution name", description: "Use dynosai on PyPI, keep the dynosai_flow import, and keep MCP serverInfo as dynosai-flow.", source: "docs/adr/0001-pypi-distribution-name.md", group: "Project", order: 147 },
  { slug: "adr-agent-plugins", title: "ADR: Agent Plugins", description: "Future plugins follow the portable Agent Plugins spec with a com.dynosai extension. No DynosAI Pack format.", source: "docs/adr/0002-agent-plugins.md", group: "Project", order: 148 },
  { slug: "adr-mcp-eras", title: "ADR: MCP protocol eras", description: "Support 2026-07-28 plus 2025-11-25 and 2025-06-18 on stdio. Official HTTP conformance is not pretended.", source: "docs/adr/0003-mcp-protocol-eras.md", group: "Project", order: 149 },
  { slug: "release-process", title: "Release process", description: "Versioning, changelog discipline, tests, acceptance matrices, and release-candidate and promotion policy.", source: "docs/RELEASE_PROCESS.md", group: "Project", order: 150 },
  { slug: "security", title: "Security", description: "Security policy, responsible reporting, and project security boundaries.", source: "SECURITY.md", group: "Project", order: 170 },
  { slug: "contributing", title: "Contributing", description: "Maintainer development, release gate and certified-provider process. External pull requests are not currently accepted.", source: "CONTRIBUTING.md", group: "Project", order: 171 },
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
  "COMPATIBILITY.md": "/docs/compatibility/",
  "THREAT_MODEL.md": "/docs/threat-model/",
  "EVOLUTION.md": "/docs/evolution/",
  "CODE_QUALITY.md": "/docs/code-quality/",
  "RELEASE_1.0.0-rc.8.md": "/docs/release-1-0-0-rc-8/",
  "RELEASE_1.0.0-rc.7.md": "/docs/release-1-0-0-rc-7/",
  "RELEASE_1.0.0-rc.6.md": "/docs/release-1-0-0-rc-6/",
  "RELEASE_1.0.0-rc.5.md": "/docs/release-1-0-0-rc-5/",
  "RELEASE_1.0.0-rc.4.md": "/docs/release-1-0-0-rc-4/",
  "RELEASE_1.0.0-rc.3.md": "/docs/release-1-0-0-rc-3/",
  "RELEASE_1.0.0-rc.2.md": "/docs/release-1-0-0-rc-2/",
  "RELEASE_1.0.0-rc.1.md": "/docs/release-1-0-0-rc-1/",
  "RELEASE_0.19.0.md": "/docs/release-0-19-0/",
  "RELEASE_0.18.0.md": "/docs/release-0-18-0/",
  "RELEASE_0.17.0.md": "/docs/release-0-17-0/",
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
  "CONTRIBUTING.md": "/docs/contributing/",
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
  "docs/COMPATIBILITY.md": "/docs/compatibility/",
  "docs/THREAT_MODEL.md": "/docs/threat-model/",
  "docs/EVOLUTION.md": "/docs/evolution/",
  "docs/CODE_QUALITY.md": "/docs/code-quality/",
  "docs/RELEASE_1.0.0-rc.8.md": "/docs/release-1-0-0-rc-8/",
  "docs/RELEASE_1.0.0-rc.7.md": "/docs/release-1-0-0-rc-7/",
  "docs/RELEASE_1.0.0-rc.6.md": "/docs/release-1-0-0-rc-6/",
  "docs/RELEASE_1.0.0-rc.5.md": "/docs/release-1-0-0-rc-5/",
  "docs/RELEASE_1.0.0-rc.4.md": "/docs/release-1-0-0-rc-4/",
  "docs/RELEASE_1.0.0-rc.3.md": "/docs/release-1-0-0-rc-3/",
  "docs/RELEASE_1.0.0-rc.2.md": "/docs/release-1-0-0-rc-2/",
  "docs/RELEASE_1.0.0-rc.1.md": "/docs/release-1-0-0-rc-1/",
  "docs/RELEASE_0.19.0.md": "/docs/release-0-19-0/",
  "docs/RELEASE_0.18.0.md": "/docs/release-0-18-0/",
  "docs/RELEASE_0.17.0.md": "/docs/release-0-17-0/",
  "docs/RELEASE_0.16.0.md": "/docs/release-0-16-0/",
  "docs/RELEASE_0.15.0.md": "/docs/release-0-15-0/",
  "docs/RELEASE_0.14.1.md": "/docs/release-0-14-1/",
  "docs/RELEASE_0.14.0.md": "/docs/release-0-14-0/",
  "docs/RELEASE_PROCESS.md": "/docs/release-process/",
  "docs/reference/CLI.md": "/docs/reference/cli/",
  "docs/reference/CONFIGURATION.md": "/docs/reference/configuration/",
  "docs/reference/MODULES.md": "/docs/reference/modules/",
  "docs/adr/0001-pypi-distribution-name.md": "/docs/adr-pypi-name/",
  "docs/adr/0002-agent-plugins.md": "/docs/adr-agent-plugins/",
  "docs/adr/0003-mcp-protocol-eras.md": "/docs/adr-mcp-eras/",
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
