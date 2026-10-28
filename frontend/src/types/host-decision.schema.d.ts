/* Generated from contracts/. Do not edit; run npm run gen:types. */

/**
 * Which manager's moment the group screen shows, and who decided. Also the trace for the video and the input to the recap.
 */
export type HostDecision = {
  moment_id: string;
  fixture_id?: string;
  /**
   * Match time in milliseconds from kick-off. 90 minutes is 5400000; stoppage time runs past it.
   */
  t: number;
  chosen_manager: string;
  /**
   * scorer: clear winner, no model call. host: close call settled by the host agent. fallback: host timed out, scorer's pick used.
   */
  source: "scorer" | "host" | "fallback";
  /**
   * Whether the scorer had no clear winner. Only a close call's losers count as overruled.
   */
  close_call: boolean;
  /**
   * Shown on the group screen when source is host.
   */
  reason?: string;
  /**
   * One row per bid that was scored. Declined bids are not scored and do not appear.
   *
   * @minItems 1
   * @maxItems 5
   */
  scores:
    | [
        {
          manager: string;
          score: number;
          airtime_share: number;
          overruled_count: number;
          /**
           * The manager agent's one-line bid for the group screen.
           */
          pitch?: string;
          /**
           * How the bid was made: ok, retried after a failed check, or a plain-stat fallback.
           */
          status?: "ok" | "retried" | "fallback";
          /**
           * How long the bid took.
           */
          latency_ms?: number;
          /**
           * Who wrote the pitch: manager (the agent) or code (#138).
           */
          pitch_source?: "manager" | "code";
          angle?: string;
        }
      ]
    | [
        {
          manager: string;
          score: number;
          airtime_share: number;
          overruled_count: number;
          /**
           * The manager agent's one-line bid for the group screen.
           */
          pitch?: string;
          /**
           * How the bid was made: ok, retried after a failed check, or a plain-stat fallback.
           */
          status?: "ok" | "retried" | "fallback";
          /**
           * How long the bid took.
           */
          latency_ms?: number;
          /**
           * Who wrote the pitch: manager (the agent) or code (#138).
           */
          pitch_source?: "manager" | "code";
          angle?: string;
        },
        {
          manager: string;
          score: number;
          airtime_share: number;
          overruled_count: number;
          /**
           * The manager agent's one-line bid for the group screen.
           */
          pitch?: string;
          /**
           * How the bid was made: ok, retried after a failed check, or a plain-stat fallback.
           */
          status?: "ok" | "retried" | "fallback";
          /**
           * How long the bid took.
           */
          latency_ms?: number;
          /**
           * Who wrote the pitch: manager (the agent) or code (#138).
           */
          pitch_source?: "manager" | "code";
          angle?: string;
        }
      ]
    | [
        {
          manager: string;
          score: number;
          airtime_share: number;
          overruled_count: number;
          /**
           * The manager agent's one-line bid for the group screen.
           */
          pitch?: string;
          /**
           * How the bid was made: ok, retried after a failed check, or a plain-stat fallback.
           */
          status?: "ok" | "retried" | "fallback";
          /**
           * How long the bid took.
           */
          latency_ms?: number;
          /**
           * Who wrote the pitch: manager (the agent) or code (#138).
           */
          pitch_source?: "manager" | "code";
          angle?: string;
        },
        {
          manager: string;
          score: number;
          airtime_share: number;
          overruled_count: number;
          /**
           * The manager agent's one-line bid for the group screen.
           */
          pitch?: string;
          /**
           * How the bid was made: ok, retried after a failed check, or a plain-stat fallback.
           */
          status?: "ok" | "retried" | "fallback";
          /**
           * How long the bid took.
           */
          latency_ms?: number;
          /**
           * Who wrote the pitch: manager (the agent) or code (#138).
           */
          pitch_source?: "manager" | "code";
          angle?: string;
        },
        {
          manager: string;
          score: number;
          airtime_share: number;
          overruled_count: number;
          /**
           * The manager agent's one-line bid for the group screen.
           */
          pitch?: string;
          /**
           * How the bid was made: ok, retried after a failed check, or a plain-stat fallback.
           */
          status?: "ok" | "retried" | "fallback";
          /**
           * How long the bid took.
           */
          latency_ms?: number;
          /**
           * Who wrote the pitch: manager (the agent) or code (#138).
           */
          pitch_source?: "manager" | "code";
          angle?: string;
        }
      ]
    | [
        {
          manager: string;
          score: number;
          airtime_share: number;
          overruled_count: number;
          /**
           * The manager agent's one-line bid for the group screen.
           */
          pitch?: string;
          /**
           * How the bid was made: ok, retried after a failed check, or a plain-stat fallback.
           */
          status?: "ok" | "retried" | "fallback";
          /**
           * How long the bid took.
           */
          latency_ms?: number;
          /**
           * Who wrote the pitch: manager (the agent) or code (#138).
           */
          pitch_source?: "manager" | "code";
          angle?: string;
        },
        {
          manager: string;
          score: number;
          airtime_share: number;
          overruled_count: number;
          /**
           * The manager agent's one-line bid for the group screen.
           */
          pitch?: string;
          /**
           * How the bid was made: ok, retried after a failed check, or a plain-stat fallback.
           */
          status?: "ok" | "retried" | "fallback";
          /**
           * How long the bid took.
           */
          latency_ms?: number;
          /**
           * Who wrote the pitch: manager (the agent) or code (#138).
           */
          pitch_source?: "manager" | "code";
          angle?: string;
        },
        {
          manager: string;
          score: number;
          airtime_share: number;
          overruled_count: number;
          /**
           * The manager agent's one-line bid for the group screen.
           */
          pitch?: string;
          /**
           * How the bid was made: ok, retried after a failed check, or a plain-stat fallback.
           */
          status?: "ok" | "retried" | "fallback";
          /**
           * How long the bid took.
           */
          latency_ms?: number;
          /**
           * Who wrote the pitch: manager (the agent) or code (#138).
           */
          pitch_source?: "manager" | "code";
          angle?: string;
        },
        {
          manager: string;
          score: number;
          airtime_share: number;
          overruled_count: number;
          /**
           * The manager agent's one-line bid for the group screen.
           */
          pitch?: string;
          /**
           * How the bid was made: ok, retried after a failed check, or a plain-stat fallback.
           */
          status?: "ok" | "retried" | "fallback";
          /**
           * How long the bid took.
           */
          latency_ms?: number;
          /**
           * Who wrote the pitch: manager (the agent) or code (#138).
           */
          pitch_source?: "manager" | "code";
          angle?: string;
        }
      ]
    | [
        {
          manager: string;
          score: number;
          airtime_share: number;
          overruled_count: number;
          /**
           * The manager agent's one-line bid for the group screen.
           */
          pitch?: string;
          /**
           * How the bid was made: ok, retried after a failed check, or a plain-stat fallback.
           */
          status?: "ok" | "retried" | "fallback";
          /**
           * How long the bid took.
           */
          latency_ms?: number;
          /**
           * Who wrote the pitch: manager (the agent) or code (#138).
           */
          pitch_source?: "manager" | "code";
          angle?: string;
        },
        {
          manager: string;
          score: number;
          airtime_share: number;
          overruled_count: number;
          /**
           * The manager agent's one-line bid for the group screen.
           */
          pitch?: string;
          /**
           * How the bid was made: ok, retried after a failed check, or a plain-stat fallback.
           */
          status?: "ok" | "retried" | "fallback";
          /**
           * How long the bid took.
           */
          latency_ms?: number;
          /**
           * Who wrote the pitch: manager (the agent) or code (#138).
           */
          pitch_source?: "manager" | "code";
          angle?: string;
        },
        {
          manager: string;
          score: number;
          airtime_share: number;
          overruled_count: number;
          /**
           * The manager agent's one-line bid for the group screen.
           */
          pitch?: string;
          /**
           * How the bid was made: ok, retried after a failed check, or a plain-stat fallback.
           */
          status?: "ok" | "retried" | "fallback";
          /**
           * How long the bid took.
           */
          latency_ms?: number;
          /**
           * Who wrote the pitch: manager (the agent) or code (#138).
           */
          pitch_source?: "manager" | "code";
          angle?: string;
        },
        {
          manager: string;
          score: number;
          airtime_share: number;
          overruled_count: number;
          /**
           * The manager agent's one-line bid for the group screen.
           */
          pitch?: string;
          /**
           * How the bid was made: ok, retried after a failed check, or a plain-stat fallback.
           */
          status?: "ok" | "retried" | "fallback";
          /**
           * How long the bid took.
           */
          latency_ms?: number;
          /**
           * Who wrote the pitch: manager (the agent) or code (#138).
           */
          pitch_source?: "manager" | "code";
          angle?: string;
        },
        {
          manager: string;
          score: number;
          airtime_share: number;
          overruled_count: number;
          /**
           * The manager agent's one-line bid for the group screen.
           */
          pitch?: string;
          /**
           * How the bid was made: ok, retried after a failed check, or a plain-stat fallback.
           */
          status?: "ok" | "retried" | "fallback";
          /**
           * How long the bid took.
           */
          latency_ms?: number;
          /**
           * Who wrote the pitch: manager (the agent) or code (#138).
           */
          pitch_source?: "manager" | "code";
          angle?: string;
        }
      ];
  latency_ms?: number;
  /**
   * Set on the decision of the moment that spent a "simulate failure" press (#90): the arm id the chaos endpoint returned, and the one manager whose agent was skipped (absent: every agent).
   */
  knocked_out?: {
    arm: number;
    manager?: string;
  };
  /**
   * Why the host chose. rank_change, differential and captain_swing name a fact kind: the chosen contender's angle must be a fact of that kind, checked by code. bigger_swing, least_airtime, most_close_calls_lost, dead_heat and scorer_pick are scorer reasons from the points and the ledger (#88).
   */
  because?:
    | "bigger_swing"
    | "least_airtime"
    | "most_close_calls_lost"
    | "dead_heat"
    | "scorer_pick"
    | "rank_change"
    | "differential"
    | "captain_swing";
  /**
   * The two pitches the group screen shows on a close call before the verdict (#89). Two different managers; set on every close call settled by the host.
   *
   * @minItems 2
   * @maxItems 2
   */
  contenders?: [
    {
      manager: string;
      pitch: string;
      angle?: string;
    },
    {
      manager: string;
      pitch: string;
      angle?: string;
    }
  ];
};
