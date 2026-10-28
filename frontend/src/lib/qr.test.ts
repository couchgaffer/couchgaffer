import jsQR from "jsqr";
import { describe, expect, it } from "vitest";

import { phoneUrl, qrMatrix } from "./qr";

/** Draws the matrix as black and white pixels, the way a phone's camera would see the screen. */
function pixels(matrix: boolean[][], scale = 4): { data: Uint8ClampedArray; size: number } {
  const size = matrix.length * scale;
  const data = new Uint8ClampedArray(size * size * 4);
  for (let y = 0; y < size; y++) {
    for (let x = 0; x < size; x++) {
      const v = matrix[Math.floor(y / scale)]?.[Math.floor(x / scale)] ? 0 : 255;
      data.set([v, v, v, 255], (y * size + x) * 4);
    }
  }
  return { data, size };
}

describe("the QR code to the phone view", () => {
  const url = phoneUrl("https://ca-watchparty.example.azurecontainerapps.io", "Ab3_dEfGhIjKlMnOp", "mgr_3",
                       "T0k-en_Abc123XyZ"); // prettier-ignore

  it("links the manager's own phone view, with the token in the fragment", () => {
    expect(url).toBe(
      "https://ca-watchparty.example.azurecontainerapps.io/phone/Ab3_dEfGhIjKlMnOp/mgr_3#token=T0k-en_Abc123XyZ&manager=mgr_3",
    );
  });

  it("scans back to exactly that link", () => {
    const { data, size } = pixels(qrMatrix(url));
    expect(jsQR(data, size, size)?.data).toBe(url);
  });

  it("keeps the quiet zone a camera needs", () => {
    const matrix = qrMatrix(url);
    const edge = [...(matrix[0] ?? []), ...matrix.map((row) => row[0])];
    expect(edge.every((dark) => !dark)).toBe(true);
  });
});
