import re
from typing import Any

import pytest

from watchparty.agents.base import room_line
from watchparty.agents.checks import (
    language_problems,
    names_problems,
    number_problems,
    number_tokens,
    room_problems,
    style_problems,
    tone_problems,
    undash,
)
from watchparty.points import RULES

EVIDENCE: list[dict[str, Any]] = [
    {"id": "ev_delta", "value": 8, "unit": "ip"},
    {"id": "ev_xg", "value": 0.31, "unit": "xg"},
    {"id": "ev_saves", "value": 3, "unit": "count"},
    {"id": "ev_rank", "value": 2, "unit": "count"},
    {"id": "ev_possession", "value": 0.55, "unit": "ratio"},
    {"id": "ev_loss", "value": -1.5, "unit": "ip"},
    {"id": "ev_minute", "value": 34, "unit": "minute"},
]
CITED = [str(e["id"]) for e in EVIDENCE]


@pytest.mark.parametrize(
    "text",
    [
        "Third save of the night, 8 IP.",  # ordinal word
        "A 0.31 xG chance, saved: +8 IP.",
        "That was a 0.3 xG chance.",  # rounded to the precision shown
        "They have 55% of the ball.",  # a ratio shown as a percentage
        "Down 1.5 IP after the card.",  # a negative value, written without its sign
        "-1.5 IP after the card.",  # or with it
        "+8 IP for your keeper.",
        "Saved in the 34th minute.",
        "Saved on 34'.",
        "Eight points for your keeper.",
        "You climb to 2nd.",
        "Eight saves for your keeper.",  # number words
        "A huge moment for your keeper.",  # no numbers at all
    ],
)
def test_numbers_backed_by_cited_evidence_pass(text: str) -> None:
    assert number_problems(text, EVIDENCE, CITED) == []


DIFFICULTY = [{"id": "ev_difficulty", "value": 0.72}]  # a pass's difficulty, 0 to 1, has no unit (#87)


@pytest.mark.parametrize("text", ["A 0.72 xG pass.", "A pass of 72% difficulty.", "You gained 0.72 points."])
def test_a_number_written_with_a_unit_never_matches_an_item_without_one(text: str) -> None:
    assert number_problems(text, DIFFICULTY, ["ev_difficulty"]) != []


@pytest.mark.parametrize(
    ("text", "lang"), [("A pass of difficulty 0.72.", "en"), ("Un pase de dificultad 0,72.", "es")]
)
def test_a_number_without_a_unit_matches_an_item_without_one(text: str, lang: str) -> None:
    assert number_problems(text, DIFFICULTY, ["ev_difficulty"], lang) == []


@pytest.mark.parametrize(
    "text",
    ["That is 9 IP.", "A 0.4 xG chance.", "They have 60% of the ball.", "Fourth save.", "Ten points!", "Up 12 places."],
)
def test_numbers_without_evidence_fail(text: str) -> None:
    assert number_problems(text, EVIDENCE, CITED)


@pytest.mark.parametrize(
    "text",
    [
        "-8 IP for your keeper.",  # evidence is +8 (#23)
        "You lost 8 IP there.",
        "Gained 1.5 IP after the card.",  # evidence is -1.5
        "+1.5 IP after the card.",
        "A 3 xG chance.",  # 3 is a count, not an xG (#23)
        "A 2 xG chance.",
        "Saved in the 8th minute.",  # 8 is IP, not a minute
        "That is 31% of the ball.",  # 0.31 is an xG, not a ratio
        "8 xG.",
    ],
)
def test_a_number_must_keep_its_sign_and_unit(text: str) -> None:
    assert number_problems(text, EVIDENCE, CITED)


@pytest.mark.parametrize(("text", "lang"), [
    ("An xG 3 chance.", "en"),  # #45: 3 is a count of saves, and the unit can come first
    ("Saved on minute 8.", "en"),  # 8 is IP
    ("Parada en el minuto 8.", "es"),
    ("You lost 8 there.", "en"),  # +8 IP, with no "IP" written after it
    ("Pierdes 8 aquí.", "es"),
])  # fmt: skip
def test_a_unit_before_the_number_and_a_sign_word_without_ip_are_checked(text: str, lang: str) -> None:
    assert number_problems(text, EVIDENCE, CITED, lang)


@pytest.mark.parametrize("text", ["An xG 0.31 chance.", "Saved on minute 34.", "You gained 8 there.",
                                  "Down 1.5 after the card."])  # fmt: skip
def test_units_and_sign_words_that_agree_with_the_evidence_pass(text: str) -> None:
    assert number_problems(text, EVIDENCE, CITED) == []


def test_a_score_is_not_a_negative_number() -> None:
    assert number_problems("Up 3-2.", EVIDENCE, CITED) == []  # 3 saves, rank 2: the dash is not a sign


@pytest.mark.parametrize(("value", "unit", "text", "ok"), [
    (0.62, "xg", "1 big chance.", False),  # rounding to a whole number moved it by 38 % (#23)
    (0.31, "xg", "0 xG.", False),
    (0.62, "xg", "0.6 xG.", True),
    (283.5, "ip", "284 IP.", True),  # 0.2 %
    (9.5, "ip", "10 IP.", False),  # 5.3 %
    (0.857, "ratio", "86%.", True),
    (282.5, "ip", "283 IP.", True),  # #49: a half rounds up, as people round, not to the even neighbour
    (3.5, "ip", "+3.5 IP.", True),
])  # fmt: skip
def test_rounding_may_not_move_a_value_by_more_than_5_percent(value: float, unit: str, text: str, ok: bool) -> None:
    assert (number_problems(text, [{"id": "ev", "value": value, "unit": unit}], ["ev"]) == []) is ok


def test_twice_is_a_count_too() -> None:
    overruled_once = [{"id": "ev_overruled", "value": 1}]
    assert number_problems("Overruled twice now.", overruled_once, ["ev_overruled"])
    assert number_problems("Overruled twice now.", [{"id": "ev_overruled", "value": 2}], ["ev_overruled"]) == []


def test_only_cited_evidence_counts() -> None:
    assert number_problems("That is 8 IP.", EVIDENCE, ["ev_xg"])
    assert number_problems("That is 8 IP.", EVIDENCE, ["ev_delta", "ev_missing"]) == [
        "cites unknown evidence ev_missing"
    ]


KNOWN = [
    "Rhys Mallory",
    "Jonah Pryce",
    "Hollinsford United",
    "Saltmere Rovers",
    "Priya",
    "Tayo Brennan",
    "Ade O'Neill",
    "You",
]


@pytest.mark.parametrize(
    "text",
    [
        "Rhys Mallory denies Jonah Pryce again.",
        "Great save! Priya's keeper Mallory is on fire.",
        "Saltmere Rovers hold on. Your captain keeps the Wall tier alive.",
        "That is 8 IP for you.",
        "Brennan scored again.",  # a sentence may start with a name from this match (#23)
        "Still level at the break.",  # or with an ordinary word
        "Biggest swing: a save.",  # seen live, 25 Oct
        "Mid-table for you now.",  # #49
        "GOAL! Priya is top.",  # capitals for a shout, not a name
        "Half time: you're 2nd.",
        "You\u2019re top. Don't look down.",
        "Estás arriba. Tienes a Priya detrás.",
        "O'Neill keeps it out.",
    ],
)
def test_known_names_and_ordinary_sentences_pass(text: str) -> None:
    assert names_problems(text, KNOWN) == []


@pytest.mark.parametrize(
    "text",
    [
        "He is playing like Oskar Vasquith tonight.",
        "A finish worthy of Castlebridge on a good day.",
        "Mallory channels his inner Henrik Vandrup.",
        "Vasquith scored again.",  # the first word of a sentence is checked too (#23)
        "Great save. Castlebridge would want him.",
        "Saved! Brannock next?",
        "Real Valmora would want him.",
        "ZELKOV would have scored that.",  # #49: capitals do not hide a name
        "Show Priya: Vasquith-level save.",
    ],
)
def test_names_from_outside_the_match_fail(text: str) -> None:
    assert names_problems(text, KNOWN)


@pytest.mark.parametrize("text", ["Want to bet on it?", "That's worth £5.", "First prize to Priya!", "Pure gambling."])
def test_betting_money_and_prize_language_fails(text: str) -> None:
    assert tone_problems(text)


@pytest.mark.parametrize("text", ["A better save you will not see.", "Against the odds, he scores."])
def test_ordinary_words_pass_the_tone_check(text: str) -> None:
    assert tone_problems(text) == []


@pytest.mark.parametrize("text", ["Nice bonus for Priya.", "A clean sheet in sight."])
def test_the_other_fantasy_games_terms_fail_the_tone_check(text: str) -> None:
    assert tone_problems(text)


@pytest.mark.parametrize(
    ("text", "ok"),
    [
        ("Big save, 8 IP for you!", True),
        ("Big save for you, worth 8 IP, from a 0.31 xG chance.", False),  # two numbers
        ("Big save. Your keeper is flying.", False),  # two sentences
        (
            "What a save from your keeper, the kind of stop that keeps a whole mini-league chat buzzing and talking for"
            " days and days afterwards!",
            False,
        ),  # over 120 characters
    ],
)
def test_casual_is_one_short_sentence_with_at_most_one_number(text: str, ok: bool) -> None:
    assert (style_problems(text, "casual") == []) is ok


def test_analyst_allows_two_sentences_with_numbers() -> None:
    assert style_problems("A 0.31 xG chance saved. That is 8 IP and the Wall tier.", "analyst") == []
    assert style_problems("One. Two. Three sentences is too many.", "analyst")


@pytest.mark.parametrize(
    "text",
    [
        "Una ocasión de 0,31 xG, parada: 8 IP para ti.",  # decimal comma
        "Pierdes 1,5 IP con la tarjeta.",
        "Tienen el 55 % del balón.",  # a space before %
        "Tercera parada de la noche, 8 IP.",  # ordinal word
        "Ocho puntos para tu portero.",  # number word
        "Subes al 2.º puesto.",
        "¡Qué parada!",
    ],
)
def test_spanish_numbers_backed_by_cited_evidence_pass(text: str) -> None:
    assert number_problems(text, EVIDENCE, CITED, lang="es") == []


@pytest.mark.parametrize("text", ["Son 9 IP.", "Una ocasión de 0,4 xG.", "Tienen el 60 % del balón.", "Cuarta parada.",
                                  "¡Diez puntos!"])  # fmt: skip
def test_spanish_numbers_without_evidence_fail(text: str) -> None:
    assert number_problems(text, EVIDENCE, CITED, lang="es")


def test_number_words_belong_to_their_language() -> None:
    assert number_problems("Saved once more.", EVIDENCE, CITED) == []  # English "once" is not eleven
    assert number_problems("Once paradas.", EVIDENCE, CITED, lang="es")  # Spanish "once" is


def test_spanish_names_with_accents_are_checked_whole() -> None:
    problems = names_problems("Una parada digna del Valdoré.", KNOWN)
    assert problems == ["'Valdoré' is not a club, player or manager in this match"]
    assert names_problems("La quiere. ¿Zelkov?", KNOWN)[0].startswith("'Zelkov' starts a sentence")
    assert names_problems("¡Qué noche para Priya y Rhys Mallory!", KNOWN) == []
    assert names_problems("Él sigue en racha, y tú también.", KNOWN) == []


@pytest.mark.parametrize("text", ["¿Una apuesta segura?", "Vale dinero.", "Primer premio para Priya.",
                                  "Un bono para ti.", "Portería a cero a la vista."])  # fmt: skip
def test_spanish_betting_money_and_other_game_terms_fail(text: str) -> None:
    assert tone_problems(text)


@pytest.mark.parametrize("text", ["Un disparo efectivo.", "El bote del balón engaña al portero."])
def test_ordinary_spanish_football_words_pass_the_tone_check(text: str) -> None:
    assert tone_problems(text) == []


def test_casual_spanish_counts_numbers_with_decimal_commas_as_one() -> None:
    assert style_problems("¡Gran parada, 5,5 IP para ti!", "casual", lang="es") == []


@pytest.mark.parametrize(("text", "problem"), [
    ("En el 34, una ocasión de 0.31 xG.", "'0.31' uses a decimal point: in Spanish write '0,31'"),
    ("Pierdes 1.5 IP con la tarjeta.", "'1.5' uses a decimal point: in Spanish write '1,5'"),
    ("Una ocasión de xG0.31 para Priya.", "'0.31' uses a decimal point: in Spanish write '0,31'"),
])  # fmt: skip
def test_spanish_text_with_a_decimal_point_is_sent_back_for_a_comma(text: str, problem: str) -> None:
    # The parser reads both separators, so "0.13 xG" with the right value reached a Spanish phone (#85)
    assert number_problems(text, EVIDENCE, CITED, lang="es") == [problem]


def test_spanish_text_with_a_decimal_comma_passes() -> None:
    assert number_problems("En el 34, una ocasión de 0,31 xG. Subes al 2.º puesto.", EVIDENCE, CITED, lang="es") == []


def test_english_text_keeps_its_decimal_point() -> None:
    assert number_problems("In the 34th minute, a 0.31 xG chance: down 1.5 IP.", EVIDENCE, CITED) == []


@pytest.mark.parametrize(("text", "ok"), [
    ("Para you, es un pequeño impulso.", False),
    ("Gran parada with tu portero.", False),
    ("Gran save para Priya.", False),  # #36
    ("Tu keeper sigue en racha.", False),
    ("Gran parada de Rhys Mallory para Saltmere Rovers: 8 IP.", True),  # names and IP are not English words
    ("¡Qué noche, Priya! Sigues en el nivel Wall.", True),
    ("Gol de Hollinsford United, y tú sigues arriba.", True),  # "United" is part of a name
    ("Your portero sigue en racha.", False),  # #45: an English pronoun is never stripped as a name
    ("You sigues arriba, Priya.", False),  # not even the viewer's own name, "You": in Spanish it is "tú"
    ("Rhys Mallory y Jonah Pryce siguen en el nivel Wall.", True),  # names and the tier stay out of it
])  # fmt: skip
def test_a_spanish_text_has_no_common_english_words(text: str, ok: bool) -> None:
    assert (language_problems(text, "es", KNOWN) == []) is ok


@pytest.mark.parametrize(("text", "ok"), [
    ("Big save from Rhys Mallory, and you stay top.", True),
    ("Your keeper is on fire for Saltmere Rovers.", True),
    ("Big parada from your keeper.", False),  # the English copy has Spanish left in it (#36)
    ("Your captain sigue en racha.", False),
])  # fmt: skip
def test_an_english_text_has_no_common_spanish_words(text: str, ok: bool) -> None:
    assert (language_problems(text, "en", KNOWN) == []) is ok


# A number written right after punctuation is read like any other (#107): before, "...99 IP" or "Hola,0.31" skipped
# the check, so a made-up number there reached a phone.
ONLY_DELTA = [e for e in EVIDENCE if e["id"] == "ev_delta"]


@pytest.mark.parametrize(("text", "lang", "flagged"), [
    ("Ganas 8 IP...99 IP.", "es", "'99 IP'"),
    ("Hello,0.31 xG.", "en", "'0.31 xG'"),
    ("Big save, 8,99 IP.", "en", "'99 IP'"),  # an English digit comma is no decimal: both numbers are checked
])  # fmt: skip
def test_a_number_right_after_punctuation_is_checked(text: str, lang: str, flagged: str) -> None:
    problems = number_problems(text, ONLY_DELTA, ["ev_delta"], lang=lang)
    assert any(p.startswith(flagged) for p in problems), problems


def test_a_spanish_decimal_point_right_after_a_comma_is_sent_back() -> None:
    assert number_problems("Hola,0.31 xG.", EVIDENCE, CITED, lang="es") == [
        "'0.31' uses a decimal point: in Spanish write '0,31'"]  # fmt: skip


@pytest.mark.parametrize(("text", "lang", "tokens"), [
    ("Una ocasión de 0,31 xG.", "es", ["0.31 xG"]),  # one number, read as a point
    ("A 1.5 IP swing.", "en", ["1.5 IP"]),
    ("Big save!8 IP.", "en", ["8 IP"]),
    ("Build v1.5 and abc1.5.", "en", []),
])  # fmt: skip
def test_each_number_is_read_once_and_whole(text: str, lang: str, tokens: list[str]) -> None:
    assert number_tokens(text, lang) == tokens


def test_a_cited_number_right_after_punctuation_passes() -> None:
    assert number_problems("Big save!8 IP.", EVIDENCE, CITED) == []


@pytest.mark.parametrize("text", ["Down...8 IP.", "Down: 8 IP.", "Down - 8 IP.", "Down, 8 IP.", "Down\u20148 IP.",
                                  "Pierdes: 8 IP."])  # fmt: skip
def test_a_sign_word_reaches_a_number_through_punctuation(text: str) -> None:
    lang = "es" if text.startswith("Pierdes") else "en"
    assert number_problems(text, ONLY_DELTA, ["ev_delta"], lang=lang)  # a loss claimed against a gain


def test_a_sign_word_never_crosses_a_sentence_break() -> None:
    assert number_problems("You lost. 8 IP for Priya.", ONLY_DELTA, ["ev_delta"]) == []
    assert number_problems("You lost.8 IP for Priya.", ONLY_DELTA, ["ev_delta"]) == []


def test_a_chain_of_numbers_is_refused_whole() -> None:
    assert number_problems("A 0.31.99 IP swing.", EVIDENCE, CITED) == [
        "'0.31.99' is not one number: write each number on its own"]  # fmt: skip


def test_spanish_reads_a_decimal_point_once_inside_a_chain() -> None:
    problems = number_problems("1.2.3.4", [], [], lang="es")  # not "3.4" as well
    assert [p for p in problems if "decimal point" in p] == ["'1.2' uses a decimal point: in Spanish write '1,2'"]


DASHES = re.compile("[\u2012\u2013\u2014\u2015]")


@pytest.mark.parametrize(
    ("text", "shown"),
    [
        ("Nice one \u2014 Tom's save gives you 3 IP", "Nice one, Tom's save gives you 3 IP"),  # #82 as seen
        ("Arjun waited longest \u2013 5 IP each.", "Arjun waited longest, 5 IP each."),  # spaced en dash
        ("Won 2\u20131, then 2 \u2013 1, then 2-1.", "Won 2-1, then 2, 1, then 2-1."),  # spaced: an aside (#122)
        ("Vas 2.º con 0,31 xG \u2014 gran parada.", "Vas 2.º con 0,31 xG, gran parada."),  # Spanish
        ("Priya \u2014la líder\u2014 suma 8 IP.", "Priya, la líder, suma 8 IP."),  # a Spanish aside
        ("You're at \u20138 IP now.", "You're at -8 IP now."),  # an en dash as a minus sign
        ("(\u20131.5 IP)", "(-1.5 IP)"),
        ("Big save\u2014your keeper again.", "Big save, your keeper again."),  # unspaced
        ("Great save \u2014", "Great save"),
        ("\u2014 great save.", "great save."),
        ("Great save \u2014.", "Great save."),
        ("One more, \u2014 two.", "One more, two."),
        ("Wow! \u2014 two.", "Wow! two."),
        ("Wall (\u2014 nearly) reached.", "Wall (nearly) reached."),
        ("Figure \u2012 dash and bar \u2015 too.", "Figure, dash and bar, too."),
    ],
)
def test_undash_writes_commas_and_hyphens_for_em_and_en_dashes(text: str, shown: str) -> None:
    assert undash(text) == shown
    assert not DASHES.search(undash(text))


@pytest.mark.parametrize(
    "text", ["Mid-table, -8 IP and \u22128 IP.", "Won 2-1.", "Sin cambios: 0,31 xG.", "", "Plain text, no dashes."]
)
def test_undash_leaves_text_without_dashes_byte_for_byte(text: str) -> None:
    assert undash(text) == text


@pytest.mark.parametrize(
    ("text", "lang"), [("Down \u2014 8 IP.", "en"), ("Down\u20148 IP.", "en"), ("Bajas \u2014 8 IP.", "es")]
)
def test_a_sign_word_before_a_dash_still_reads_as_a_loss_once_undashed(text: str, lang: str) -> None:
    loss, gain = [{"id": "ev", "value": -8, "unit": "ip"}], [{"id": "ev", "value": 8, "unit": "ip"}]
    assert number_problems(undash(text), loss, ["ev"], lang) == []
    assert number_problems(undash(text), gain, ["ev"], lang)


def test_an_en_dash_minus_reads_as_negative_once_undashed() -> None:
    gain: list[dict[str, Any]] = [{"id": "ev", "value": 8, "unit": "ip"}]
    assert number_problems(undash("You're at \u20138 IP."), gain, ["ev"])  # "-8" is not +8
    assert number_problems(undash("You're at \u20138 IP."), [gain[0] | {"value": -8}], ["ev"]) == []


def test_undash_keeps_a_score_as_the_number_checker_already_reads_it() -> None:
    cited: list[dict[str, Any]] = [
        {"id": "ev", "value": 1, "unit": "count"},
        {"id": "ev2", "value": 2, "unit": "count"},
    ]
    assert number_tokens(undash("Won 2\u20131.")) == number_tokens("Won 2-1.")
    assert number_problems(undash("Won 2\u20131."), cited, ["ev", "ev2"]) == []


EM, EN = "\N{EM DASH}", "\N{EN DASH}"


@pytest.mark.parametrize(
    ("text", "shown"),
    [
        (f"Delta:{EN}8 IP", "Delta:-8 IP"),  # a minus after punctuation keeps its sign (#122)
        (f"Saldo:{EM}8 IP", "Saldo:-8 IP"),
        (f"Up 3,{EN}8 IP", "Up 3,-8 IP"),
        (f"Ouch;{EN}8 IP", "Ouch;-8 IP"),
        (f"Ouch!{EN}8 IP", "Ouch!-8 IP"),
        (f"\u00bfY ahora?{EN}8 IP", "\u00bfY ahora?-8 IP"),
        (f'"{EN}8 IP"', '"-8 IP"'),
        (f"Down{EM}8 IP", "Down, 8 IP"),  # a letter before: an aside, and the sign word still reads the loss
    ],
)
def test_a_dash_touching_only_the_digit_after_it_is_a_minus(text: str, shown: str) -> None:
    assert undash(text) == shown


@pytest.mark.parametrize("text", [f"Delta:{EN}8 IP.", f"Saldo:{EM}8 IP.", f'Ouch! "{EN}8 IP"'])
def test_a_minus_after_punctuation_is_read_as_negative(text: str) -> None:
    lang = "es" if text.startswith("Saldo") else "en"
    assert number_problems(undash(text), [{"id": "ev", "value": -8, "unit": "ip"}], ["ev"], lang) == []
    assert number_problems(undash(text), [{"id": "ev", "value": 8, "unit": "ip"}], ["ev"], lang)


@pytest.mark.parametrize(
    ("text", "shown"),
    [
        (f"231 {EM} 8 clear of Sam.", "231, 8 clear of Sam."),  # an em dash between numbers is never a score
        (f"231{EM}8 clear.", "231, 8 clear."),
        (f"Quedan 231 {EM} 8 m\u00e1s que Sam.", "Quedan 231, 8 m\u00e1s que Sam."),
        (f"Won 2 {EN} 1.", "Won 2, 1."),  # spaced: an aside
        (f"Won 2{EN}1.", "Won 2-1."),
        ("Won 2\N{FIGURE DASH}1.", "Won 2-1."),
        (f"From 34'{EN}40'.", "From 34'-40'."),  # minutes
        (f"Del 34\N{PRIME}{EN}40\N{PRIME}.", "Del 34\N{PRIME}-40\N{PRIME}."),
    ],
)
def test_only_an_unspaced_short_dash_between_numbers_is_a_range(text: str, shown: str) -> None:
    assert undash(text) == shown


@pytest.mark.parametrize(
    ("text", "shown"),
    [
        (f"Wow!{EM}two.", "Wow! two."),  # unspaced
        (f"Wait. {EM} two.", "Wait. two."),  # spaced
        (f"Wait.{EM} two.", "Wait. two."),
        (f"Wait\N{HORIZONTAL ELLIPSIS}{EM}two.", "Wait\N{HORIZONTAL ELLIPSIS} two."),
        (f"Look: {EN} two; {EM}three? {EM}four.", "Look: two; three? four."),
        (f"\u00a1Vaya!{EM}dos.", "\u00a1Vaya! dos."),  # Spanish
        (f"Ojo: {EM} dos.", "Ojo: dos."),
        (f"Top three:\n{EM} Priya, 231 IP\n{EM} Sam, 220 IP", "Top three:\nPriya, 231 IP\nSam, 220 IP"),  # line start
        (f"Al descanso:\n\n{EM} Priya, 231 IP", "Al descanso:\n\nPriya, 231 IP"),
        (f"{EM} Priya first\n {EM} Sam next", "Priya first\nSam next"),
    ],
)
def test_a_dash_after_sentence_punctuation_or_at_a_line_start_leaves_one_space_or_the_line_break(
    text: str, shown: str
) -> None:
    assert undash(text) == shown


@pytest.mark.parametrize(
    ("text", "shown"),
    [
        (f"Big save {EM} {EM} again.", "Big save, again."),  # a run of dashes is one aside
        (f"Big save {EM}{EN} again.", "Big save, again."),
        (f"\N{LEFT-POINTING DOUBLE ANGLE QUOTATION MARK}{EM}Vamos{EM}\N{RIGHT-POINTING DOUBLE ANGLE QUOTATION MARK}",
         "\N{LEFT-POINTING DOUBLE ANGLE QUOTATION MARK}Vamos\N{RIGHT-POINTING DOUBLE ANGLE QUOTATION MARK}"),
        (f"\N{LEFT DOUBLE QUOTATION MARK}Great save {EM}\N{RIGHT DOUBLE QUOTATION MARK}",
         "\N{LEFT DOUBLE QUOTATION MARK}Great save\N{RIGHT DOUBLE QUOTATION MARK}"),
        (f'"Great save {EM}"', '"Great save"'),
        (f"[{EM} note] and [note {EM}]", "[note] and [note]"),
        (f"Mid{EN}table again.", "Mid-table again."),  # an unspaced en dash between letters is a hyphen
        (f"Media{EN}tabla otra vez.", "Media-tabla otra vez."),
        (f"Big save{EM}your keeper.", "Big save, your keeper."),  # an em dash between letters is not
    ],
)  # fmt: skip
def test_runs_brackets_quotes_and_hyphens(text: str, shown: str) -> None:
    assert undash(text) == shown
    assert not DASHES.search(undash(text))


def test_undash_treats_a_non_breaking_space_like_a_space() -> None:
    assert undash("x\u00a0\u2014\u00a0y") == "x, y"
    assert undash("Wait.\u00a0\u2014\u00a0two.") == "Wait. two."


# #138: the group screen's line is read by the whole room


@pytest.mark.parametrize(
    "text",
    [
        "Priya climbs from 3 to 2 in the league.",  # two numbers for one fact
        "Priya's captain keeper stops a 0.31 xG chance.",  # captain and xG, one fact
        "Only Priya owns Rhys Mallory, +8 IP.",
        "Youthful Priya teams up with a 0.31 xG save.",  # "you" and "te" inside other words
        "Priya's captain actually delivers, +8 IP.",  # "actually" is not expected against actual
        "Showtime for Priya: +8 IP.",  # an order is a whole word at the start
    ],
)
def test_a_line_for_the_room_in_the_third_person_with_one_fact_passes(text: str) -> None:
    assert room_problems(text, "Priya") == []


@pytest.mark.parametrize(
    ("text", "problem"),
    [
        ("Priya's keeper, and you're up in the league.", "'you'"),
        ("Huge for your keeper, Priya.", "'your'"),
        ("Priya: tu portero, 8 IP.", "'tu'"),  # Spanish second person
        ("Priya, te toca.", "'te'"),
        ("Show my moment.", "'my'"),  # the agent speaking
        ("We need this one for Priya.", "'We'"),
        ("Priya's captain adds 8 on top of 16, from 3 to 2.", "at most 2 numbers"),
        ("Priya's captain returned 5 actual IP against 16 expected.", "not both"),  # two numbers, two facts
        ("Priya's armband paid off, more than expected.", "not both"),
        ("Show Sam this, the goal pushes the league move up.", "not an order"),  # seen in the live smoke
        ("Put Priya on screen: +8 IP.", "not an order"),
        ("Please show Priya this one.", "not an order"),
        ("Priya, show this one.", "not an order"),
        ("Priya, give Sam this one.", "not an order"),  # a name between the order and its object
        ("The host should show Priya this one.", "not an order"),
        ("A big moment for Sam.", "name Priya"),  # under Priya's name, about someone else
    ],
)
def test_a_line_for_the_room_that_addresses_someone_or_stacks_facts_fails(text: str, problem: str) -> None:
    assert any(problem in p for p in room_problems(text, "Priya")), room_problems(text, "Priya")


def test_the_judges_own_manager_is_you_on_the_shared_screen_but_never_i() -> None:
    assert room_problems("Your keeper again, you climb to 2.", "You") == []  # the viewer, as host reasons say
    assert room_problems("My keeper again.", "You") != []
    # seen in the live smoke: the model wrote the viewer as a third person
    assert any("viewer" in p for p in room_problems("You has jumped from 5 to 3.", "You"))
    assert any("viewer" in p for p in room_problems("You\u2019s captain watch matters here.", "You"))
    assert any("name You" in p for p in room_problems("Tom Ashcombe saves again.", "You"))
    assert any("viewer" in p for p in room_problems("You climbs to 2.", "You"))  # any third-person verb
    assert room_problems("You always climb when your keeper saves.", "You") == []
    assert any("viewer" in p for p in room_problems("You was on top already.", "You"))
    # smoke-es6: the judge's own pitch ordered the host
    assert any("not an order" in p for p in room_problems("You should show this for You, 4 IP.", "You"))


# #141


@pytest.mark.parametrize(
    "text",
    ["Priya's keeper saves. Show this one.", "Priya's keeper saves; show it.", "Look at Priya's keeper, +8 IP!",
     "Watch Priya climb", "Don't miss Priya's save",
     "Don\u2019t miss Priya's save", "A save for Priya, please show it."],
)  # fmt: skip
def test_an_order_anywhere_a_sentence_starts_is_refused(text: str) -> None:
    assert any("not an order" in p for p in room_problems(text, "Priya")), room_problems(text, "Priya")


@pytest.mark.parametrize(
    "text",
    ["Priya's keeper, pick of the week, saves again.", "Big swing, pick of the round for Priya.",
     "Priya should pick a new captain after that.", "The red card means Priya must look elsewhere for points.",
     "Priya's US-born keeper saves.", "Ian I. Moss saves for Priya.", "a big moment for priya."],
)  # fmt: skip
def test_statements_that_look_like_orders_or_pronouns_pass(text: str) -> None:
    assert room_problems(text, "Priya") == []


@pytest.mark.parametrize("name", ["J.R.", "Sam!", "Ana-Lu"])
def test_a_typed_league_name_with_punctuation_is_found(name: str) -> None:
    assert room_problems(f"A big moment for {name}", name) == []
    assert any("name " in p for p in room_problems("A big moment for Priya.", name))


@pytest.mark.parametrize(
    ("text", "delta", "wrong"),
    [
        # smoke-en3: Priya's own player's red card cost her 3 IP
        ("Priya's 1-owner differential Lasse Vikholm red card is exactly the kind of swing that can protect the lead.",
         -3, True),
        ("Lasse Vikholm's red card costs Priya 3 IP.", -3, False),
        ("A blow for Priya: Lasse Vikholm sent off.", -3, False),
        ("Priya drops to 2 after the goal.", -1, False),
        ("-3 IP for Priya from this moment (red card).", -3, False),
        ("Priya passes Sam, who drops to 3.", 4, False),  # a gain may still drop a rival
        ("A setback for Priya, +4 IP.", 4, True),
        ("Priya banks 4 IP from the save.", 4, False),
        ("Priya's keeper saves again.", 0, False),  # no swing, no direction to check
        ("Priya's lead shrinks after a red card.", -3, False),
        ("Priya slides to 4th after the own goal.", -2, False),
        ("Priya gives away 3 IP on the red card.", -3, False),
        ("Priya's clean sheet is gone.", -2, False),
        ("Priya concedes 3 IP on the red card.", -3, False),
    ],
)  # fmt: skip
def test_the_line_says_which_way_the_swing_went(text: str, delta: float, wrong: bool) -> None:
    found = [p for p in room_problems(text, "Priya", delta) if "loss" in p]
    assert bool(found) is wrong, found


@pytest.mark.parametrize("code", sorted({*RULES["rules"], "no_change"} - {"no_change"}))
@pytest.mark.parametrize("delta", [8, -1.5])
@pytest.mark.parametrize("name", ["Priya", "You", "J.R."])
def test_codes_own_line_passes_the_room_check_for_every_rule(code: str, delta: float, name: str) -> None:
    stake = {"reason_code": code, "evidence": [{"id": "ev_m_03_mgr_2_delta", "value": delta}]}
    assert room_problems(room_line(stake, {"display_name": name}), name, delta) == []
