// SPDX-License-Identifier: MIT
// Copyright (c) 2026 Pablo Cano

import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { DocsSearch } from "@/components/docs-search";
import { DocsSidebar } from "@/components/docs-sidebar";
import { MarkdownPage } from "@/components/markdown-page";
import { docs, getDoc } from "@/lib/docs";

export const dynamicParams = false;

export function generateStaticParams() {
  return docs.map((doc) => ({ slug: doc.slug.split("/") }));
}

export async function generateMetadata({ params }: { params: Promise<{ slug?: string[] }> }): Promise<Metadata> {
  const { slug = [] } = await params;
  const doc = getDoc(slug.join("/"));
  if (!doc) return {};
  return { title: doc.title, description: doc.description };
}

export default async function DocPage({ params }: { params: Promise<{ slug?: string[] }> }) {
  const { slug = [] } = await params;
  if (slug.length === 0) notFound();
  const activeSlug = slug.join("/");
  const doc = getDoc(activeSlug);
  if (!doc) notFound();

  return (
    <div className="mx-auto max-w-7xl px-5 py-10 lg:px-8">
      <div className="mb-8 flex justify-end"><DocsSearch docs={docs} /></div>
      <div className="lg:flex lg:gap-10">
        <DocsSidebar activeSlug={activeSlug} />
        <article className="min-w-0 flex-1 lg:max-w-4xl">
          <MarkdownPage content={doc.content} />
        </article>
      </div>
    </div>
  );
}
