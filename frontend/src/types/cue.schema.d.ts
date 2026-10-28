/* Generated from contracts/. Do not edit; run npm run gen:types. */

/**
 * A timed, machine-readable overlay instruction (rule 4.1 stage d). Screens show it at show_at in match time, keep it at least min_dwell_ms, and drop it at expires_at. The cue log is also the edit list for the recap videos.
 */
export type Cue = {
  cue_id: string;
  /**
   * Strictly increasing per session, across all screens.
   */
  seq: number;
  screen: string;
  moment_id?: string;
  fixture_id?: string;
  /**
   * Match time in milliseconds from kick-off. 90 minutes is 5400000; stoppage time runs past it.
   */
  show_at: number;
  /**
   * Real milliseconds. The plan uses 6000 on the group screen.
   */
  min_dwell_ms: number;
  /**
   * Match time in milliseconds from kick-off. 90 minutes is 5400000; stoppage time runs past it.
   */
  expires_at: number;
  /**
   * cue_id this one replaces, for a plain stat upgraded to an explanation.
   */
  replaces?: string;
  /**
   * stat: plain number card from code. explanation: a manager agent's text. host_reason: why the group screen shows this. table: league table. tag: player name tag on the pitch. summary: a manager's half-time or full-time summary, on their phone. recap_ready: link to the recap video. clash: on a close call, the two competing pitches on the group screen until verdict_at, then the verdict (#89). lost_screen: on a phone, the manager's moment lost the group screen to winner (#89).
   */
  kind: "stat" | "explanation" | "host_reason" | "table" | "tag" | "summary" | "recap_ready" | "clash" | "lost_screen";
  source: "code" | "scorer" | "host" | "manager" | "fallback";
  payload: {
    headline: string;
    body?: string;
    body_en?: string;
    /**
     * BCP 47 language tag: en or es (#16).
     */
    lang?: string;
    /**
     * Rule 4.1(e): analyst gets the numbers and the evidence; casual gets one plain sentence.
     */
    style?: "analyst" | "casual";
    /**
     * On a group explanation card: who wrote its body, the winner's pitch. manager (an agent) or code (#138). The cue's source says who decided the screen, not who wrote the line.
     */
    body_source?: "manager" | "code";
    manager?: string;
    player?: string;
    /**
     * Shown in the analyst evidence drawer.
     */
    evidence?: Evidence[];
    /**
     * @maxItems 5
     */
    table?:
      | []
      | [
          {
            manager: string;
            points: number;
            rank: number;
            delta?: number;
          }
        ]
      | [
          {
            manager: string;
            points: number;
            rank: number;
            delta?: number;
          },
          {
            manager: string;
            points: number;
            rank: number;
            delta?: number;
          }
        ]
      | [
          {
            manager: string;
            points: number;
            rank: number;
            delta?: number;
          },
          {
            manager: string;
            points: number;
            rank: number;
            delta?: number;
          },
          {
            manager: string;
            points: number;
            rank: number;
            delta?: number;
          }
        ]
      | [
          {
            manager: string;
            points: number;
            rank: number;
            delta?: number;
          },
          {
            manager: string;
            points: number;
            rank: number;
            delta?: number;
          },
          {
            manager: string;
            points: number;
            rank: number;
            delta?: number;
          },
          {
            manager: string;
            points: number;
            rank: number;
            delta?: number;
          }
        ]
      | [
          {
            manager: string;
            points: number;
            rank: number;
            delta?: number;
          },
          {
            manager: string;
            points: number;
            rank: number;
            delta?: number;
          },
          {
            manager: string;
            points: number;
            rank: number;
            delta?: number;
          },
          {
            manager: string;
            points: number;
            rank: number;
            delta?: number;
          },
          {
            manager: string;
            points: number;
            rank: number;
            delta?: number;
          }
        ];
    /**
     * League rulebook entries a summary relies on, retrieved from the Foundry IQ knowledge base (#18).
     *
     * @maxItems 5
     */
    rulebook?:
      | []
      | [
          {
            id: string;
            title: string;
          }
        ]
      | [
          {
            id: string;
            title: string;
          },
          {
            id: string;
            title: string;
          }
        ]
      | [
          {
            id: string;
            title: string;
          },
          {
            id: string;
            title: string;
          },
          {
            id: string;
            title: string;
          }
        ]
      | [
          {
            id: string;
            title: string;
          },
          {
            id: string;
            title: string;
          },
          {
            id: string;
            title: string;
          },
          {
            id: string;
            title: string;
          }
        ]
      | [
          {
            id: string;
            title: string;
          },
          {
            id: string;
            title: string;
          },
          {
            id: string;
            title: string;
          },
          {
            id: string;
            title: string;
          },
          {
            id: string;
            title: string;
          }
        ];
    recap_url?: string;
    /**
     * The two pitches the group screen shows on a close call before the verdict (#89). On a clash cue only.
     *
     * @minItems 2
     * @maxItems 2
     */
    contenders?: [
      {
        manager: string;
        pitch: string;
        /**
         * Who wrote the pitch: manager (the agent) or code (#138).
         */
        pitch_source?: "manager" | "code";
        angle?: string;
      },
      {
        manager: string;
        pitch: string;
        /**
         * Who wrote the pitch: manager (the agent) or code (#138).
         */
        pitch_source?: "manager" | "code";
        angle?: string;
      }
    ];
    /**
     * Match time in milliseconds from kick-off. 90 minutes is 5400000; stoppage time runs past it.
     */
    verdict_at?: number;
    /**
     * Whose moment the group screen showed: on a clash cue, the host's pick shown after verdict_at; on a lost_screen cue, the manager who took the screen (never the phone's own manager).
     */
    winner?: string;
  };
};

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
