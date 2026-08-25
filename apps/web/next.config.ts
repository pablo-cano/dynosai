// SPDX-License-Identifier: MIT
// Copyright (c) 2026 Pablo Cano

import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  output: "export",
  images: { unoptimized: true },
  trailingSlash: true,
};

export default nextConfig;
