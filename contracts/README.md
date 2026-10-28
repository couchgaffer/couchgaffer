# Contracts

**Status: draft.** Final once all three team members sign off on issue #4. Until then, change these files freely, but in the same commit update the examples and keep `validate.py` passing.

The message formats every part of the system builds against. JSON Schema, draft 2020-12. The backend generates pydantic models from these files and the front end generates TypeScript types; the schemas are the source of truth, not the generated code.

## Flow

```
simulator ──MatchEvent, MatchFrame──▶ points engine ──Stake──▶ manager agent
                                                                    │ Bid (after the number checker)
                                                                    ▼
screens ◀──WsMessage{meta or gameweek, league, frame, event, decision, cue}── scorer / host ──HostDecision
```

## Files

| Schema | Message | Producer → consumer |
|---|---|---|
| `common.schema.json` | Shared types: time, coordinates, IDs, language, style, evidence | All |
| `match-meta.schema.json` | MatchMeta: seed, speed, playback delay, invented clubs and players | Simulator → all |
| `gameweek.schema.json` | Gameweek: seed, speed, playback delay and the three fixtures' MatchMeta, sent in place of meta (#94) | Session → all |
| `league.schema.json` | League: five managers, squads, captain, style, language | Session setup → all |
| `match-event.schema.json` | MatchEvent: one synthetic event | Simulator → points engine, screens |
| `match-frame.schema.json` | MatchFrame: approximate positions for drawing | Simulator → screens |
| `stake.schema.json` | Stake: what a moment means for one manager, with evidence | Points engine → manager agent, scorer |
| `bid.schema.json` | Bid: the explanation and the pitch to the host | Manager agent → scorer, phone |
| `host-decision.schema.json` | HostDecision: what the group screen shows and who decided | Scorer or host → group screen, recap |
| `cue.schema.json` | Cue: timed overlay instruction | Workflow → screens; the cue log feeds the recap job |
| `ws-message.schema.json` | WsMessage: envelope for everything sent over the WebSocket | App server → screens |

`examples/` holds one valid message per schema; the file name starts with the schema name.

## Conventions

- **Time.** `t`, `show_at` and `expires_at` are match milliseconds from kick-off. `min_dwell_ms`, `latency_ms` and `playback_delay_ms` are real milliseconds. At 30x, one real second is 30 match seconds.
- **Coordinates.** Normalised 0 to 1. x runs from the home goal line to the away goal line, y from the left touchline, seen from the home end. The pitch is 105 m by 68 m.
- **IDs.** `p_home_9`, `mgr_2`, `m_07`, `ev_412_xg`, `c_31`, `t_hollinsford`. Patterns are in `common.schema.json`.
- **Gameweek (#94).** A gameweek session sends `gameweek` in place of `meta`, and sets `fixture_id` (`fx_1`, `fx_2`, `fx_3`; the pattern allows up to `fx_9`) on every event, frame, cue, stake, bid and host decision. Player ids there carry the club's short name in lower case (`p_hol_9`). A single match sends no `fixture_id` and keeps `p_home_9` and `p_away_9`.
- **Evidence rule.** Every number in text a viewer sees must match the `value` of an evidence item the message cites. The points engine is the only source of evidence. The number checker enforces this; the schema only requires that evidence is cited.
- **Style.** `analyst` gets the numbers and an evidence drawer; `casual` gets one plain sentence (rule 4.1(e)).
- **Language.** `lang` is a BCP 47 tag. Text in any language other than English also carries an English version (`explanation_en`, `body_en`) for judges and logs (rule 4.9).
- **Cue timing.** Screens run `playback_delay_ms` behind the simulator, buffer cues by `show_at`, keep each at least `min_dwell_ms`, and drop it at `expires_at`. A `stat` cue from code comes first; an `explanation` cue with `replaces` upgrades it.

## Rules these formats keep

- All clubs, players and managers are invented (rules 4.4, 4.7). Check new names before adding them.
- The scoring is our own: impact points (IP) and tiers such as the Wall tier. No other fantasy game's rule names.
- Free to play: no stakes, prizes, wagering or money anywhere in the league (rule 4.4, content).

## Validate

From the repo root:

```
uv run --no-project --with jsonschema python contracts/validate.py
```

It checks every schema, every example, and that a set of deliberately broken messages are rejected.
