// The QR code to a judge's phone view (#17), drawn in the browser: nothing leaves the page to make it.
import { encode } from "uqr";

import { fragment } from "./api";

/** Dark modules, with the four-module quiet zone the QR standard asks for. Medium error correction copes
 *  with glare on a TV or laptop screen. */
export function qrMatrix(text: string): boolean[][] {
  return encode(text, { ecc: "M", border: 4 }).data;
}

export function phoneUrl(origin: string, session: string, manager: string, token: string): string {
  return `${origin}/phone/${encodeURIComponent(session)}/${manager}${fragment(token, manager)}`;
}
