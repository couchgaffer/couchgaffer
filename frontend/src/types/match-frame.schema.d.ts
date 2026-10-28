/* Generated from contracts/. Do not edit; run npm run gen:types. */

/**
 * Approximate positions for drawing the pitch, about 10 per real second. The screens interpolate between frames.
 */
export interface MatchFrame {
  /**
   * Match time in milliseconds from kick-off. 90 minutes is 5400000; stoppage time runs past it.
   */
  t: number;
  fixture_id?: string;
  possession: "home" | "away";
  ball: {
    /**
     * Pitch coordinate, normalised. x runs from the home goal line (0) to the away goal line (1); y runs from the left touchline (0) to the right (1), seen from the home end. The pitch is 105 m by 68 m.
     */
    x: number;
    /**
     * Pitch coordinate, normalised. x runs from the home goal line (0) to the away goal line (1); y runs from the left touchline (0) to the right (1), seen from the home end. The pitch is 105 m by 68 m.
     */
    y: number;
    z_m?: number;
  };
  /**
   * @maxItems 22
   */
  players: {
    id: string;
    /**
     * Pitch coordinate, normalised. x runs from the home goal line (0) to the away goal line (1); y runs from the left touchline (0) to the right (1), seen from the home end. The pitch is 105 m by 68 m.
     */
    x: number;
    /**
     * Pitch coordinate, normalised. x runs from the home goal line (0) to the away goal line (1); y runs from the left touchline (0) to the right (1), seen from the home end. The pitch is 105 m by 68 m.
     */
    y: number;
  }[];
}
