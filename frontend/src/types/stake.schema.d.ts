/* Generated from contracts/. Do not edit; run npm run gen:types. */

/**
 * What one key moment means for one manager, computed by the points engine. Deterministic: the only source of numbers for the agents.
 */
export interface Stake {
  moment_id: string;
  fixture_id?: string;
  manager: string;
  /**
   * Match time in milliseconds from kick-off. 90 minutes is 5400000; stoppage time runs past it.
   */
  t: number;
  /**
   * Impact points this moment adds or removes, captain multiplier included.
   */
  delta_points: number;
  /**
   * League total after this moment.
   */
  total_points: number;
  rank_before?: number;
  rank_after?: number;
  players?: string[];
  /**
   * Which scoring rule fired, for example save_tier_reached.
   */
  reason_code: string;
  /**
   * Progress towards the next tier, for 'one more save' style lines.
   */
  milestone?: {
    name: string;
    progress: number;
    target: number;
  };
  /**
   * True if this manager's agent should be called for this moment.
   */
  above_threshold: boolean;
  /**
   * @minItems 1
   */
  evidence: [Evidence, ...Evidence[]];
  /**
   * What the moment means for this manager beyond the points (#87). Fact ids are unique within a stake, and every evidence id a fact cites is in this stake's evidence; the points engine guarantees both. rank_change is emitted only when the position changed.
   *
   * Items: A fact about what a moment means for one manager, computed by the points engine (#87). Agents may only cite facts from their own manager's stake.
   */
  facts?: (
    | FactDifferential
    | FactTemplate
    | FactCaptainSwing
    | FactRankChange
    | FactExpectedVsActual
    | FactPassQuality
    | FactElsewhere
  )[];
}
/**
 * One number an agent may use. Every number in any text shown to a viewer must match the value of an evidence item it cites.
 */
export interface Evidence {
  id: string;
  label: string;
  value: number;
  unit?: "ip" | "xg" | "km_h" | "m" | "count" | "ratio" | "minute";
  source: {
    kind: "event" | "computed";
    event_seq?: number[];
    /**
     * Name and version of the rule or metric, for example save_points@v1.
     */
    formula?: string;
  };
}
/**
 * Only this manager owns the player involved.
 */
export interface FactDifferential {
  id: string;
  kind: "differential";
  /**
   * @minItems 1
   */
  evidence_ids: [string, ...string[]];
  player: string;
  owners: 1;
}
/**
 * At least three managers own the player involved, so the moment barely moves the table between them.
 */
export interface FactTemplate {
  id: string;
  kind: "template";
  /**
   * @minItems 1
   */
  evidence_ids: [string, ...string[]];
  player: string;
  owners: number;
}
/**
 * This manager's captain scored double against managers who own but do not captain him.
 */
export interface FactCaptainSwing {
  id: string;
  kind: "captain_swing";
  /**
   * @minItems 1
   */
  evidence_ids: [string, ...string[]];
  player: string;
  /**
   * Points the armband added; a captain who lost points is no swing.
   */
  extra_points: number;
  /**
   * @minItems 1
   */
  versus: [string, ...string[]];
}
/**
 * The moment moved this manager's league position; emitted only when rank_before differs from rank_after.
 */
export interface FactRankChange {
  id: string;
  kind: "rank_change";
  /**
   * @minItems 1
   */
  evidence_ids: [string, ...string[]];
  rank_before: number;
  rank_after: number;
}
/**
 * Goal points a player has scored so far against the goal points expected from the xG of his chances, both before any captain multiplier; other awards count on neither side.
 */
export interface FactExpectedVsActual {
  id: string;
  kind: "expected_vs_actual";
  /**
   * @minItems 1
   */
  evidence_ids: [string, ...string[]];
  player: string;
  /**
   * His xG so far times the points for a goal.
   */
  expected_points: number;
  /**
   * His goals so far times the points for a goal.
   */
  actual_points: number;
  xg: number;
}
/**
 * The pass that led to the moment.
 */
export interface FactPassQuality {
  id: string;
  kind: "pass_quality";
  /**
   * @minItems 1
   */
  evidence_ids: [string, ...string[]];
  player: string;
  distance_m: number;
  difficulty: number;
}
/**
 * In a gameweek: this manager's points so far from every fixture but the stake's own, the one number about other fixtures an agent may cite (#94). Emitted only when it is not zero.
 */
export interface FactElsewhere {
  id: string;
  kind: "elsewhere";
  /**
   * @minItems 1
   */
  evidence_ids: [string, ...string[]];
  /**
   * Points from the other fixtures, captain multiplier included.
   */
  points: number;
}
