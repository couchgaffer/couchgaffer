/* Generated from contracts/. Do not edit; run npm run gen:types. */

/**
 * One synthetic event from the simulator (rule 4.1 stage a). seq is gap-free and strictly increasing per match; in a gameweek, strictly increasing across all its fixtures, so evidence event_seq never needs a fixture (#94).
 */
export type MatchEvent = {
  seq: number;
  fixture_id?: string;
  /**
   * Match time in milliseconds from kick-off. 90 minutes is 5400000; stoppage time runs past it.
   */
  t: number;
  type:
    | "kickoff"
    | "pass"
    | "carry"
    | "dribble"
    | "pressure"
    | "tackle"
    | "interception"
    | "possession_change"
    | "shot"
    | "save"
    | "goal"
    | "foul"
    | "card"
    | "corner"
    | "free_kick"
    | "offside"
    | "substitution"
    | "half_time"
    | "full_time";
  team: "home" | "away";
  player?: string;
  /**
   * Pitch coordinate, normalised. x runs from the home goal line (0) to the away goal line (1); y runs from the left touchline (0) to the right (1), seen from the home end. The pitch is 105 m by 68 m.
   */
  x: number;
  /**
   * Pitch coordinate, normalised. x runs from the home goal line (0) to the away goal line (1); y runs from the left touchline (0) to the right (1), seen from the home end. The pitch is 105 m by 68 m.
   */
  y: number;
  /**
   * Pitch coordinate, normalised. x runs from the home goal line (0) to the away goal line (1); y runs from the left touchline (0) to the right (1), seen from the home end. The pitch is 105 m by 68 m.
   */
  end_x?: number;
  /**
   * Pitch coordinate, normalised. x runs from the home goal line (0) to the away goal line (1); y runs from the left touchline (0) to the right (1), seen from the home end. The pitch is 105 m by 68 m.
   */
  end_y?: number;
  receiver?: string;
  /**
   * The defender, keeper or fouled player involved.
   */
  opponent?: string;
  outcome?: "success" | "fail" | "saved" | "blocked" | "off_target" | "woodwork" | "goal" | "yellow" | "red";
  /**
   * Set when the player passes the speed threshold, for name tagging (rule 4.1 ii).
   */
  player_speed_km_h?: number;
  pass?: {
    distance_m: number;
    /**
     * 0 easy to 1 hardest.
     */
    difficulty: number;
    progressive: boolean;
    ball_speed_km_h?: number;
  };
  shot?: {
    xg: number;
    speed_km_h: number;
    body_part: "right_foot" | "left_foot" | "head" | "other";
  };
  /**
   * Set on every event in a key moment.
   */
  moment_id?: string;
  /**
   * True on the last event of a key moment. The points engine computes stakes when it sees this.
   */
  climax?: boolean;
  scenario?:
    | "build_up"
    | "counterattack"
    | "high_press"
    | "set_piece"
    | "low_block"
    | "keeper_save_run"
    | "late_equaliser"
    | "filler";
};
