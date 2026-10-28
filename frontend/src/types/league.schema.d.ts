/* Generated from contracts/. Do not edit; run npm run gen:types. */

/**
 * The mini-league for one session: five managers, one played by the viewer and four bots. Free to play; no stakes, prizes or wagering.
 */
export interface League {
  league_id: string;
  name: string;
  scoring_rules_version: string;
  /**
   * @minItems 5
   * @maxItems 5
   */
  managers: [
    {
      id: string;
      display_name: string;
      is_bot: boolean;
      /**
       * Rule 4.1(e): analyst gets the numbers and the evidence; casual gets one plain sentence.
       */
      style: "analyst" | "casual";
      /**
       * BCP 47 language tag: en or es (#16).
       */
      lang: string;
      favourite_club?: string;
      /**
       * Players this manager owns in this match. The captain must be one of them.
       *
       * @minItems 4
       * @maxItems 6
       */
      squad:
        | [string, string, string, string]
        | [string, string, string, string, string]
        | [string, string, string, string, string, string];
      captain: string;
      /**
       * Impact points before kick-off: the league standing.
       */
      points: number;
    },
    {
      id: string;
      display_name: string;
      is_bot: boolean;
      /**
       * Rule 4.1(e): analyst gets the numbers and the evidence; casual gets one plain sentence.
       */
      style: "analyst" | "casual";
      /**
       * BCP 47 language tag: en or es (#16).
       */
      lang: string;
      favourite_club?: string;
      /**
       * Players this manager owns in this match. The captain must be one of them.
       *
       * @minItems 4
       * @maxItems 6
       */
      squad:
        | [string, string, string, string]
        | [string, string, string, string, string]
        | [string, string, string, string, string, string];
      captain: string;
      /**
       * Impact points before kick-off: the league standing.
       */
      points: number;
    },
    {
      id: string;
      display_name: string;
      is_bot: boolean;
      /**
       * Rule 4.1(e): analyst gets the numbers and the evidence; casual gets one plain sentence.
       */
      style: "analyst" | "casual";
      /**
       * BCP 47 language tag: en or es (#16).
       */
      lang: string;
      favourite_club?: string;
      /**
       * Players this manager owns in this match. The captain must be one of them.
       *
       * @minItems 4
       * @maxItems 6
       */
      squad:
        | [string, string, string, string]
        | [string, string, string, string, string]
        | [string, string, string, string, string, string];
      captain: string;
      /**
       * Impact points before kick-off: the league standing.
       */
      points: number;
    },
    {
      id: string;
      display_name: string;
      is_bot: boolean;
      /**
       * Rule 4.1(e): analyst gets the numbers and the evidence; casual gets one plain sentence.
       */
      style: "analyst" | "casual";
      /**
       * BCP 47 language tag: en or es (#16).
       */
      lang: string;
      favourite_club?: string;
      /**
       * Players this manager owns in this match. The captain must be one of them.
       *
       * @minItems 4
       * @maxItems 6
       */
      squad:
        | [string, string, string, string]
        | [string, string, string, string, string]
        | [string, string, string, string, string, string];
      captain: string;
      /**
       * Impact points before kick-off: the league standing.
       */
      points: number;
    },
    {
      id: string;
      display_name: string;
      is_bot: boolean;
      /**
       * Rule 4.1(e): analyst gets the numbers and the evidence; casual gets one plain sentence.
       */
      style: "analyst" | "casual";
      /**
       * BCP 47 language tag: en or es (#16).
       */
      lang: string;
      favourite_club?: string;
      /**
       * Players this manager owns in this match. The captain must be one of them.
       *
       * @minItems 4
       * @maxItems 6
       */
      squad:
        | [string, string, string, string]
        | [string, string, string, string, string]
        | [string, string, string, string, string, string];
      captain: string;
      /**
       * Impact points before kick-off: the league standing.
       */
      points: number;
    }
  ];
}
