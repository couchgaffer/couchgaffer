// The delayed playback clock. Screens run playback_delay_ms behind the stream, so a moment's stat card,
// explanations and group decision have arrived before the moment is shown.

const STALL_MS = 1000; // if the stream goes quiet, stop extrapolating after this long

export class PlaybackClock {
  private lastT = 0;
  private receivedAt = 0;
  private started = false;
  private ended = false;
  private shown = 0;

  constructor(
    private readonly speed: number,
    private readonly delayMs: number,
  ) {}

  /** A message stamped with match time t arrived at real time now. */
  observe(t: number, now: number): void {
    if (!this.started || t >= this.lastT) {
      this.lastT = t;
      this.receivedAt = now;
      this.started = true;
    }
  }

  /** The stream is over: play out the buffered end instead of waiting for more. */
  end(): void {
    this.ended = true;
  }

  /** Match time to show at real time now: never ahead of what has arrived, never backwards. */
  at(now: number): number {
    if (!this.started) return 0;
    const ahead = Math.min(now - this.receivedAt, this.ended ? Infinity : STALL_MS);
    const target = this.lastT + ahead * this.speed - this.delayMs * this.speed;
    this.shown = Math.max(this.shown, Math.min(Math.max(target, 0), this.lastT));
    return this.shown;
  }
}
