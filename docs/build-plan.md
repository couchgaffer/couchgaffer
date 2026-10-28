# Build plan

Agreed on Wed 21 Oct 2026 in a review of `concept.md` against the official rules. Where later decisions replaced part of it, `decisions/` says so.

## The product in one paragraph

Five managers in a fantasy-style mini-league watch one synthetic match. A judge opening the demo link plays one of them; four bots play the rest. Each manager's phone explains what each moment means for their own squad. One group screen shows one graphic at a time: code ranks the managers' bids, and a host agent settles close calls and says why on screen. At full time, every manager gets a recap video cut around their own players.

## Decisions

### What a judge sees

- The judge picks one of five managers. The other four are bots with fixed squads.
- One page: the group screen, the judge's phone panel beside it, a toggle to peek at any other manager's feed, and the mini-league table.
- A QR code opens the same phone view on a real phone.

### The match

- The full 90 minutes at about 30x: about 3 real minutes. No skip button.
- 12 to 15 key moments from a library of scripted scenarios (counterattack, high press, set piece, keeper save run, late equaliser) with seeded variation, and filler play between them.
- Event-level simulation with approximate player positions, not full physics.
- One fixed demo seed, used for the video and offered to judges, guarantees a dramatic match. A random seed is optional.
- Consequence of 30x, added in the review: key moments arrive about every 12 to 15 s. So the simulator spaces key moments at least about 10 s apart in real time, a group-screen graphic stays up at least 6 s. Moments are decided one at a time; the merging first planned here was removed (decisions/0003).

### Scoring

- Our own invented rules with our own names, for example impact points from chance quality, progressive passes, saves weighted by shot quality, and a captain at x2.
- Described as "a fantasy-style mini-league". Nothing that copies another fantasy game's rules, names or branding (rules 4.4 and 4.7).
- The points engine is deterministic code. It is the source of every number an agent may use.

### Agents

```
event → points engine: stake per manager (code)
  stake < threshold → no model call; silent, or a plain stat
  stake ≥ threshold → manager agent → {explanation, pitch_to_host, evidence_ids}
                    → number checker → phone, and a bid to the scorer
bids → scorer: stake × urgency × fairness ledger (code)
  clear winner → air it, no model call
  close call   → host agent → pick + one-line visible reason
  timeout      → scorer's pick + plain stat
```

- **Manager agent:** one parameterised agent, run for each manager with a real stake (usually one or two of five per moment). It writes the personal explanation in that manager's language, a one-line pitch to the host, and evidence IDs for every number.
- **Number checker:** code. Any number not backed by an evidence ID is rejected. One retry, then a plain stat.
- **Scorer and fairness ledger:** code. It tracks airtime per manager and how often each has been overruled.
- **Host agent:** called only when the top bids are close, or when fairness conflicts with importance. Its reason is shown on the group screen ("showing Priya's keeper: she has been overruled twice").
- **Failure recovery:** if any agent times out, the scorer's pick and a plain stat go out. This is the failure beat in the video.
- **MCP tools:** squad points, match stats, standings and scoring rules. Manager agents call tools themselves only for the half-time and full-time recaps, where there is no latency limit.

### Latency gate

- Test on day 1: 30 scripted moments against the deployed models. Measure p50 and p95.
- Pass: a plain stat card from code at once, and the personal explanation at p95 of 4 s or less.
- If it fails: the explanations for every manager at stake come from one batched call returning JSON; a faster model handles the managers; the host stays on the larger model.
- The models are chosen by this test, not in advance.

### Azure

```
browser ──ws──▶ [Container App: simulator → points → Agent Framework workflow
                                → MCP tools → cue stream → static front end]
                       │ full time
                       ▼
               [Container Apps Job: ffmpeg recaps] → Blob → phone
Foundry: gpt-5.4-mini for managers and host (decisions/0001)
```

- One app process holds the simulator, points engine, workflow, MCP server, push and front end. `minReplicas: 1`.
- Each judge session runs its own seeded match in memory. No database.
- The recap job is event-driven: it is triggered at full time and writes five videos to Blob.

### Scope

All four are in scope:

1. Per-manager recap video: one template, rendered from the cue log, no voiceover. All five rendered; two shown in the video.
2. One second language, for the explanation text only.
3. Real-phone QR view, as the same responsive route as the phone panel.
4. Foundry IQ holding the scoring rules and squads.

Contracts live in `contracts/schemas/`, with examples in `contracts/examples/`.

## Risks

| Risk | Mitigation |
|---|---|
| Agent theatre: judges see the host as a function with a job title | The LLM host is used only where reasoning is needed, and its reason is visible; the fallback is shown working |
| Latency at 30x with moments about 12 to 15 s apart | Stake gating, the 4 s gate, minimum dwell |
| Scoring or naming read as copying another fantasy game | Invented rules and names; check before the video |
| Demo link breaks | Fixed demo seed; a daily smoke test of the public path |
