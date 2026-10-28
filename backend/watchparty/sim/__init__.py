from watchparty.sim.engine import Match, messages, play, simulate

# The match for the demo video and for judges: 14 key moments, four keeper save runs (both Wall tiers),
# a red card, and a late equaliser before the 2-1 winner. Pinned by test_demo_seed_has_the_story_for_the_video.
DEMO_SEED = 267
# The gameweek the lobby starts by default (#155): every friend has at least two moments worth points, in at least
# two fixtures; one moment reaches Priya's analyst phone and a casual friend's; each fixture has key moments in
# both halves; and with fake agents the host settles a close call. The only seed of 0 to 599 with all four
# (tools/demo_gameweek_scan.py). Pinned by test_demo_gameweek_has_the_story.
DEMO_GAMEWEEK_SEED = 368

__all__ = ["DEMO_GAMEWEEK_SEED", "DEMO_SEED", "Match", "messages", "play", "simulate"]
