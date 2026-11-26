/**
 * Move of the live price since an AI result was generated, as a ratio (0.012 = +1.2%).
 * Null when either price is missing or the base is not a positive number.
 */
export function changeSinceGeneration(
  generatedPrice: number | null | undefined,
  livePrice: number | null | undefined,
): number | null {
  if (generatedPrice == null || livePrice == null) return null;
  if (!Number.isFinite(generatedPrice) || !Number.isFinite(livePrice) || generatedPrice <= 0) {
    return null;
  }
  return livePrice / generatedPrice - 1;
}
