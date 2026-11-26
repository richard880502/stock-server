import { describe, expect, it } from "vitest";
import { parseView, resolveView } from "./view-mode";

const base = { clicked: null, param: null, stored: null, narrow: false } as const;

describe("resolveView", () => {
  it("defaults to the terminal on wide screens and the list on phones", () => {
    expect(resolveView(base)).toBe("terminal");
    expect(resolveView({ ...base, narrow: true })).toBe("list");
  });
  it("prefers click, then URL, then saved choice", () => {
    expect(resolveView({ clicked: "list", param: "terminal", stored: "terminal", narrow: false })).toBe("list");
    expect(resolveView({ ...base, param: "list", stored: "terminal" })).toBe("list");
    expect(resolveView({ ...base, stored: "list" })).toBe("list");
  });
  it("ignores junk values", () => {
    expect(parseView("x")).toBeNull();
    expect(resolveView({ ...base, param: "x", stored: "y", narrow: true })).toBe("list");
  });
});
