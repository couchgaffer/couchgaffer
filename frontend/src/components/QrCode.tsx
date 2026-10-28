// A QR code as one SVG path: crisp at any size, no canvas and no injected HTML.
import { qrMatrix } from "../lib/qr";

export function QrCode({ text, label }: { text: string; label: string }) {
  const matrix = qrMatrix(text);
  const path = matrix
    .flatMap((row, y) => row.map((dark, x) => (dark ? `M${x} ${y}h1v1h-1z` : "")))
    .join("");
  return (
    <svg className="qr" viewBox={`0 0 ${matrix.length} ${matrix.length}`} role="img" aria-label={label}
         shapeRendering="crispEdges">
      <rect width={matrix.length} height={matrix.length} fill="#fff" />
      <path d={path} fill="#000" />
    </svg>
  );
}
