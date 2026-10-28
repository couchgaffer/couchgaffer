"""The single match path holds still (#149, #94 case 3): every screen gets exactly what develop sent it.

golden/demo_log.json.gz was recorded by tools/golden_log.py on a tree of origin/develop at ff6858a, before moments
could overlap. Re-record it only for a change meant to alter what screens receive, and say so in its PR.
"""

import gzip
import json
from pathlib import Path
from typing import Any

import pytest

from tools.golden_log import compact, record

pytestmark = pytest.mark.anyio
GOLDEN = Path(__file__).parent / "golden" / "demo_log.json.gz"


def first_difference(got: list[Any], want: list[Any]) -> str:
    for i, (a, b) in enumerate(zip(got, want, strict=False)):
        if a != b:
            return f"message {i}: got {json.dumps(a)[:300]}, want {json.dumps(b)[:300]}"
    return f"lengths differ: got {len(got)}, want {len(want)}"


async def test_the_demo_match_sends_every_screen_what_develop_sent() -> None:
    got = json.loads(json.dumps(compact(await record()), sort_keys=True))
    want = json.loads(gzip.decompress(GOLDEN.read_bytes()))
    assert set(got) == set(want)
    for screen in want:
        assert got[screen] == want[screen], f"{screen}, {first_difference(got[screen], want[screen])}"
