import type { ReactNode } from "react";
import { StanceChip } from "@/components/status-chip";
import type { Judgement } from "@/lib/api/types";

export function JudgementSummary({
  judgement,
  className = "judgement",
  heading,
}: {
  judgement: Judgement;
  className?: string;
  /** Replaces the plain stance chip (e.g. to add the judge model name). */
  heading?: ReactNode;
}) {
  return (
    <div className={className}>
      {heading ?? <StanceChip stance={judgement.stance} />}
      <h3>{judgement.headline}</h3>
      <p>{judgement.operation_guide}</p>
    </div>
  );
}

export function EvidenceColumns({
  evidenceLabel,
  judgement,
}: {
  evidenceLabel: string;
  judgement: Judgement;
}) {
  return (
    <div className="evidence-columns">
      <div>
        <h4 className="column-label">{evidenceLabel}</h4>
        <ul className="bullet-list">
          {judgement.key_evidence.map((item) => (
            <li key={item}>{item}</li>
          ))}
        </ul>
      </div>
      <div>
        <h4 className="column-label">風險與限制</h4>
        <ul className="bullet-list">
          {[...judgement.risk_warnings, ...judgement.limitations].map((item) => (
            <li key={item}>{item}</li>
          ))}
        </ul>
      </div>
    </div>
  );
}

export function ScenarioRow({ scenarios }: { scenarios: Judgement["scenarios"] }) {
  if (scenarios.length === 0) return null;
  return (
    <div className="scenario-row">
      {scenarios.map((scenario) => (
        <div className="scenario" key={scenario.name}>
          <strong>{scenario.name}</strong>
          <span className="scenario-condition">若 {scenario.condition}</span>
          <p>{scenario.response}</p>
        </div>
      ))}
    </div>
  );
}
