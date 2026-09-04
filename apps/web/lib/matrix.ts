// SPDX-License-Identifier: MIT
// Copyright (c) 2026 Pablo Cano

import fs from "node:fs";
import path from "node:path";

export type LiveMatrixCell = {
  provider: string;
  mode: string;
  status: string;
};

type LiveMatrixFile = {
  cells?: LiveMatrixCell[];
  all_passed?: boolean;
  copied_from_historical?: boolean;
};

function repositoryRoot() {
  return path.resolve(process.cwd(), "../..");
}

export function loadLiveMatrixCells(): LiveMatrixCell[] {
  const file = path.join(repositoryRoot(), "docs/validation/matrix-1.0.json");
  const payload = JSON.parse(fs.readFileSync(file, "utf8")) as LiveMatrixFile;
  const cells = payload.cells ?? [];
  return cells.map((cell) => ({
    provider: cell.provider,
    mode: cell.mode,
    status: cell.status,
  }));
}

export function liveMatrixHeadline(cells: LiveMatrixCell[]) {
  const statuses = new Set(cells.map((cell) => cell.status));
  if (statuses.size === 1 && statuses.has("not_run")) {
    return "1.0 live cells stay not_run until real provider runs exist.";
  }
  if (cells.length === 4 && cells.every((cell) => cell.status === "pass")) {
    return "MATRIX_1.0 live cells are pass from recorded provider trials.";
  }
  return "MATRIX_1.0 live cells record real trials; historical 0.13 scores are not copied.";
}
