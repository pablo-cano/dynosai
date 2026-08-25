// SPDX-License-Identifier: MIT
// Copyright (c) 2026 Pablo Cano

import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const scriptDir = path.dirname(fileURLToPath(import.meta.url));
const webRoot = path.resolve(scriptDir, "..");
const repoRoot = path.resolve(webRoot, "../..");
const sourceRoots = ["app", "components", "lib"];
const sourceFiles = sourceRoots.flatMap((sourceRoot) => {
  const base = path.join(webRoot, sourceRoot);
  const files = [];
  const visit = (current) => {
    for (const entry of fs.readdirSync(current, { withFileTypes: true })) {
      const full = path.join(current, entry.name);
      if (entry.isDirectory()) visit(full);
      else if (/\.(ts|tsx)$/.test(entry.name)) files.push(full);
    }
  };
  visit(base);
  return files;
});

const failures = [];
for (const file of sourceFiles) {
  const relative = path.relative(webRoot, file);
  const content = fs.readFileSync(file, "utf8");
  if (!content.includes("SPDX-License-Identifier: MIT")) failures.push(`${relative}: missing SPDX header`);
  if (!content.includes("Pablo Cano")) failures.push(`${relative}: missing author header`);
}

const packageJson = JSON.parse(fs.readFileSync(path.join(webRoot, "package.json"), "utf8"));
if (packageJson.version !== "0.13.0") failures.push("package.json: website version must match DynosAI 0.13.0");
if (packageJson.license !== "MIT") failures.push("package.json: license must be MIT");
if (packageJson.author !== "Pablo Cano") failures.push("package.json: author is incorrect");

const docsSource = fs.readFileSync(path.join(webRoot, "lib/docs.ts"), "utf8");
const mappedSources = [...docsSource.matchAll(/source: "([^"]+)"/g)].map((match) => match[1]);
for (const source of mappedSources) {
  if (!fs.existsSync(path.join(repoRoot, source))) failures.push(`lib/docs.ts: mapped source does not exist: ${source}`);
}

for (const required of ["README.md", "GETTING_STARTED.md", "pyproject.toml"]) {
  const content = fs.readFileSync(path.join(repoRoot, required), "utf8");
  if (!content.includes("github.com/pablo-cano/dynosai")) failures.push(`${required}: official source repository is not referenced`);
}

if (failures.length) {
  console.error("DynosAI website source checks failed:\n" + failures.map((item) => `- ${item}`).join("\n"));
  process.exit(1);
}
console.log(`DynosAI website source checks: PASS (${sourceFiles.length} TypeScript files, ${mappedSources.length} docs routes checked)`);
