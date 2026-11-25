/** Matching rules for the ⌘K palette, kept pure so they can be tested. */

/** A code the user can open directly before search answers: 2330, 2330.TW, 6488.TWO, ^TWII. */
export function looksLikeSymbol(text: string): boolean {
  return /^[0-9A-Z^][0-9A-Z.^]{1,15}$/i.test(text.trim());
}

/** Case-insensitive substring match; an empty query matches everything. */
export function matchesQuery(label: string, query: string): boolean {
  const needle = query.trim().toLowerCase();
  return needle === "" || label.toLowerCase().includes(needle);
}
