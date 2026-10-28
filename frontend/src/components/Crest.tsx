// A club crest for the invented clubs: a shield with its three letters, white for the home side and outlined for
// the away side (#194: the match screens carry no club colours). No real badge.
export function Crest({ short, side, size = 40 }: { short: string; side: "home" | "away"; size?: number }) {
  return (
    <svg className={`crest ${side}`} width={size} height={size * 1.15} viewBox="0 0 40 46" aria-hidden="true">
      <path d="M20 2 L37 7.5 V22 C37 33.5 29.5 40.5 20 44 C10.5 40.5 3 33.5 3 22 V7.5 Z" strokeWidth="2.5" />
      <text x="20" y="27" textAnchor="middle" fontSize="11" fontWeight="800" letterSpacing="0.5">{short}</text>
    </svg>
  );
}
