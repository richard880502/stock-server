/** Calendar days from `from` to `to` (ISO dates). */
function daysBetween(from: string, to: string): number {
  return Math.round((Date.parse(`${to}T00:00:00Z`) - Date.parse(`${from}T00:00:00Z`)) / 86_400_000);
}

/**
 * Whether to warn that the market environment is older than the chosen as-of date.
 *
 * On the default date (today) the environment normally trails by a session until the
 * exchange publishes after-market reports, or by a weekend on Monday; that is expected and
 * already visible in the card's date, so it is not worth an amber notice every morning.
 * Warn when the viewer picked another date, or when the gap is longer than a weekend.
 */
export function showEnvironmentLagNotice(environmentAsOf: string, asOf: string, today: string): boolean {
  if (environmentAsOf >= asOf) return false;
  if (asOf !== today) return true;
  return daysBetween(environmentAsOf, asOf) > 4;
}
