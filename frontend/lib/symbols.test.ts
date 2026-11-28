import { describe, expect, it } from "vitest";
import { isEtfSymbol } from "./symbols";

describe("isEtfSymbol", () => {
  it("recognises every ETF code shape, with or without a market suffix", () => {
    for (const symbol of ["0050", "0050.TW", "00878.TW", "006208.TW", "00631L.TW", "00632R", "00679B.TWO", "00400A.TW"])
      expect(isEtfSymbol(symbol)).toBe(true);
  });

  it("leaves stocks, ETNs and indexes alone", () => {
    for (const symbol of ["2330.TW", "6488.TWO", "1101B.TW", "020000.TW", "02001L.TW", "^TWII", ""])
      expect(isEtfSymbol(symbol)).toBe(false);
  });
});
