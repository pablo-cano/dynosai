// SPDX-License-Identifier: MIT
// Copyright (c) 2026 Pablo Cano

const stages = [
  { name: "Need", detail: "Define the engineering need and create durable work state." },
  { name: "Discovery", detail: "Inspect repository context, constraints, and relevant existing behavior." },
  { name: "Specification", detail: "Turn intent into requirements and acceptance criteria before implementation." },
  { name: "Plan", detail: "Define tasks, dependencies, files, actions, and validation profiles." },
  { name: "Implementation", detail: "Execute only the approved work and keep changes inside governed scope." },
  { name: "Verification", detail: "Compare agent claims with Git, scope, task, requirement, and evidence state." },
  { name: "Validation", detail: "Run the configured project checks and record their real outcomes." },
  { name: "Done", detail: "Finish only after required gates, evidence, and validation are resolved." },
];

export function FlowDiagram() {
  return (
    <div className="rounded-2xl border border-border bg-card p-5 shadow-sm sm:p-6">
      <ol className="relative" aria-label="DynosAI governed workflow">
        {stages.map((stage, index) => (
          <li key={stage.name} className="relative grid grid-cols-[2rem_1fr] gap-3 pb-5 last:pb-0">
            {index < stages.length - 1 && (
              <span
                aria-hidden="true"
                className="absolute left-[0.9375rem] top-8 h-[calc(100%-1rem)] w-px bg-border"
              />
            )}
            <div className="relative z-10 flex h-8 w-8 items-center justify-center rounded-full border border-border bg-background text-xs font-bold tabular-nums">
              {index + 1}
            </div>
            <div className="min-w-0 pt-0.5">
              <div className="text-sm font-semibold text-foreground">{stage.name}</div>
              <p className="mt-1 text-sm leading-6 text-muted-foreground">{stage.detail}</p>
            </div>
          </li>
        ))}
      </ol>
    </div>
  );
}
