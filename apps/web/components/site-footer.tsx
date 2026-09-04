// SPDX-License-Identifier: MIT
// Copyright (c) 2026 Pablo Cano

import Link from "next/link";
import { DynosAILogo } from "@/components/dynosai-logo";
import { siteConfig } from "@/lib/site";

export function SiteFooter() {
  return (
    <footer className="border-t border-border">
      <div className="mx-auto grid max-w-7xl gap-8 px-5 py-10 text-sm text-muted-foreground sm:grid-cols-2 lg:px-8">
        <div>
          <div className="flex items-center gap-2.5">
            <DynosAILogo className="h-8 w-8" />
            <p className="font-extrabold leading-none text-foreground">{siteConfig.name}</p>
            <span className="rounded-full border border-border px-2 py-0.5 text-[11px] font-semibold uppercase tracking-wide">RC</span>
          </div>
          <p className="mt-3 max-w-lg leading-6">Open-source, local-first governance for AI-assisted software development.</p>
          <p className="mt-3">Created and maintained by <a className="underline underline-offset-4" href={siteConfig.linkedin} target="_blank" rel="noreferrer">{siteConfig.author}</a>. Released under the {siteConfig.license} License.</p>
          <p className="mt-3 max-w-xl leading-6">{siteConfig.status}. External pull requests are not currently accepted while the public API and contribution model stabilize. Feedback and bug reports are welcome.</p>
        </div>
        <div className="flex flex-wrap content-start gap-x-6 gap-y-3 sm:justify-end">
          <Link href="/docs/">Documentation</Link>
          <Link href="/docs/changelog/">Changelog</Link>
          <Link href="/docs/security/">Security</Link>
          <a href={siteConfig.github} target="_blank" rel="noreferrer">GitHub</a>
          <a href={siteConfig.linkedin} target="_blank" rel="noreferrer">LinkedIn</a>
        </div>
      </div>
    </footer>
  );
}
