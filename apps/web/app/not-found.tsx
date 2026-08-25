// SPDX-License-Identifier: MIT
// Copyright (c) 2026 Pablo Cano

import Link from "next/link";
import { Button } from "@/components/ui/button";

export default function NotFound() {
  return (
    <div className="mx-auto flex min-h-[60vh] max-w-3xl flex-col items-start justify-center px-5 py-20 lg:px-8">
      <p className="text-sm font-semibold uppercase tracking-[0.16em] text-muted-foreground">404</p>
      <h1 className="mt-3 text-4xl font-bold tracking-tight">This page is not part of the current DynosAI contract.</h1>
      <p className="mt-5 max-w-xl leading-7 text-muted-foreground">The page may have moved, or the documentation route may not exist in this beta release.</p>
      <div className="mt-7 flex gap-3">
        <Button asChild><Link href="/docs/">Open documentation</Link></Button>
        <Button variant="outline" asChild><Link href="/">Back home</Link></Button>
      </div>
    </div>
  );
}
