/* Generated from contracts/. Do not edit; run npm run gen:types. */

/**
 * A manager agent's output for one moment, after the number checker. The explanation goes to that manager's phone; the bid goes to the scorer. The conditional rules here are enforced by the JSON Schema; the generated pydantic and TypeScript types do not enforce them.
 */
export type Bid = {
  moment_id: string;
  fixture_id?: string;
  manager: string;
  /**
   * Rule 4.1(e): analyst gets the numbers and the evidence; casual gets one plain sentence.
   */
  style: "analyst" | "casual";
  /**
   * BCP 47 language tag: en or es (#16).
   */
  lang: string;
  /**
   * The agent's own claim, 0 to 1. The scorer weighs it; it does not decide.
   */
  urgency?: number;
  /**
   * Shown on the manager's phone.
   */
  explanation?: string;
  /**
   * English version when lang is not en, for judges and logs (rule 4.9).
   */
  explanation_en?: string;
  /**
   * One line arguing for the group screen, and the line the group screen shows if this bid wins: English, about the manager in the third person (#138).
   */
  pitch_to_host?: string;
  /**
   * @minItems 1
   */
  evidence_ids?: [string, ...string[]];
  /**
   * ok: checked and passed. retried: passed on the one retry. fallback: replaced by the plain stat after a failed check or a timeout. declined: the agent passed on a moment that does not change its manager's points; no phone message, no bid for the screen. Declined bids are not scored: they never appear in a host decision's scores and never reach the group screen.
   */
  status: "ok" | "retried" | "fallback" | "declined";
  latency_ms?: number;
  /**
   * Foundry deployment name that produced it.
   */
  model?: string;
  /**
   * Who wrote pitch_to_host: manager (the agent) or code. code on a plain-stat fallback, and when only the pitch failed its checks twice and code's line replaced it, keeping the agent's explanation (#138). Absent means manager unless status is fallback.
   */
  pitch_source?: "manager" | "code";
  /**
   * The fact from this manager's stake the agent chose to argue with (#88). Checked against the stake in the workflow, not here.
   */
  angle?: string;
};
