import { describe, expect, it } from "vitest";
import type { DebateReport } from "@/lib/api/types";
import { aiCacheKey, createAiCache, generatedLabel } from "./ai-cache";

const report = { model: "m", judge_model: "m" } as DebateReport;

describe("aiCacheKey", () => {
  it("normalises the symbol and includes the day", () => {
    expect(aiCacheKey(" 2330.tw ", "2026-11-26")).toBe("2330.TW:2026-11-26");
  });
});

describe("createAiCache", () => {
  it("stores guide and debate separately for one key", () => {
    const cache = createAiCache();
    cache.saveGuide("A:d", { text: "g", model: "m", generatedAt: 1, price: 100 });
    cache.saveDebate("A:d", { report, generatedAt: 2, price: 101 });
    const entry = cache.get("A:d");
    expect(entry?.guide?.text).toBe("g");
    expect(entry?.debate?.price).toBe(101);
    expect(cache.get("B:d")).toBeUndefined();
  });

  it("clears follow-up messages when the debate is replaced, not when the guide is", () => {
    const cache = createAiCache();
    cache.saveDebate("A:d", { report, generatedAt: 1, price: null });
    cache.addMessage("A:d", { question: "q", answer: "a" });
    cache.saveGuide("A:d", { text: "g", model: null, generatedAt: 2, price: null });
    expect(cache.get("A:d")?.messages).toHaveLength(1);
    cache.saveDebate("A:d", { report, generatedAt: 3, price: null });
    expect(cache.get("A:d")?.messages).toHaveLength(0);
  });

  it("never mutates an entry it already handed out", () => {
    const cache = createAiCache();
    cache.saveDebate("A:d", { report, generatedAt: 1, price: null });
    const before = cache.get("A:d");
    cache.addMessage("A:d", { question: "q", answer: "a" });
    expect(before?.messages).toHaveLength(0);
  });

  it("evicts the least recently written entry past the limit", () => {
    const cache = createAiCache(2);
    const guide = { text: "g", model: null, generatedAt: 1, price: null };
    cache.saveGuide("A:d", guide);
    cache.saveGuide("B:d", guide);
    cache.saveGuide("A:d", guide); // A is now the freshest
    cache.saveGuide("C:d", guide);
    expect(cache.size).toBe(2);
    expect(cache.get("B:d")).toBeUndefined();
    expect(cache.get("A:d")).toBeDefined();
  });
});

describe("subscribe", () => {
  it("notifies on writes until unsubscribed", () => {
    const cache = createAiCache();
    let calls = 0;
    const off = cache.subscribe(() => {
      calls += 1;
    });
    cache.saveGuide("A:d", { text: "g", model: null, generatedAt: 1, price: null });
    cache.addMessage("A:d", { question: "q", answer: "a" });
    expect(calls).toBe(2);
    off();
    cache.clear();
    expect(calls).toBe(2);
  });
});

describe("generatedLabel", () => {
  const at = new Date(2026, 10, 26, 14, 5).getTime();
  it("says 今天稍早 for the same local day", () => {
    expect(generatedLabel(at, new Date(2026, 10, 26, 18, 0).getTime())).toBe("今天稍早產生 · 14:05");
  });
  it("shows the date for another day", () => {
    expect(generatedLabel(at, new Date(2026, 10, 27, 9, 0).getTime())).toBe("11/26 14:05 產生");
  });
});
