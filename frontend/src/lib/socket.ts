// One screen's WebSocket, reconnecting with backoff. The server refuses an unknown session, screen or token
// before accepting the socket, so the browser sees a failed handshake (1006), not the server's 1008, and gives
// up only after six failed tries in a row (backoffMs: 0.5 + 1 + 2 + 4 + 5 = 12.5 s of waits).
import { parseMessage, type WsMessage } from "./messages";

export type Connection = "connecting" | "open" | "retrying" | "refused";

const NORMAL_CLOSURE = 1000;
const POLICY_VIOLATION = 1008;
const GIVE_UP_AFTER = 6; // six failed tries in a row, about 12.5 s of backoff between them

export function backoffMs(attempt: number): number {
  return Math.min(500 * 2 ** attempt, 5000);
}

/** Whether to stop retrying. The server closes with 1000 when it drops a finished session. It refuses an unknown
 *  session, screen or token with 1008 before accepting (app/main.py, stream), which a browser reports as a failed
 *  handshake, 1006, like a dropped network; so six failures in a row without ever opening count as a refusal.
 *  A 1008 is still final if one ever arrives on an open socket. */
export function gaveUp(code: number, failuresInARow: number): boolean {
  return code === NORMAL_CLOSURE || code === POLICY_VIOLATION || failuresInARow >= GIVE_UP_AFTER;
}

/** A screen's WebSocket address. cuesOnly: the page has the match from another screen, so this one needs only
 * its own cues, not every frame again (#49). */
export function screenUrl(
  loc: { protocol: string; host: string },
  session: string,
  screen: string,
  token: string | null,
  cuesOnly: boolean,
): string {
  const params = new URLSearchParams();
  if (token) params.set("token", token);
  if (cuesOnly) params.set("cues", "only");
  const encoded = params.toString().replace(/\+/g, "%20"); // not params.size: older Safari has no size
  const query = encoded ? `?${encoded}` : "";
  return `${loc.protocol === "https:" ? "wss" : "ws"}://${loc.host}/ws/${encodeURIComponent(session)}/${screen}${query}`;
}

export function openScreen(
  session: string,
  screen: string,
  token: string | null,
  cuesOnly: boolean,
  onMessage: (m: WsMessage) => void,
  onState: (c: Connection) => void,
): () => void {
  const url = screenUrl(location, session, screen, token, cuesOnly);
  let socket: WebSocket | null = null;
  let attempt = 0;
  let timer: number | undefined;
  let closed = false;

  const connect = () => {
    onState(attempt === 0 ? "connecting" : "retrying");
    socket = new WebSocket(url);
    socket.onopen = () => {
      attempt = 0;
      onState("open");
    };
    socket.onmessage = (e: MessageEvent<string>) => {
      const m = parseMessage(e.data);
      if (m) onMessage(m);
      else if (import.meta.env.DEV) console.warn("[watchparty] message outside the contract", e.data);
    };
    socket.onclose = (e) => {
      if (closed) return;
      if (gaveUp(e.code, attempt + 1)) return onState("refused");
      onState("retrying");
      timer = window.setTimeout(connect, backoffMs(attempt++));
    };
  };
  connect();
  return () => {
    closed = true;
    window.clearTimeout(timer);
    socket?.close();
  };
}
