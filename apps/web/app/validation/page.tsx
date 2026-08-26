// SPDX-License-Identifier: MIT
// Copyright (c) 2026 Pablo Cano

import type { Metadata } from "next";
import Link from "next/link";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";

export const metadata: Metadata = {
  title: "Validation",
  description: "How the DynosAI 0.14.0 product layer preserves the 0.13.0 certified governed core while adding deterministic Studio and application-layer regression coverage.",
};

const matrix = [
  ["Codex", "Greenfield · Fibonacci", "PASS", "100", "8/8"],
  ["Cursor", "Greenfield · Fibonacci", "PASS", "100", "8/8"],
  ["Codex", "Brownfield · Contract discounts", "PASS", "100", "10/10"],
  ["Cursor", "Brownfield · Contract discounts", "PASS", "100", "10/10"],
];

export default function ValidationPage() {
  return (
    <div className="mx-auto max-w-6xl px-5 py-20 lg:px-8">
      <div className="max-w-3xl">
        <Badge>β Public beta · 0.14.0</Badge>
        <h1 className="mt-5 text-4xl font-bold tracking-[-0.04em] sm:text-5xl">The beta baseline is backed by evidence, not by a version number.</h1>
        <p className="mt-6 text-lg leading-8 text-muted-foreground">DynosAI 0.14.0 preserves the accepted 0.13.0 governed-core behavior while adding a local App Server and Studio regression layer. The real-provider Green/Brown matrix shown below remains the core provider baseline; 0.14 adds deterministic application, risk, validation-discovery and packaged-Studio checks without expanding agent authority.</p>
      </div>

      <div className="mt-12 overflow-x-auto rounded-2xl border border-border">
        <table className="w-full min-w-[680px] text-sm">
          <thead className="bg-muted text-left"><tr><th className="p-4">Provider</th><th className="p-4">Scenario</th><th className="p-4">Result</th><th className="p-4">Quality</th><th className="p-4">Oracle</th></tr></thead>
          <tbody>{matrix.map((row) => <tr key={`${row[0]}-${row[1]}`} className="border-t border-border">{row.map((value) => <td key={value} className="p-4">{value}</td>)}</tr>)}</tbody>
        </table>
      </div>

      <div className="mt-10 grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        {[['63', 'MCP calls'], ['0', 'MCP failures'], ['0', 'scope requests'], ['0', 'retries']].map(([value, label]) => (
          <Card key={label}><CardContent className="pt-6"><div className="text-3xl font-bold">{value}</div><div className="mt-1 text-sm text-muted-foreground">{label}</div></CardContent></Card>
        ))}
      </div>

      <div className="mt-16 grid gap-5 lg:grid-cols-2">
        <Card>
          <CardHeader><CardTitle>What the matrix proves</CardTitle></CardHeader>
          <CardContent className="space-y-3 text-sm leading-6 text-muted-foreground">
            <p>Both supported providers can complete governed greenfield work with the current governed workflow.</p>
            <p>Both can preserve and extend existing behavior in the brownfield scenario while passing independent Oracle checks.</p>
            <p>Codex uses structured-primary MCP transport while Cursor uses the compatibility transport required by its CLI stream.</p>
          </CardContent>
        </Card>
        <Card>
          <CardHeader><CardTitle>What it does not prove</CardTitle></CardHeader>
          <CardContent className="space-y-3 text-sm leading-6 text-muted-foreground">
            <p>It is not a universal guarantee across every language, repository size, CI system, security policy, or provider version.</p>
            <p>DynosAI remains pre-1.0 and benefits from real-world feedback on larger repositories and broader stacks.</p>
            <p>The Predictive Router remains in shadow mode even though the historical sample size now crosses its quantitative authority gates.</p>
          </CardContent>
        </Card>
      </div>

      <div className="mt-10 flex flex-wrap gap-3">
        <Button asChild><Link href="/docs/testing-strategy/">Read testing strategy</Link></Button>
        <Button variant="outline" asChild><Link href="/docs/quality-and-validation/">Understand Quality 100</Link></Button>
      </div>
    </div>
  );
}
