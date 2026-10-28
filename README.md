# Couch Gaffer

A mini-league watch party. Five friends in one fantasy league watch the same matches on one shared screen: by default a gameweek of three matches at once, or a single match. Each friend has an AI agent that argues for the moments that matter to their squad, and a host agent decides what the shared screen shows and keeps it fair. Each friend also gets their own explanations on their phone, in English or Spanish, and a recap video at full time.

All match data is synthetic: the clubs, players, managers and the match itself are invented and generated in code.

**Try it:** https://couchgaffer.com. No sign-in. A gameweek or a single match takes about three minutes.

Built from 20 Oct 2026 in a private repository; the full development history is available to the organisers on request.

## Testing instructions

1. Open the link and choose **Watch now**. Its three steps pick your manager (the other four are bots), your phone (casual or analyst, and a language) and the match; each has a default ("A gameweek: three matches at once", the demo match), so you can press **Start the watch party** straight away. For one match instead, choose "A single match" in the third step.
2. The group screen plays the gameweek at 30 times real speed: three matches on one clock, one league table for all of them. The screen shows the match of the card on screen, and the top bar shows all three scores, the one on the big screen marked with a dot. On a phone, tap a score to follow that match there. When something matters to a friend, their agent writes a line for their phone and a one-line pitch to the host.
3. Open **Phone and tools** at the top right: scan its QR code to follow your manager on your own phone, or use the phone in it.
4. Watch for a close call: two friends' agents both have a strong case. Whether one comes depends on the agents' bids, so a run may have none. The group screen gives way to their two pitches side by side, then the verdict (with the host's reason when the host decided), then the chosen moment. The friend who lost the screen gets a note on their phone.
5. Open **Behind the scenes** to see each decision: every agent's bid and how long it took, the scores with each friend's airtime and lost close calls, who decided (with the host's time and reason on a close call). Close the panel before the next step.
6. In **Phone and tools**, under **Try a failure**, press **Knock out your agent** (or pick another friend's phone first to knock out theirs). The next time that agent bids, its phone gets a plain stat line from code while the other agents carry on, so the match never stalls.
7. At half time and full time each friend gets a summary. A summary agent writes it with the match tools, across all three matches in a gameweek, or code writes it if that agent fails. Within a few minutes of full time each friend gets a recap video cut around their own players.
8. Then play it as a league: on the first page choose **Play with your friends**, start a league, open its join link in a private window as a second friend and join, pick five players and a captain in each, and press **Start gameweek 1**. At full time the season table on the league's page shows the result.

Each address gets 4 matches an hour with the AI agents. Later matches run on plain stat lines from code, with no recap.

## How it works

```
simulator ──> points engine ──> manager agents (one per friend, in parallel) ──> scorer ──> host agent (close calls only) ──> cues ──> group screen and phones
                     │                    │
                     └── facts and evidence, checked numbers ──┘                full time ──> queue ──> recap job ──> videos
```

- The simulator ([`sim/engine.py`](backend/watchparty/sim/engine.py)) streams events: passes, shots, tackles, pressure, cards.
- The points engine ([`points/engine.py`](backend/watchparty/points/engine.py)) scores each friend's squad and builds a stake for every key moment: the points swing, the evidence for each number, and facts such as a player only this friend owns, a captain swing, a rank change, or a player's expected against actual goal points.
- A manager agent per friend ([`agents/manager.py`](backend/watchparty/agents/manager.py)) writes that friend's explanation and a pitch to the host. A checker ([`agents/checks.py`](backend/watchparty/agents/checks.py)) rejects any number not in the evidence, any name not in the match, and a wrong language; one retry, then a plain stat line.
- The scorer ([`agents/scorer.py`](backend/watchparty/agents/scorer.py)) ranks the bids by stake and urgency, weighted by how often each friend has been on screen and how many close calls they have lost. Clear winners go straight to the screen; close calls go to the host agent ([`agents/host.py`](backend/watchparty/agents/host.py)), whose stated reason must be true of the moment it picks.
- Every output is a timed cue with a schema ([`contracts/schemas/`](contracts/schemas/)), so the screens are overlays that any player could render.

Each moment has 4.5 seconds from arrival to the screen. An agent that misses its slice falls back to code for that friend only.

A gameweek ([`sim/gameweek.py`](backend/watchparty/sim/gameweek.py)) builds three fixtures among six invented clubs from one seed, on one clock, with 15 to 18 key moments at least three match minutes apart, so each moment is still decided on its own by the same flow. One table ([`points/standings.py`](backend/watchparty/points/standings.py)) adds every friend's points from all three. If one fixture fails while it is being handled, it alone stops and the other two play on. The single match is one choice away in Watch now.

## Microsoft technology

| Technology | Where |
|---|---|
| Microsoft Foundry models (`gpt-5.4-mini`) | [`agents/foundry.py`](backend/watchparty/agents/foundry.py) |
| Microsoft Agent Framework: the per-moment workflow, agents, function middleware | [`app/workflow.py`](backend/watchparty/app/workflow.py), [`agents/foundry.py`](backend/watchparty/agents/foundry.py) |
| MCP: the match tools the summary agent calls (standings, squad points, key moments, rules) | [`matchtools/server.py`](backend/watchparty/matchtools/server.py), [`docs/mcp.md`](docs/mcp.md) |
| Foundry IQ (Azure AI Search knowledge base): the league rulebook the summaries cite | [`knowledge.py`](backend/watchparty/knowledge.py), [`tools/foundry_iq.py`](backend/tools/foundry_iq.py) |
| Azure Container Apps, and a Container Apps Job for the recaps, triggered by Azure Storage queues; recaps in Blob Storage | [`infra/main.bicep`](infra/main.bicep), [`recap/`](backend/watchparty/recap/) |
| The app signs in with managed identities; it holds no keys | [`infra/README.md`](infra/README.md) |
| Azure Managed Redis: shared live state, so two replicas serve every screen | [`app/shared.py`](backend/watchparty/app/shared.py) |
| Azure Database for PostgreSQL: leagues, squads and seasons | [`store/`](backend/watchparty/store/) |
| Azure Monitor: Log Analytics, and Application Insights traces with no prompts, answers or names | [`infra/main.bicep`](infra/main.bicep), [`telemetry.py`](backend/watchparty/telemetry.py) |

## Does the multi-agent design earn its place?

[`tools/evidence.py`](backend/tools/evidence.py) runs the same moments three ways under the same 4.5 second budget and the same text checks on each explanation (the one agent writes no pitch, so it skips our angle and room checks): our agents, one model call that writes for every friend whose moment is worth points and picks the screen, and a code rule that always shows the biggest swing. In one run on 20 held-out moments with `gpt-5.4-mini`, from a laptop to Azure ([full report](docs/evidence.md)):

| | Ours | One agent |
|---|---|---|
| Explanations delivered in time | 86% | 93% |
| Delivered after the one retry | 6 | 12 |
| Fell back to the plain line | 6 | 3 |
| Decisions over the 4.5 s budget | 1 | 0 |
| Median decision time | 2.9 s | 1.9 s |
| Model calls | 75 | 27 |

The retry row does not compare like with like: our agents retry one friend's bid at a time, while the one agent retries its whole answer, so every owed friend whose line passes on that second attempt counts as retried.

In this run a single agent did better on almost every model measure: it delivered more explanations, fell back less, never went over budget, answered faster at the median and used fewer calls and tokens. Ours was faster only at the slowest end (p95 4.0 s against 4.3 s), and each method's screen pick fell back to code once. What the separate agents give is an argument you can see: each friend's agent makes its case from its own facts, the host has to choose, and Behind the scenes shows every argument and decision. Without models, over 50 matches, the fairness weighting puts 22% of the least-served friend's moments on the shared screen, against 19% under the biggest-swing rule. The full report lists every measure where ours loses.

## Run it locally

`make run` needs only Docker. `make check` needs Python 3.12 with [uv](https://docs.astral.sh/uv/) and Node 22 or later.

```sh
make check   # contracts, backend lint, types and tests, frontend tests and build
make run     # build and serve on http://127.0.0.1:8000
```

Without Azure settings the agents are off: every line comes from code and there are no recap videos, so the whole match runs offline. To run the agents on your own Foundry deployment, see [`infra/README.md`](infra/README.md).

## Repository

| Path | What |
|---|---|
| [`backend/watchparty/`](backend/watchparty/) | Simulator, points engine, agents, match tools, the app and the recap renderer |
| [`frontend/`](frontend/) | The landing page, Watch now, the league pages, the group screen and the phone, in React |
| [`contracts/`](contracts/) | JSON Schemas shared by the backend and the frontend, with examples |
| [`infra/`](infra/) | Bicep for the Azure resources (the Foundry resource is created separately) |
| [`docs/`](docs/) | The concept, the dataset, the match tools, the evidence report and the decision records |

MIT licence. Third-party notices are in [`NOTICE.md`](NOTICE.md).
