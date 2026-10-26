import { describe, expect, it } from "vitest";
import { errorMessage } from "../api/client";
import { drawdown, money, parseInts, pct, pctPlain, tone, when } from "./format";

describe("format", () => {
  it("signs percentages and hides non-numbers", () => {
    expect(pct(0.1234)).toBe("+12.3%");
    expect(pct(-0.045)).toBe("−4.5%");
    expect(pct(0)).toBe("0.0%");
    expect(pct(-0.00001)).toBe("0.0%");
    expect(pct(null)).toBe("—");
    expect(pct(Number.NaN)).toBe("—");
    expect(pctPlain(0.171)).toBe("17.1%");
  });

  it("formats money with a real minus sign", () => {
    expect(money(113940.4)).toBe("$113,940");
    expect(money(-1500)).toBe("−$1,500");
    expect(money(undefined)).toBe("—");
  });

  it("shows server times in Beijing time", () => {
    expect(when("2026-10-23T08:24:10+00:00")).toBe("2026-10-23 16:24");
    expect(when("2026-10-23T08:24:10")).toBe("2026-10-23 16:24"); // no zone: the server writes UTC
    expect(when("2026-10-22T20:05:00Z")).toBe("2026-10-23 04:05");
    expect(when(null)).toBe("—");
    expect(when("not a date")).toBe("—");
  });

  it("picks a tone", () => {
    expect(tone(0.01)).toBe("up");
    expect(tone(-0.01)).toBe("down");
    expect(tone(0)).toBe("flat");
    expect(tone(null)).toBe("flat");
  });

  it("computes drawdowns from the running peak", () => {
    expect(drawdown([100, 120, 90, 130, 65])).toEqual([0, 0, -0.25, 0, -0.5]);
  });

  it("parses integer lists", () => {
    expect(parseInts("5,10， 20 x 3.5 -1")).toEqual([5, 10, 20]);
  });
});

describe("errorMessage", () => {
  it("reads FastAPI error bodies", () => {
    expect(errorMessage("没有叫 x 的模拟账户", 404)).toBe("没有叫 x 的模拟账户");
    expect(errorMessage([{ msg: "Value error, 短均线天数要小于长均线天数" }], 422)).toBe("短均线天数要小于长均线天数");
    expect(errorMessage(undefined, 500)).toBe("请求失败（500）");
    expect(errorMessage(undefined, 0)).toContain("连不上服务");
  });
});
