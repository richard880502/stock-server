/**
 * Whether the compact query summary should be showing. Hysteresis keeps it from flickering
 * around the threshold: it appears once the full query bar has scrolled away and hides again
 * only when the user is back near the top.
 */
export function shouldShowCompactQuery(scrollY: number, wasShown: boolean): boolean {
  return wasShown ? scrollY > 24 : scrollY > 150;
}
