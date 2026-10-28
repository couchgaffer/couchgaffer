# 0002: Who gets the group screen

- **Status:** decided, 23 Oct 2026 (#11)
- **Decision:** code scores every bid and settles clear winners. The host agent decides only close calls. It picks from facts that code has already checked, and its reason may quote only the chosen manager's own numbers.

## Context

- One shared screen and up to five managers who want it. Fairness is part of the product: nobody should be left off all match.
- The host's reason is shown to the whole room, so a false claim does more harm than no claim (rule 4.3: claim only what ships).
- Budget: 2.5 s for the host call (0001-models.md). On a miss, the scorer's pick goes out (`source: "fallback"`).

## Scoring (`watchparty/agents/scorer.py`)

```
stake    = |delta| / largest |delta| among the bids * (0.75 + 0.25 * urgency)
fairness = 1 + 0.3 * overruled - 0.5 * max(0, airtime_share - 0.2)
score    = stake * fairness
```

- **Points decide; urgency only nudges.** The urgency is the manager agent's own estimate, so it is worth at most a quarter. A bid cannot talk its way past a bigger moment.
- **`overruled` counts close calls lost since the manager was last on screen.** It resets when their turn comes, so a long-forgotten loss does not keep compounding.
- **A clear winner leads on both points and score by more than 10%.** Anything else goes to the host: a near tie, or fairness reversing the order the points give. Judging the lead on points, not on urgency-weighted stake, means a tie on points always reaches the host, whatever the agents say about their own urgency (a real-model run showed urgency alone turning ties into "clear" wins).
- **A plain-stat fallback bid carries a neutral urgency of 0.5,** so a manager whose model call failed neither wins nor loses the screen because of it.

## The host agent (`watchparty/agents/host.py`)

- **Input:** each candidate in the scorer's order, with its pitch, impact points, airtime share, close calls lost and **`true_facts`**. The facts are computed by code and hold against every other candidate:
  - `bigger_swing`
  - `least_airtime`
  - `most_close_calls_lost`
  - `scorer_pick`
- **Output:** `chosen_manager`, `because` (one of that candidate's true facts) and a reason of up to 140 characters.
- **Code checks:**
  - `because` is true for the pick
  - every number in the reason is one of the chosen manager's own
  - names come only from this match
  - no betting or money language
  - no gendered pronouns for managers

  If a check fails, the host gets one retry with the problems listed; if it fails again, the fallback goes out.

### Why the facts are given, not left to the model

Each version was tested with the real model, three demo matches each, run from a laptop in India against Sweden Central (`tools/eval_session.py`):

| Host input | What went wrong | Host decisions | Fallbacks |
|---|---|---|---|
| Ledger numbers only; any candidate's number allowed | False claims passed the number check: "Arjun has the **biggest swing** at 3 IP" (Sam had 4); "lost **2** close calls" (it was 1, and the 2 belonged to another candidate) | 11 of 12 | 1 (a cold-start timeout) |
| Model must state a true `because`; own numbers only | Nothing false reached the screen, but the model kept getting the comparisons wrong | 7 of 12 | 5 |
| Code gives each candidate's `true_facts` | Nothing false reached the screen; 2 retries, slowest call 2.0 s | 11 of 11 | 0 |
| The same, final prompt ("never mention the scorer") | Nothing false reached the screen; 1 retry; one call missed the 2.5 s deadline from the laptop | 8 of 9 | 1 |

So the model does what it is good at: the judgement (is a bigger swing worth more than a fairer turn?) and the wording. Code does the comparisons, which it gets right every time. In one run the host overruled the scorer, which had a fairer turn for You narrowly ahead, and gave the screen to Sam for the bigger swing.

## The demo league

With the first league, only three of the demo's 14 moments had more than one bidder, and all three had clear winners, so the host was never called. In a real mini-league popular players are owned by several managers. Two ownerships were added (`league_v1.json`):
- Arjun also owns the home keeper Tom Ashcombe.
- Sam also owns Kwame Adomako.

The demo now has seven contested moments. With the scorer's points rule, the host is called on 4 of the 14 in 195 of 200 runs with random urgencies (3 in the other 5). Every manager gets the screen at least once.

The judge can play any of the five managers. Whichever one they pick is shown as "You" for that session, and the agents address that name as the viewer (`backend/watchparty/app/session.py`).

## Revisit if

- In-region latency puts the host p95 near 2.5 s, or real-model runs show more than one fallback per match.
