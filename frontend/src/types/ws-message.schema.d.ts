/* Generated from contracts/. Do not edit; run npm run gen:types. */

/**
 * Envelope for every message from the app server to a screen over the WebSocket. One socket per screen; the server sends only cues for that screen, plus meta (a single match) or gameweek (#94), league, frames, events and decisions.
 */
export type WsMessage = {
  v: 1;
  type: "meta" | "gameweek" | "league" | "frame" | "event" | "decision" | "cue";
  data: {};
};
