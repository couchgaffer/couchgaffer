# 0003: Deciding a moment before the screens show it

- **Status:** decided, 24 Oct 2026 (#26)
- **Decision:** the bids and the host share one 4.5 s budget, counted from when the moment reaches the server. Moments are decided one at a time, each by its own deadline. Merging moments is removed.

## Context

- Screens run 5 s behind the stream (`playback_delay_ms`), so the group decision must arrive within 5 s of the moment or the screen shows it late.
- The manager deadline (4.0 s) and the host deadline (2.5 s) ran one after the other, 6.5 s in the worst case. The medians (bids 1.5 s, host 0.8 s; 0001-models.md) made a late decision rare, not impossible.
- The build plan merged moments that landed close together into one host decision. The simulator keeps key moments at least 5 match minutes apart (`MOMENT_SPACING`; 10 s of real time at 30x), and across 400 seeds the smallest gap is 5.5 minutes, against a merge window of 3. Merging never ran outside a test, and where it did run it showed one moment's headline over another's explanations.

## The budget

| | Before | Now |
|---|---|---|
| Bids | 4.0 s | at most 4.0 s, and never past the moment's deadline |
| Host | 2.5 s after the bids | what is left of 4.5 s, at most 2.5 s |
| Worst case | 6.5 s | 4.5 s, inside the 5 s playback delay |

- The host is not called when less than a quarter of its 2.5 s is left: that call would almost surely be cut off, so the scorer's pick goes out (`source: "fallback"`) without spending one.
- We did not raise the playback delay instead: every screen would run further behind the match for a case the medians say is rare.

## One moment at a time

- Each moment is decided alone, by its own deadline. Since #149 its bids start as it arrives, even while an earlier moment is still being decided; the scorer, the host and the ledger then take one moment at a time, in arrival order. A moment whose turn comes with under a quarter of the host's time left gets the scorer's pick, so both are decided before they are shown. A manager agent sees the ledger as it was when its bid started, so on overlapping moments it may not know the earlier decision; the scorer and host rank on the full ledger.
- Tests: `test_a_host_with_too_little_time_left_is_not_called` (bids at 3.9 s and the host at 2.4 s, scaled down tenfold), `test_a_second_moment_bids_on_arrival_and_both_are_decided_before_they_are_shown` (two moments 1 s apart, also scaled tenfold) and `test_a_turn_that_comes_with_under_a_quarter_of_the_hosts_time_left_goes_to_the_scorer`. `test_sim.py` checks the spacing (`MIN_GAP`).
- `merged_moment_ids` is gone from the HostDecision contract.

This replaces the merging described in `docs/build-plan.md`.
