"""The live end-to-end check's pure helpers (#204). The browser run itself needs a deployed site and is not run here."""

import tempfile
from pathlib import Path

import pytest

from tools.e2e_live import Fail, club_names, default_out, five_phones, frame_gaps, parse_cases, same_fixtures, scrub


def test_scrub_cuts_tokens_from_a_failure_reason() -> None:
    reason = "Page.goto: Timeout navigating to http://h/phone/abc123def/mgr_1#token=SECRET&manager=mgr_1\nCall log: ..."
    assert scrub(reason) == "Page.goto: Timeout navigating to http://h/phone/…#…"
    assert "SECRET" not in scrub("ws://h/ws/s/phone:mgr_1?token=SECRET&cues=only closed")
    assert "SECRET" not in scrub("header Authorization: Bearer SECRET refused")


def test_parse_cases_reads_lists_and_ranges() -> None:
    assert parse_cases(None) == list(range(1, 23))
    assert parse_cases("20,1,3-5") == [1, 3, 4, 5, 20]
    with pytest.raises(ValueError, match="no case 23"):
        parse_cases("22-23")


def test_frame_gaps_counts_missing_frames_per_fixture() -> None:
    whole = [("fx_1", t) for t in range(0, 30_000, 3_000)]
    assert frame_gaps(whole) == {"fx_1": 0}
    # Another fixture's frame at the missing time does not hide the gap
    holed = [f for f in whole if f[1] not in (9_000, 12_000)] + [("fx_2", 9_000), ("fx_2", 12_000)]
    assert frame_gaps(holed) == {"fx_1": 2, "fx_2": 0}


def test_club_names_ignores_the_score() -> None:
    assert club_names(["KES 1-0 RAV", "PEL 0-0 HOL"]) == club_names(["PEL 2-1 HOL", "KES 1-1 RAV"])


def test_scrub_hides_session_and_manager_ids_in_paths() -> None:
    line = scrub("Page.goto: net::ERR at http://h/phone/AbCdEf123456/mgr_2 and ws://h/ws/AbCdEf123456/phone:mgr_2")
    assert "AbCdEf123456" not in line
    assert "mgr_2" not in line


def test_same_fixtures_fails_on_an_empty_strip() -> None:
    same_fixtures(["KES 1-0 RAV"], ["KES 1-1 RAV"])
    for group, phone in (([], []), (["KES 1-0 RAV"], []), ([], ["KES 1-0 RAV"])):
        with pytest.raises(Fail, match="no fixtures"):
            same_fixtures(group, phone)
    with pytest.raises(Fail, match="the phone shows"):
        same_fixtures(["KES 1-0 RAV"], ["PEL 0-0 HOL"])


def test_five_phones_needs_five_distinct_friends() -> None:
    five_phones(["You", "Priya", "Arjun", "Leo", "Sam"])
    for tabs in ([], ["You", "Priya", "Arjun", "Leo"], ["You", "Priya", "Arjun", "Leo", "Leo"]):
        with pytest.raises(Fail, match="not five friends"):
            five_phones(tabs)


def test_failure_screenshots_go_outside_the_checkout() -> None:
    out = default_out().resolve()
    checkout = Path(__file__).resolve().parents[2]
    assert not out.is_relative_to(checkout)
    assert out.is_relative_to(Path(tempfile.gettempdir()).resolve())
