import { VerdictSkeleton } from "@/features/signal/verdict-hero";

/** The verdict placeholder shown while a stock's signal is on its way. */
export function VerdictPlaceholder() {
  return <VerdictSkeleton />;
}

/** Evidence placeholder: reserves the height of the factor and alert cards. */
export function EvidenceSkeleton() {
  return (
    <div className="grid">
      <section className="card span-5" aria-hidden="true">
        <span className="skeleton line" style={{ width: "35%" }} />
        {[0, 1, 2, 3, 4].map((i) => (
          <span key={i} className="skeleton line" style={{ marginTop: 18 }} />
        ))}
      </section>
      <section className="card span-7" aria-hidden="true">
        <span className="skeleton line" style={{ width: "30%" }} />
        {[0, 1, 2].map((i) => (
          <span key={i} className="skeleton block" style={{ height: 44, marginTop: 14 }} />
        ))}
      </section>
    </div>
  );
}
