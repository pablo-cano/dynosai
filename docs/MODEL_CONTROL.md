# Model Control

DynosAI treats model choice as an auditable engineering decision rather than a fixed provider setting.

## Signals evaluated at decision opportunities

A model-control decision can consider:

- current workflow phase;
- repository/work complexity;
- active task and requirement count;
- live phase token usage versus phase budgets;
- recent validation outcomes and their classified failure kind;
- scope/governance risk;
- tool-loop or stagnation signals;
- current provider/activity/tier route;
- historical outcome evidence.

## Context pressure is not model capability

Large context does not by itself justify a more expensive model. DynosAI first prefers deterministic context controls such as:

```text
reuse checkpoint
-> compact response
-> narrow tool surface
-> retrieve only missing context
-> remove stale context
-> re-evaluate
```

Only real capability evidence should justify escalation.

## Route evidence lifecycle

DynosAI separates these facts:

- candidate route;
- recommendation;
- transition request;
- transition applied by provider;
- transition verified from provider evidence.

A recommendation is not counted as a real model transition until the provider evidence verifies it.

## Predictive Router

The predictive router stores historical provider/activity/tier outcomes and can replay decisions chronologically offline. It uses conservative evidence and excludes known orchestration/legacy contamination.

The final 0.13.0 offline evidence includes 31 observations, 29 eligible samples, two providers, two scenarios, and six model-failure observations. Quantitative authority gates are satisfied, but the replay still contains six missed escalation signals.

For that reason the **beta runtime baseline keeps predictive routing in shadow mode**. The evidence is useful for research and future releases, and 0.14.0 still does not delegate autonomous model spending to it.

Machine-readable evidence: [`validation/predictive-router-0.13.0.json`](validation/predictive-router-0.13.0.json).
