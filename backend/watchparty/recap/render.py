"""Draws a recap and encodes it: Pillow frames piped into ffmpeg, H.264, vertical 1080 x 1920, about 25 s.

The pitch looks like the web one (#13): invented clubs' colours, shirt numbers, name tags. No music, no
real badges or marks (rule 4.4). Static layers are drawn once per card; each frame only moves the dots.
"""

import bisect
import os
import subprocess
from functools import cache
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont

from watchparty.agents.base import written
from watchparty.recap.bundle import VERSION, covers
from watchparty.recap.plan import Clip, Recap

W, H, FPS = 1080, 1920, 25
TITLE_S, END_S, MIN_S = 2.5, 4.0, 20.0
CLIP_S = 4.5  # video seconds per moment: its 13.5 s of match time at 3x; longer when there are few moments
PITCH_X, PITCH_Y, SCALE = 40, 430, 1000 / 105  # pixels per metre
BG, PANEL, TEXT, MUTED, ACCENT = (15, 20, 25), (26, 34, 43), (238, 242, 246), (169, 182, 195), (92, 200, 255)
AI, GRASS, LINE = (201, 167, 255), (29, 94, 58), (190, 215, 200)
FONTS = ["/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", "/Library/Fonts/Arial Unicode.ttf"]
BOLD = ["/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", "/Library/Fonts/Arial Bold.ttf"]

# Every label the video draws, in the manager's language (#71). Places as summary.py writes them, "2.º" agreeing
# with "puesto", never with the manager (#16). Captions and the end card are already the manager's own phone text;
# the grey headline on a moment card is the group screen's stat card, shared by all five managers, so it stays in
# that screen's language.
WORDS = {
    "en": {"your_night": "Your night", "night": "{name}'s night", "moment": "Moment {n} of {total} · {minute}'",
           "final": "Final: {place} · {points} IP", "title": "Your recap", "one": "1 moment that mattered to you.",
           "many": "{n} moments that mattered to you.", "full_time": "Full time",
           "disclaimer": "Invented clubs, players and match. Free to play."},
    "es": {"your_night": "Tu noche", "night": "La noche de {name}", "moment": "Momento {n} de {total} · {minute}'",
           "final": "Final: {place} puesto · {points} IP", "title": "Tu resumen",
           "one": "1 momento que te importó.", "many": "{n} momentos que te importaron.",
           "full_time": "Final del partido", "disclaimer": "Clubes, jugadores y partido inventados. Juego gratuito."},
}  # fmt: skip


@cache
def font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for path in [os.environ.get("RECAP_FONT_BOLD" if bold else "RECAP_FONT", ""), *(BOLD if bold else FONTS)]:
        if path and Path(path).exists():
            return ImageFont.truetype(path, size)
    return ImageFont.load_default(size)


def _rgb(hex_colour: str) -> tuple[int, int, int]:
    h = hex_colour.lstrip("#")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def _wrap(draw: ImageDraw.ImageDraw, text: str, size: int, width: int, bold: bool = False) -> list[str]:
    """Greedy wrap by measured width, so any script wraps sensibly."""
    lines: list[str] = []
    for paragraph in text.split("\n"):
        line = ""
        for word in paragraph.split():
            trial = f"{line} {word}".strip()
            if draw.textlength(trial, font=font(size, bold)) <= width:
                line = trial
                continue
            if line:
                lines.append(line)
            line = word
            while len(line) > 1 and draw.textlength(line, font=font(size, bold)) > width:  # wider than the box
                cut = _fitting(draw, line, size, width, bold)
                lines.append(line[:cut])
                line = line[cut:]
        lines.append(line)
    return lines


def _fitting(draw: ImageDraw.ImageDraw, word: str, size: int, width: int, bold: bool) -> int:
    """How many of the word's first characters fit the width; at least one, so a break always moves on."""
    widths = [draw.textlength(word[:n], font=font(size, bold)) for n in range(1, len(word) + 1)]  # rising
    return max(bisect.bisect_right(widths, width), 1)


def _lines(draw: ImageDraw.ImageDraw, text: str, size: int, width: int, bold: bool, max_lines: int) -> list[str]:
    """The wrapped lines, at most `max_lines`; a cut last line ends in "…" and still fits the width (#71)."""
    lines = _wrap(draw, text, size, width, bold)
    if len(lines) <= max_lines:
        return lines
    last = lines[max_lines - 1]
    while last and draw.textlength(f"{last}…", font=font(size, bold)) > width:
        last = last.rsplit(" ", 1)[0] if " " in last else last[:-1]
    return [*lines[: max_lines - 1], f"{last}…"]


def _text(draw: ImageDraw.ImageDraw, xy: tuple[int, int], text: str, size: int, fill: tuple[int, int, int],
          width: int = W - 120, bold: bool = False, spacing: float = 1.3, max_lines: int = 8) -> int:  # fmt: skip
    """Draws wrapped text; returns the y below it."""
    x, y = xy
    for line in _lines(draw, text, size, width, bold, max_lines):
        draw.text((x, y), line, font=font(size, bold), fill=fill)
        y += int(size * spacing)
    return y


def _pitch() -> Image.Image:
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)
    x0, y0 = PITCH_X, PITCH_Y

    def box(x: float, y: float, w: float, h: float) -> None:
        d.rectangle([x0 + x * SCALE, y0 + y * SCALE, x0 + (x + w) * SCALE, y0 + (y + h) * SCALE], outline=LINE, width=3)

    d.rectangle([x0 - 20, y0 - 20, x0 + 105 * SCALE + 20, y0 + 68 * SCALE + 20], fill=GRASS)
    box(0, 0, 105, 68)
    d.line([x0 + 52.5 * SCALE, y0, x0 + 52.5 * SCALE, y0 + 68 * SCALE], fill=LINE, width=3)
    r = 9.15 * SCALE
    d.ellipse([x0 + 52.5 * SCALE - r, y0 + 34 * SCALE - r, x0 + 52.5 * SCALE + r, y0 + 34 * SCALE + r], outline=LINE,
              width=3)  # fmt: skip
    for x in (0.0, 105 - 16.5):
        box(x, 34 - 20.16, 16.5, 40.32)
    for x in (0.0, 105 - 5.5):
        box(x, 34 - 9.16, 5.5, 18.32)
    return img


class Positions:
    """Player and ball positions at any match time, interpolated between the 3-second frames."""

    def __init__(self, frames: list[dict[str, Any]]) -> None:
        self.frames = frames
        self.times = [f["t"] for f in frames]

    def at(self, t: float) -> tuple[tuple[float, float], dict[str, tuple[float, float]]]:
        i = bisect.bisect_right(self.times, t)
        a, b = self.frames[max(i - 1, 0)], self.frames[min(i, len(self.frames) - 1)]
        f = 0.0 if b["t"] == a["t"] else min(max((t - a["t"]) / (b["t"] - a["t"]), 0.0), 1.0)
        nxt = {p["id"]: p for p in b["players"]}

        def lerp(p: dict[str, Any], q: dict[str, Any]) -> tuple[float, float]:
            return p["x"] + (q["x"] - p["x"]) * f, p["y"] + (q["y"] - p["y"]) * f

        return lerp(a["ball"], b["ball"]), {p["id"]: lerp(p, nxt.get(p["id"], p)) for p in a["players"]}


def _to_px(x: float, y: float) -> tuple[float, float]:
    return PITCH_X + x * 105 * SCALE, PITCH_Y + y * 68 * SCALE


def _header(d: ImageDraw.ImageDraw, recap: Recap, line: str) -> None:
    d.text((60, 70), recap.league_name.upper(), font=font(30, True), fill=ACCENT)
    words = WORDS[recap.lang]
    night = words["your_night"] if recap.name == "You" else words["night"].format(name=recap.name)
    d.text((60, 115), night, font=font(64, True), fill=TEXT)
    d.text((60, 205), line, font=font(34), fill=MUTED)


def _ai_mark(d: ImageDraw.ImageDraw, xy: tuple[int, int]) -> None:
    x, y = xy
    d.rounded_rectangle([x, y, x + 64, y + 40], radius=8, fill=AI)
    d.text((x + 13, y + 4), "AI", font=font(28, True), fill=(31, 16, 51))


class Fixture:
    """What a clip draws from one fixture: its positions, each player's colour, shirt and name, and its score."""

    def __init__(self, meta: dict[str, Any], frames: list[dict[str, Any]], goals: list[dict[str, Any]]) -> None:
        sides = ("home", "away")
        self.meta, self.goals = meta, goals
        self.colour = {p["id"]: _rgb(meta[s]["colour"]) for s in sides for p in meta[s]["players"]}
        self.shirt = {p["id"]: str(p["shirt"]) for s in sides for p in meta[s]["players"]}
        self.names = {p["id"]: p["name"] for s in sides for p in meta[s]["players"]}
        self.positions = Positions(frames)

    def score(self, t: float) -> tuple[int, int]:
        """Goals up to and including match time t, home then away."""
        sides = [g["team"] for g in self.goals if g["t"] <= t]
        return sides.count("home"), sides.count("away")


def fixtures(bundle: dict[str, Any]) -> dict[str | None, Fixture]:
    """Each clip's fixture by its fixture_id, laid out as the bundle's version says. A single-match bundle (v1) is
    one fixture under None, with no score line, so it draws exactly as before."""
    if bundle["v"] == VERSION:
        return {None: Fixture(bundle["meta"], bundle["frames"], [])}
    return {f["fixture_id"]: Fixture(f["meta"], f["frames"], f["goals"]) for f in bundle["fixtures"]}


def _fixture_of(bundle: dict[str, Any], by_id: dict[str | None, Fixture], clip: Clip) -> Fixture:
    """The clip's own fixture, never another's players; in a gameweek, only one whose frames span the whole clip."""
    fixture = by_id.get(clip.fixture_id)
    where = f"bundle {bundle.get('session_id', '?')}, moment {clip.moment_id}"
    if fixture is None:
        raise ValueError(f"{where}: its fixture {clip.fixture_id!r} is not in the bundle")
    if clip.fixture_id is not None and not covers(fixture.positions.frames, clip.start_t, clip.end_t):
        raise ValueError(f"{where}: fixture {clip.fixture_id} has no frames from {clip.start_t} to {clip.end_t} ms")
    return fixture


def _scoreline(d: ImageDraw.ImageDraw, fixture: Fixture, t: float) -> None:
    """The clip's fixture under the header: each club's colour, name and score. Full names at the largest size that
    fits, down to 30 px (DejaVu, the job's font, needs it for the longest pairing), else short names."""
    home, away = fixture.meta["home"], fixture.meta["away"]
    h, a = fixture.score(t)
    chip, gap = 30, 16
    size, text = 40, ""
    for size, key in ((40, "name"), (34, "name"), (30, "name"), (40, "short_name")):
        text = f"{home[key]}  {h} - {a}  {away[key]}"
        if d.textlength(text, font=font(size, True)) + 2 * (chip + gap) <= W - 120:
            break
    y, top = 290, 290 + (size - chip) // 2 + 2  # each chip level with the text's middle
    d.rounded_rectangle([60, top, 60 + chip, top + chip], radius=6, fill=_rgb(home["colour"]), outline=TEXT)
    d.text((60 + chip + gap, y), text, font=font(size, True), fill=TEXT)
    right = 60 + chip + gap + d.textlength(text, font=font(size, True)) + gap
    d.rounded_rectangle([right, top, right + chip, top + chip], radius=6, fill=_rgb(away["colour"]), outline=TEXT)


def _clip_base(pitch: Image.Image, recap: Recap, clip: Clip, n: int) -> Image.Image:
    img = pitch.copy()
    d = ImageDraw.Draw(img)
    _header(d, recap, WORDS[recap.lang]["moment"].format(n=n, total=len(recap.clips), minute=clip.minute))
    top = PITCH_Y + int(68 * SCALE) + 50
    d.rounded_rectangle([40, top, W - 40, H - 60], radius=24, fill=PANEL)
    y = _text(d, (80, top + 36), clip.headline, 40, MUTED, width=W - 160, max_lines=2)
    d.text((80, y + 10), clip.points, font=font(72, True), fill=ACCENT)
    if clip.by_ai:
        _ai_mark(d, (W - 160, y + 26))
    _text(d, (80, y + 120), clip.caption, 46, TEXT, width=W - 160, max_lines=8)
    return img


def _card(recap: Recap, title: str, body: str, by_ai: bool) -> Image.Image:
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)
    words = WORDS[recap.lang]
    place, points = _ordinal(recap.rank, recap.lang), written(recap.points, recap.lang)  # 283,5 in Spanish
    _header(d, recap, words["final"].format(place=place, points=points))
    d.text((80, 620), title, font=font(96, True), fill=TEXT)
    if by_ai:
        _ai_mark(d, (80, 760))
    _text(d, (80, 840), body, 52, TEXT, width=W - 160, max_lines=12)
    d.text((80, H - 140), words["disclaimer"], font=font(30), fill=MUTED)
    return img


def title_card(recap: Recap) -> Image.Image:
    words, moments = WORDS[recap.lang], len(recap.clips)
    return _card(recap, words["title"], words["one"] if moments == 1 else words["many"].format(n=moments), False)


def end_card(recap: Recap) -> Image.Image:
    return _card(recap, WORDS[recap.lang]["full_time"], recap.end_card, recap.end_by_ai)


def _ordinal(n: int, lang: str) -> str:
    if lang == "es":
        return f"{n}.º"
    return f"{n}{ {1: 'st', 2: 'nd', 3: 'rd'}.get(n, 'th') }"


def frames(bundle: dict[str, Any], recap: Recap) -> Any:
    """Yields every video frame as raw RGB bytes. Each clip is drawn from its own fixture (#94)."""
    by_id = fixtures(bundle)
    moments = len(recap.clips)
    yield from [title_card(recap).tobytes()] * int(TITLE_S * FPS)
    pitch = _pitch()
    clip_s = max(CLIP_S, (MIN_S - TITLE_S - END_S) / moments) if moments else 0.0
    for n, clip in enumerate(recap.clips, start=1):
        fixture = _fixture_of(bundle, by_id, clip)
        colour, shirt, names = fixture.colour, fixture.shirt, fixture.names
        base = _clip_base(pitch, recap, clip, n)
        steps = int(clip_s * FPS)
        for i in range(steps):
            t = clip.start_t + i * (clip.end_t - clip.start_t) / steps
            img = base.copy()
            d = ImageDraw.Draw(img)
            if clip.fixture_id is not None:  # a gameweek clip says which match it is, and the score at this frame
                _scoreline(d, fixture, t)
            ball, players = fixture.positions.at(t)
            for pid, (x, y) in players.items():
                px, py = _to_px(x, y)
                d.ellipse([px - 17, py - 17, px + 17, py + 17], fill=colour.get(pid, TEXT), outline=TEXT, width=3)
                d.text((px, py), shirt.get(pid, ""), font=font(18, True), fill=TEXT, anchor="mm")
            bx, by = _to_px(*ball)
            d.ellipse([bx - 9, by - 9, bx + 9, by + 9], fill=TEXT, outline=(17, 17, 17), width=2)
            for pid in clip.players:
                if pid in players:
                    px, py = _to_px(*players[pid])
                    label = names[pid]
                    w = d.textlength(label, font=font(28, True)) + 28
                    left = min(max(px - w / 2, PITCH_X), W - PITCH_X - w)
                    box = [left, py - 74, left + w, py - 30]
                    d.rounded_rectangle(box, radius=10, fill=(10, 14, 18), outline=ACCENT)
                    d.text((left + 14, py - 70), label, font=font(28, True), fill=TEXT)
            yield img.tobytes()
    yield from [end_card(recap).tobytes()] * int((END_S if moments else MIN_S - TITLE_S) * FPS)


def render(bundle: dict[str, Any], recap: Recap, out: Path) -> None:
    """Encodes the recap to an MP4 that plays on phones and browsers (H.264, yuv420p, faststart)."""
    command = ["ffmpeg", "-loglevel", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}",
               "-r", str(FPS), "-i", "-", "-c:v", "libx264", "-preset", "veryfast", "-crf", "24",
               "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(out)]  # fmt: skip
    with subprocess.Popen(command, stdin=subprocess.PIPE) as ffmpeg:  # noqa: S603 - fixed arguments, no shell
        if ffmpeg.stdin is None:
            raise RuntimeError("ffmpeg has no stdin")
        for frame in frames(bundle, recap):
            ffmpeg.stdin.write(frame)
        ffmpeg.stdin.close()
        if ffmpeg.wait() != 0:
            raise RuntimeError(f"ffmpeg failed with {ffmpeg.returncode}")
