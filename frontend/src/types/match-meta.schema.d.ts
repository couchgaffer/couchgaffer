/* Generated from contracts/. Do not edit; run npm run gen:types. */

/**
 * Sent once when a session starts. All clubs and players are invented (rule 4.7).
 */
export interface MatchMeta {
  match_id: string;
  /**
   * Set on each fixture of a gameweek message; absent on a single match's meta.
   */
  fixture_id?: string;
  seed: number;
  /**
   * Match seconds per real second. The plan uses 30.
   */
  speed: number;
  /**
   * Real milliseconds the screens run behind the simulator: the broadcast delay the agents work inside.
   */
  playback_delay_ms?: number;
  home: Team;
  away: Team;
}
export interface Team {
  id: string;
  name: string;
  short_name: string;
  colour: string;
  formation: string;
  /**
   * @minItems 11
   * @maxItems 16
   */
  players:
    | [
        {
          id: string;
          name: string;
          shirt: number;
          position: "GK" | "DEF" | "MID" | "FWD";
          starter?: boolean;
        },
        {
          id: string;
          name: string;
          shirt: number;
          position: "GK" | "DEF" | "MID" | "FWD";
          starter?: boolean;
        },
        {
          id: string;
          name: string;
          shirt: number;
          position: "GK" | "DEF" | "MID" | "FWD";
          starter?: boolean;
        },
        {
          id: string;
          name: string;
          shirt: number;
          position: "GK" | "DEF" | "MID" | "FWD";
          starter?: boolean;
        },
        {
          id: string;
          name: string;
          shirt: number;
          position: "GK" | "DEF" | "MID" | "FWD";
          starter?: boolean;
        },
        {
          id: string;
          name: string;
          shirt: number;
          position: "GK" | "DEF" | "MID" | "FWD";
          starter?: boolean;
        },
        {
          id: string;
          name: string;
          shirt: number;
          position: "GK" | "DEF" | "MID" | "FWD";
          starter?: boolean;
        },
        {
          id: string;
          name: string;
          shirt: number;
          position: "GK" | "DEF" | "MID" | "FWD";
          starter?: boolean;
        },
        {
          id: string;
          name: string;
          shirt: number;
          position: "GK" | "DEF" | "MID" | "FWD";
          starter?: boolean;
        },
        {
          id: string;
          name: string;
          shirt: number;
          position: "GK" | "DEF" | "MID" | "FWD";
          starter?: boolean;
        },
        {
          id: string;
          name: string;
          shirt: number;
          position: "GK" | "DEF" | "MID" | "FWD";
          starter?: boolean;
        }
      ]
    | [
        {
          id: string;
          name: string;
          shirt: number;
          position: "GK" | "DEF" | "MID" | "FWD";
          starter?: boolean;
        },
        {
          id: string;
          name: string;
          shirt: number;
          position: "GK" | "DEF" | "MID" | "FWD";
          starter?: boolean;
        },
        {
          id: string;
          name: string;
          shirt: number;
          position: "GK" | "DEF" | "MID" | "FWD";
          starter?: boolean;
        },
        {
          id: string;
          name: string;
          shirt: number;
          position: "GK" | "DEF" | "MID" | "FWD";
          starter?: boolean;
        },
        {
          id: string;
          name: string;
          shirt: number;
          position: "GK" | "DEF" | "MID" | "FWD";
          starter?: boolean;
        },
        {
          id: string;
          name: string;
          shirt: number;
          position: "GK" | "DEF" | "MID" | "FWD";
          starter?: boolean;
        },
        {
          id: string;
          name: string;
          shirt: number;
          position: "GK" | "DEF" | "MID" | "FWD";
          starter?: boolean;
        },
        {
          id: string;
          name: string;
          shirt: number;
          position: "GK" | "DEF" | "MID" | "FWD";
          starter?: boolean;
        },
        {
          id: string;
          name: string;
          shirt: number;
          position: "GK" | "DEF" | "MID" | "FWD";
          starter?: boolean;
        },
        {
          id: string;
          name: string;
          shirt: number;
          position: "GK" | "DEF" | "MID" | "FWD";
          starter?: boolean;
        },
        {
          id: string;
          name: string;
          shirt: number;
          position: "GK" | "DEF" | "MID" | "FWD";
          starter?: boolean;
        },
        {
          id: string;
          name: string;
          shirt: number;
          position: "GK" | "DEF" | "MID" | "FWD";
          starter?: boolean;
        }
      ]
    | [
        {
          id: string;
          name: string;
          shirt: number;
          position: "GK" | "DEF" | "MID" | "FWD";
          starter?: boolean;
        },
        {
          id: string;
          name: string;
          shirt: number;
          position: "GK" | "DEF" | "MID" | "FWD";
          starter?: boolean;
        },
        {
          id: string;
          name: string;
          shirt: number;
          position: "GK" | "DEF" | "MID" | "FWD";
          starter?: boolean;
        },
        {
          id: string;
          name: string;
          shirt: number;
          position: "GK" | "DEF" | "MID" | "FWD";
          starter?: boolean;
        },
        {
          id: string;
          name: string;
          shirt: number;
          position: "GK" | "DEF" | "MID" | "FWD";
          starter?: boolean;
        },
        {
          id: string;
          name: string;
          shirt: number;
          position: "GK" | "DEF" | "MID" | "FWD";
          starter?: boolean;
        },
        {
          id: string;
          name: string;
          shirt: number;
          position: "GK" | "DEF" | "MID" | "FWD";
          starter?: boolean;
        },
        {
          id: string;
          name: string;
          shirt: number;
          position: "GK" | "DEF" | "MID" | "FWD";
          starter?: boolean;
        },
        {
          id: string;
          name: string;
          shirt: number;
          position: "GK" | "DEF" | "MID" | "FWD";
          starter?: boolean;
        },
        {
          id: string;
          name: string;
          shirt: number;
          position: "GK" | "DEF" | "MID" | "FWD";
          starter?: boolean;
        },
        {
          id: string;
          name: string;
          shirt: number;
          position: "GK" | "DEF" | "MID" | "FWD";
          starter?: boolean;
        },
        {
          id: string;
          name: string;
          shirt: number;
          position: "GK" | "DEF" | "MID" | "FWD";
          starter?: boolean;
        },
        {
          id: string;
          name: string;
          shirt: number;
          position: "GK" | "DEF" | "MID" | "FWD";
          starter?: boolean;
        }
      ]
    | [
        {
          id: string;
          name: string;
          shirt: number;
          position: "GK" | "DEF" | "MID" | "FWD";
          starter?: boolean;
        },
        {
          id: string;
          name: string;
          shirt: number;
          position: "GK" | "DEF" | "MID" | "FWD";
          starter?: boolean;
        },
        {
          id: string;
          name: string;
          shirt: number;
          position: "GK" | "DEF" | "MID" | "FWD";
          starter?: boolean;
        },
        {
          id: string;
          name: string;
          shirt: number;
          position: "GK" | "DEF" | "MID" | "FWD";
          starter?: boolean;
        },
        {
          id: string;
          name: string;
          shirt: number;
          position: "GK" | "DEF" | "MID" | "FWD";
          starter?: boolean;
        },
        {
          id: string;
          name: string;
          shirt: number;
          position: "GK" | "DEF" | "MID" | "FWD";
          starter?: boolean;
        },
        {
          id: string;
          name: string;
          shirt: number;
          position: "GK" | "DEF" | "MID" | "FWD";
          starter?: boolean;
        },
        {
          id: string;
          name: string;
          shirt: number;
          position: "GK" | "DEF" | "MID" | "FWD";
          starter?: boolean;
        },
        {
          id: string;
          name: string;
          shirt: number;
          position: "GK" | "DEF" | "MID" | "FWD";
          starter?: boolean;
        },
        {
          id: string;
          name: string;
          shirt: number;
          position: "GK" | "DEF" | "MID" | "FWD";
          starter?: boolean;
        },
        {
          id: string;
          name: string;
          shirt: number;
          position: "GK" | "DEF" | "MID" | "FWD";
          starter?: boolean;
        },
        {
          id: string;
          name: string;
          shirt: number;
          position: "GK" | "DEF" | "MID" | "FWD";
          starter?: boolean;
        },
        {
          id: string;
          name: string;
          shirt: number;
          position: "GK" | "DEF" | "MID" | "FWD";
          starter?: boolean;
        },
        {
          id: string;
          name: string;
          shirt: number;
          position: "GK" | "DEF" | "MID" | "FWD";
          starter?: boolean;
        }
      ]
    | [
        {
          id: string;
          name: string;
          shirt: number;
          position: "GK" | "DEF" | "MID" | "FWD";
          starter?: boolean;
        },
        {
          id: string;
          name: string;
          shirt: number;
          position: "GK" | "DEF" | "MID" | "FWD";
          starter?: boolean;
        },
        {
          id: string;
          name: string;
          shirt: number;
          position: "GK" | "DEF" | "MID" | "FWD";
          starter?: boolean;
        },
        {
          id: string;
          name: string;
          shirt: number;
          position: "GK" | "DEF" | "MID" | "FWD";
          starter?: boolean;
        },
        {
          id: string;
          name: string;
          shirt: number;
          position: "GK" | "DEF" | "MID" | "FWD";
          starter?: boolean;
        },
        {
          id: string;
          name: string;
          shirt: number;
          position: "GK" | "DEF" | "MID" | "FWD";
          starter?: boolean;
        },
        {
          id: string;
          name: string;
          shirt: number;
          position: "GK" | "DEF" | "MID" | "FWD";
          starter?: boolean;
        },
        {
          id: string;
          name: string;
          shirt: number;
          position: "GK" | "DEF" | "MID" | "FWD";
          starter?: boolean;
        },
        {
          id: string;
          name: string;
          shirt: number;
          position: "GK" | "DEF" | "MID" | "FWD";
          starter?: boolean;
        },
        {
          id: string;
          name: string;
          shirt: number;
          position: "GK" | "DEF" | "MID" | "FWD";
          starter?: boolean;
        },
        {
          id: string;
          name: string;
          shirt: number;
          position: "GK" | "DEF" | "MID" | "FWD";
          starter?: boolean;
        },
        {
          id: string;
          name: string;
          shirt: number;
          position: "GK" | "DEF" | "MID" | "FWD";
          starter?: boolean;
        },
        {
          id: string;
          name: string;
          shirt: number;
          position: "GK" | "DEF" | "MID" | "FWD";
          starter?: boolean;
        },
        {
          id: string;
          name: string;
          shirt: number;
          position: "GK" | "DEF" | "MID" | "FWD";
          starter?: boolean;
        },
        {
          id: string;
          name: string;
          shirt: number;
          position: "GK" | "DEF" | "MID" | "FWD";
          starter?: boolean;
        }
      ]
    | [
        {
          id: string;
          name: string;
          shirt: number;
          position: "GK" | "DEF" | "MID" | "FWD";
          starter?: boolean;
        },
        {
          id: string;
          name: string;
          shirt: number;
          position: "GK" | "DEF" | "MID" | "FWD";
          starter?: boolean;
        },
        {
          id: string;
          name: string;
          shirt: number;
          position: "GK" | "DEF" | "MID" | "FWD";
          starter?: boolean;
        },
        {
          id: string;
          name: string;
          shirt: number;
          position: "GK" | "DEF" | "MID" | "FWD";
          starter?: boolean;
        },
        {
          id: string;
          name: string;
          shirt: number;
          position: "GK" | "DEF" | "MID" | "FWD";
          starter?: boolean;
        },
        {
          id: string;
          name: string;
          shirt: number;
          position: "GK" | "DEF" | "MID" | "FWD";
          starter?: boolean;
        },
        {
          id: string;
          name: string;
          shirt: number;
          position: "GK" | "DEF" | "MID" | "FWD";
          starter?: boolean;
        },
        {
          id: string;
          name: string;
          shirt: number;
          position: "GK" | "DEF" | "MID" | "FWD";
          starter?: boolean;
        },
        {
          id: string;
          name: string;
          shirt: number;
          position: "GK" | "DEF" | "MID" | "FWD";
          starter?: boolean;
        },
        {
          id: string;
          name: string;
          shirt: number;
          position: "GK" | "DEF" | "MID" | "FWD";
          starter?: boolean;
        },
        {
          id: string;
          name: string;
          shirt: number;
          position: "GK" | "DEF" | "MID" | "FWD";
          starter?: boolean;
        },
        {
          id: string;
          name: string;
          shirt: number;
          position: "GK" | "DEF" | "MID" | "FWD";
          starter?: boolean;
        },
        {
          id: string;
          name: string;
          shirt: number;
          position: "GK" | "DEF" | "MID" | "FWD";
          starter?: boolean;
        },
        {
          id: string;
          name: string;
          shirt: number;
          position: "GK" | "DEF" | "MID" | "FWD";
          starter?: boolean;
        },
        {
          id: string;
          name: string;
          shirt: number;
          position: "GK" | "DEF" | "MID" | "FWD";
          starter?: boolean;
        },
        {
          id: string;
          name: string;
          shirt: number;
          position: "GK" | "DEF" | "MID" | "FWD";
          starter?: boolean;
        }
      ];
}
