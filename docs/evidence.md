# Evidence: our agents against one agent and a code rule

Run on 27 Oct 2026, 04:51 UTC, with `FOUNDRY_ENDPOINT=<your Foundry endpoint> uv run python -m tools.evidence --models 20` from `backend/` ([`tools/evidence.py`](../backend/tools/evidence.py)). Rerun it to check the arbitration and checker numbers; the model numbers vary from run to run.

## What changed since the 26 Oct run

- Explanations owed: 44 to each method, not 55. A friend whose moment is worth 0 IP is no longer owed one by either method (#88): our agent may decline that moment, and a decline goes to no phone.
- The one agent is now required and checked only on the owed friends (#135). Before, one bad line for a friend at 0 IP failed its whole answer. Its prompt now says a line for a friend at 0 IP is optional, and it may pick such a friend only with a line that passes the checks, as ours can show one only on a bid that passed them.
- Our agents changed since that run: each friend's agent argues from its own facts or declines (#88), the group screen shows a pitch that a room check reads on every bid (#138), and moments are decided side by side with a turn in arrival order (#149).
- Arbitration moved slightly with #88, because the stub manager now declines a moment worth 0 IP as production does: worst friend's reach 23% to 22%, Priya's reach 52% to 53%, shown swing 99.6% to 99.7% of the biggest. #149 did not change it: the commit before it gives the same table.
- Ours already lost on delivery and fallbacks; it now also loses on decisions over the budget (1 against 0), calls cut off by the budget (2 against 0), and tokens, which are a proven loss this time because none of the one agent's calls were cut off. Ours retried 6 explanations against 0 in the last run; the room check on every pitch (#138) is a likely reason, but one run cannot separate it from the service's load.

## Method

- Arbitration: seeds 0 to 49 (50 matches), no model calls. Every method gets the same moments and, per moment, the same eligible managers (stake above threshold). A moment with nobody eligible is not decided by any method.
- Ours here runs the production workflow with stub agents: every manager bids the plain-stat line at urgency 0.5 and the host takes the scorer's order. This measures the scorer and fairness ledger only.
- Airtime share: a friend's screens over decided moments, per match, over the friends eligible at least once in that match (a friend never eligible had nothing to win). Spread: largest share minus smallest. Reach: a friend's screens over their eligible moments. Shut out: a friend eligible at least once in a match who never got the screen.

Controls each method had:

| Method | Controls |
|---|---|
| ours | production checks: text, angle and the room check on the pitch (#138), one retry (`checked`), 4 s per bid and 2.5 s host inside the 4.5 s budget, plain-stat fallback per manager, scorer's pick if the host fails |
| one_agent | the manager agent's text checks on every explanation owed and on the chosen friend's (no pitch, so no angle or room check), and the host agent's checks on the pick and reason, over the same candidate facts (overruled_count is 0: it has no close calls); one retry (`checked`); the 4.5 s budget on the whole call; plain-stat fallback per explanation (explanations that pass on the second attempt are kept); biggest swing if the pick fails |
| greedy | none needed: code writes the plain-stat line for every eligible manager; no model, no budget |

## Arbitration

Denominators: 392 decided moments; 245 friend-matches with at least one eligible moment.

| Measure | ours | greedy |
|---|---|---|
| Mean airtime spread (lower is fairer) | 0.40 | 0.43 |
| Mean minimum airtime share | 2.5% | 2.3% |
| Friends shut out (of 245) | 52 | 59 |
| Reach, all friends | 42% | 42% |
| Reach, worst friend | 22% | 19% |
| Reach, Jo (mgr_1) | 39% | 46% |
| Reach, Priya (mgr_2) | 53% | 52% |
| Reach, Arjun (mgr_3) | 43% | 40% |
| Reach, Leo (mgr_4) | 22% | 19% |
| Reach, Sam (mgr_5) | 46% | 43% |
| Screen shows the biggest swing | 98% | 100% |
| Shown swing / biggest swing | 99.7% | 100.0% |

## Number checker on injected false numbers

100 of 100 caught. Code, not a model, wrote each false number into a manager's plain-stat line in their own language: in turn a wrong value, a flipped sign, a wrong league total, and the true value in the wrong unit. Each line was checked against the evidence it cites. This measures the number check both model methods run; no model-written text is part of it.

## Models

Model gpt-5.4-mini for every agent call. Held-out seeds 1000, 1001, 1002, 1003: the same 20 moments for both, 44 explanations owed to each, one per friend whose moment is worth points (a moment worth 0 IP is left out for both: ours may decline it, #88). Cost at $0.75 in and $4.5 out per 1M tokens. Laptop to Azure, not in-region.

| Measure | ours | one agent |
|---|---|---|
| Explanations delivered in 4.5 s | 86% (38/44) | 93% (41/44) |
| of which after the one retry | 6 | 12 |
| Fell back to the plain line | 6 | 3 |
| Screen picked by a model | 4 | 19 |
| Screen picked by our scorer (clear winner, no host call) | 15 | n/a |
| Screen pick fell back to code | 1 | 1 |
| Decision latency p50 / p95 ms | 2916 / 4020 | 1888 / 4332 |
| Decisions over 4.5 s | 1 | 0 |
| Model calls | 75 | 27 |
| of which cut off by the budget (no tokens reported) | 2 | 0 |
| Tokens in / out | at least 110365 / 8913 | 83812 / 5933 |
| Cost, USD | at least 0.1229 | 0.0896 |

The retry row does not compare like with like: our agents retry one friend's bid at a time, while the one agent retries its whole answer, so every owed friend whose line passes on that second attempt counts as retried.

## Where ours loses

- Moments showing the biggest swing: ours 98.5%, greedy 100%.
- Shown swing as a share of the biggest: ours 99.7%, greedy 100.0%.
- Jo's reach: ours 39%, greedy 46%.
- Explanations delivered in the budget: ours 86%, one agent 93%.
- Explanations that fell back: ours 6, one agent 3.
- Decision latency, median: ours 2916 ms, one agent 1888 ms. At p95 ours is faster, 4020 ms against 4332 ms.
- Decisions over 4.5 s: ours 1, one agent 0.
- Model calls: ours 75, one agent 27.
- Model calls cut off by the budget: ours 2, one agent 0.
- More tokens: ours at least 110365 in and 8913 out, one agent 83812 in and 5933 out. None of the one agent's calls were cut off, so its figures are exact and ours are already higher.

## Limitations

- The arbitration run does not include the host model's judgement; the model run does, on few moments.
- The model sample is small and one run: latency and delivery move with the service's load that hour.
- Latency is from a laptop; production calls run in-region.
- Tokens of a call cut off by the budget are not reported by the service: tokens and cost are then a lower bound, marked 'at least', and a cost difference against a lower bound is not listed as a loss.
- The one agent keeps any explanation that passes on its second attempt, which is generous to it: its single answer is otherwise all or nothing.
- The one agent writes no pitch, so its answer skips two checks our agents run on every bid: the angle check and the room check on the pitch (#138). Its explanations get the same text checks as ours. This exemption favours the baseline.
- Both baselines are ours to build; a stronger one-agent prompt might do better.
- Airtime is measured from five managers with fixed squads (league_v1); another league may differ.
