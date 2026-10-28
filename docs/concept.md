# Concept: mini-league watch party

Working title. Chosen on 20 Oct 2026.

## The idea

Five friends play fantasy football in the same mini-league. Each owns different players, so each cares about different moments in the same match. They watch one synthetic match together:

- Every manager gets live explanations about their own squad: "your captain just had a big chance", "one more save and your keeper gets a bonus point", "bad news: the league leader's captain is heating up".
- One shared group screen shows one graphic at a time. Each manager's agent argues for the moment that matters to its manager; a host agent decides what the group sees and keeps it fair.
- At full time, each manager gets their own short recap video, cut around their players.

## Why this direction

- The agents have competing interests and shared state: each argues for its own manager and the host keeps the group screen fair.
- The Premier League and Microsoft plan an AI assistant manager inside Fantasy for transfer advice. This entry is about watching a live match as a group, not transfer advice.

## How it covers the five stages in the brief

1. Ingest: a seeded simulator streams synthetic match events.
2. Interpret: deterministic code computes match stats, chance quality and live fantasy points.
3. Explain: an agent per manager explains why a moment matters to that manager's squad.
4. Render: timed overlay cues for the group screen and each manager's screen.
5. Personalise: by squad, explanation style (casual or analyst) and language (English or Spanish).

## Riskiest parts, to test first

1. Latency: five manager agents plus a host must decide within the broadcast delay. Run 30 scripted moments through the deployed model and measure p50 and p95.
2. A simulator that looks like football, not random dots.
3. Cutting five recap videos within seconds of full time.
