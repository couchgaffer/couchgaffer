# Synthetic match dataset

Every match in this project is synthetic (rule 4.7). The clubs, players and events are invented by a seeded simulator (`backend/watchparty/sim/`). There is no real match data, footage or likeness, and no real club, player or manager. The two clubs are English-style but invented. On 23 Oct 2026 every name was checked against the 2026-27 Premier League: no club, town, stadium or nickname word in common, and no full name or surname shared with any player or head coach. Every name was also web-searched for exact matches with any real professional.

## Generate a match

From `backend/`:

```
uv run python -m watchparty.sim --seed 267 --out match.jsonl
```

- The same seed always gives a byte-identical file. A different seed gives a different match.
- Seed 267 is the demo match: 2-1, 14 key moments, four keeper save runs, a red card, and a late equaliser before a stoppage-time winner.
- One match is about 3,400 lines (about 2 MB) and takes under a second to generate.

## Format

JSON Lines: one `WsMessage` per line (`contracts/schemas/ws-message.schema.json`), the same messages the app streams to the screens.

| Line | `type` | `data` schema | What it holds |
|---|---|---|---|
| First | `meta` | `match-meta.schema.json` | Seed, speed, playback delay, both invented clubs with their players |
| Then, in match-time order | `event` | `match-event.schema.json` | About 1,500 events: passes (distance, difficulty, progressive, ball speed), carries (sprint speed), tackles, interceptions, pressure, shots (xG, shot speed, body part, outcome), fouls, cards, corners, free kicks, kick-offs, half time, full time |
|  | `frame` | `match-frame.schema.json` | About 1,900 frames, one every 3 match seconds: ball position, possession, every player's approximate position |

Times are match milliseconds from kick-off. Coordinates are normalised: x runs from the home goal line (0) to the away goal line (1), y from the left touchline. Full conventions: `contracts/README.md`.

## Key moments

Each match has 12 to 15 key moments, at least 5 match minutes apart (10 seconds at 30x). They are scripted scenarios with seeded variation:

`build_up`, `counterattack`, `high_press`, `set_piece`, `low_block`, `keeper_save_run`, `late_equaliser`.

Every event in a moment carries `moment_id` and `scenario`. The last one carries `climax: true`, and the points engine scores the moment there. Play between moments is `filler`: passes, carries, turnovers, the odd shot and foul.

## How realistic it is

Checked in `backend/tests/test_sim.py` over 50 seeds:

| Statistic | Averaged over 50 seeds | Real top flight, roughly |
|---|---|---|
| Shots per match | 22 to 28 | about 25 |
| Total xG | 2.2 to 3.2 | 2.5 to 3 |
| Goals | 2.0 to 3.2 | about 2.7 |
| Pass completion | 78% to 86% | about 82% |
| Home possession | 45% to 55% | about 50% |

Single matches vary, as real ones do, within sanity bounds: 12 to 40 shots, 0 to 7 goals.

The models are deliberately simple:
- xG is logistic in shot distance and the angle the goal mouth presents.
- Pass success falls with difficulty, and difficulty comes from distance, pressure and depth.

The simulation is event-level with approximate positions, not player physics.
