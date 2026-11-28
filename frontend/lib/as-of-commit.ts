const ISO_DATE = /^(\d{4})-(\d{2})-(\d{2})$/;

/** Earliest as-of date the controls accept; anything older is a half-typed year, not a real query. */
export const MIN_AS_OF_YEAR = 1990;

/**
 * Whether a date input's text is a finished, real calendar date no later than `today`
 * (both `YYYY-MM-DD`). A native date field reports `0002-11-28` while its year is still being
 * typed, so "looks like a date" is not enough to start a request.
 */
export function isCommittableAsOf(text: string, today: string): boolean {
  const match = ISO_DATE.exec(text);
  if (!match) return false;
  const [year, month, day] = [Number(match[1]), Number(match[2]), Number(match[3])];
  if (year < MIN_AS_OF_YEAR) return false;
  const parsed = new Date(Date.UTC(year, month - 1, day));
  const real =
    parsed.getUTCFullYear() === year && parsed.getUTCMonth() === month - 1 && parsed.getUTCDate() === day;
  return real && text <= today;
}
