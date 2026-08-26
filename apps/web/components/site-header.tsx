// SPDX-License-Identifier: MIT
// Copyright (c) 2026 Pablo Cano

import Link from "next/link";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { DynosAILogo } from "@/components/dynosai-logo";
import { ThemeToggle } from "@/components/theme-toggle";
import { siteConfig } from "@/lib/site";

export function SiteHeader() {
  return (
    <header className="sticky top-0 z-50 border-b border-border/80 bg-background/90 backdrop-blur-xl">
      <div className="mx-auto flex h-16 max-w-7xl items-center justify-between px-5 lg:px-8">
        <Link href="/" className="flex items-center gap-2.5" aria-label="DynosAI home">
          <DynosAILogo className="h-9 w-9" />
          <span className="font-extrabold leading-none text-foreground">{siteConfig.shortName}</span>
          <Badge className="hidden sm:inline-flex">β Beta</Badge>
        </Link>
        <nav className="hidden items-center gap-1 md:flex" aria-label="Primary navigation">
          <Button variant="ghost" size="sm" asChild><Link href="/why/">Why DynosAI</Link></Button>
          <Button variant="ghost" size="sm" asChild><Link href="/studio/">Studio</Link></Button>
          <Button variant="ghost" size="sm" asChild><Link href="/roadmap/">Roadmap</Link></Button>
          <Button variant="ghost" size="sm" asChild><Link href="/docs/">Docs</Link></Button>
          <Button variant="ghost" size="sm" asChild><Link href="/validation/">Validation</Link></Button>
          <Button variant="ghost" size="sm" asChild><a href={siteConfig.github} target="_blank" rel="noreferrer">GitHub ↗</a></Button>
        </nav>
        <div className="flex items-center gap-1">
          <ThemeToggle />
          <Button size="sm" asChild><Link href="/docs/getting-started/">Get started</Link></Button>
        </div>
      </div>
    </header>
  );
}
