// SPDX-License-Identifier: MIT
// Copyright (c) 2026 Pablo Cano

import type { MetadataRoute } from "next";
import { docs } from "@/lib/docs";
import { siteConfig } from "@/lib/site";

export const dynamic = "force-static";

export default function sitemap(): MetadataRoute.Sitemap {
  const staticRoutes = ["", "/why", "/studio", "/roadmap", "/docs", "/validation"];

  return [
    ...staticRoutes.map((route) => ({
      url: `${siteConfig.url}${route}/`,
      changeFrequency: "weekly" as const,
      priority: route === "" ? 1 : 0.8,
    })),
    ...docs.map((doc) => ({
      url: `${siteConfig.url}/docs/${doc.slug}/`,
      changeFrequency: "weekly" as const,
      priority: 0.7,
    })),
  ];
}