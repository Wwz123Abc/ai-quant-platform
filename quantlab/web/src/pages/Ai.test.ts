import { describe, expect, it } from "vitest";
import type { AiRun } from "../api/client";
import { latestPerSymbol } from "./Ai";

const run = (id: number, symbol: string, status: AiRun["status"]) => ({ id, symbol, status }) as AiRun;

describe("latestPerSymbol", () => {
  it("shows only the newest report of each stock, so the new one replaces the old", () => {
    const runs = [run(9, "QQQM", "done"), run(8, "BAC", "done"), run(5, "QQQM", "done"), run(2, "BAC", "done")];
    expect(latestPerSymbol(runs).map((r) => r.id)).toEqual([9, 8]);
  });
  it("keeps the last finished report while a newer run is still going or failed", () => {
    const runs = [run(9, "QQQM", "running"), run(7, "QQQM", "failed"), run(5, "QQQM", "done"), run(2, "QQQM", "done")];
    expect(latestPerSymbol(runs).map((r) => r.id)).toEqual([9, 5]);
  });
});
