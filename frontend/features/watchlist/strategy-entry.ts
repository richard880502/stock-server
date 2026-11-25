import type { DebateReport } from "@/lib/api/types";

export type StrategyEntry = {
  held: boolean;
  report: DebateReport | null;
  previous: DebateReport | null;
  messages: { question: string; answer: string }[];
  dataThrough: string | null;
  flowsThrough: string | null;
};

export const emptyEntry: StrategyEntry = {
  held: false, report: null, previous: null, messages: [], dataThrough: null, flowsThrough: null,
};
