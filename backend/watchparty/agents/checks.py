"""Code checks on everything a model writes before any viewer sees it (#10).

numbers:  every number must be a cited evidence value, with its sign and unit, rounded by at most 5 %.
names:    no proper name from outside this match, so no real players or clubs (rules 4.4, 4.7).
tone:     no betting, money or prize language; the league is free to play (rule 4.4).
language: no common words of the other language, so English and Spanish are never mixed (#36).

Each returns a list of problems; empty means pass. They are deliberately strict: a false alarm costs a
retry or a plain-stat fallback, a miss puts an unsupported claim on screen.

undash is not a check but a fix: no text on screen carries an em or en dash (#82), and a retry for one would
cost a model call, so every answer is rewritten before it is checked.
"""

import math
import re
from collections.abc import Sequence
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

# Number words that state a count, per language: "once" is eleven in Spanish but not in English. One, first
# and second are left out ("one more save", "second half", "uno más", "segundo tiempo"): phrases, not claims.
_WORDS = {
    "en": ["two", "three", "four", "five", "six", "seven", "eight", "nine", "ten", "eleven", "twelve", "thirteen",
           "fourteen", "fifteen", "sixteen", "seventeen", "eighteen", "nineteen", "twenty"],
    "es": ["dos", "tres", "cuatro", "cinco", "seis", "siete", "ocho", "nueve", "diez", "once", "doce", "trece",
           "catorce", "quince", "dieciséis", "diecisiete", "dieciocho", "diecinueve", "veinte"],
}  # fmt: skip
_ORDINALS = {  # from third
    "en": [["third"], ["fourth"], ["fifth"], ["sixth"], ["seventh"], ["eighth"], ["ninth"], ["tenth"], ["eleventh"],
           ["twelfth"], ["thirteenth"], ["fourteenth"], ["fifteenth"], ["sixteenth"], ["seventeenth"],
           ["eighteenth"], ["nineteenth"], ["twentieth"]],
    "es": [["tercer", "tercero", "tercera"], ["cuarto", "cuarta"], ["quinto", "quinta"], ["sexto", "sexta"],
           ["séptimo", "séptima"], ["octavo", "octava"], ["noveno", "novena"], ["décimo", "décima"]],
}  # fmt: skip
WORD_VALUES = {
    lang: {w: n for n, w in enumerate(_WORDS[lang], start=2)}
    | {w: n for n, forms in enumerate(_ORDINALS[lang], start=3) for w in forms}
    for lang in _WORDS
}
WORD_VALUES["en"] |= {"twice": 2, "thrice": 3}
DECIMAL_COMMA = {"es"}  # 0,31 is 0.31
LANGUAGES = {"en": "English", "es": "Spanish"}  # the second language is Spanish (#16)
# Spanish ordinals agree in gender; agreeing with the place ("el 2.º puesto") never guesses a manager's.
GENDER_NEUTRAL = {"es": " Write places as 'en el 2.º puesto', never 'sigues 2.ª' or '1.º' alone, so nothing"
                        " depends on the manager's gender."}  # fmt: skip
# EVIDENCE is JSON, so it shows 0.31; without this the model copies the point and the comma check sends it back.
DECIMAL_STYLE = {"es": " Write every decimal with a comma, as 0,31, even where EVIDENCE shows 0.31."}
# A number's unit, written after it or before it, must be its evidence item's unit: neither "a 3 xG chance" nor
# "xG 3" cites a count, and "minuto 8" is never 8 IP (#23, #45).
_UNITS = {"ip": r"IP|points?|pts|puntos?", "xg": r"xG", "ratio": r"%", "minute": r"minutes?|minutos?|mins?\b|'|\u2032"}
_UNIT_OF = [(re.compile(rf"(?:{pattern})$", re.IGNORECASE), unit) for unit, pattern in _UNITS.items()]
_UNITS_BEFORE = r"xG|minutes?|minutos?|mins?"  # the ones written ahead of a number: "xG 0.31", "minute 34"
# Words that give an IP number its sign: "lost 8 IP" is -8, "gained 8 IP" is +8 (#23).
_LOSS = {"en": r"lose|loses|lost|losing|down|drop|drops|dropped|minus|shed|sheds",
         "es": r"pierdes|pierde|pierden|perdiste|perdió|bajas|baja|bajaste|bajó|menos|restas|resta|restó"}  # fmt: skip
_GAIN = {"en": r"gain|gains|gained|earn|earns|earned|up|plus|add|adds|added|bank|banks|banked",
         "es": r"ganas|gana|ganaste|ganó|sumas|suma|sumaste|sumó|subes|sube"}  # fmt: skip
_SIGN_GAP = r"\s*(?:[,:;\u2026\u2013\u2014-]|\.{2,})+\s*"  # "Down: 8", "Down...8"; never one full stop (#107)
_SIGN_WORD = {lang: tuple(re.compile(rf"\b(?:{words[lang]})(?:\s+(?:by\s+|en\s+)?|{_SIGN_GAP})$", re.IGNORECASE)
                          for words in (_LOSS, _GAIN)) for lang in _LOSS}  # fmt: skip
_PATTERNS = {
    lang: re.compile(
        r"(?<!\w)(?<!\d\.)(?:(?P<before>" + _UNITS_BEFORE + r")\s?)?"
        r"(?P<sign>[+\-\u2212](?=\d))?(?P<number>\d+(?:\.\d+)?|\b(?:" + "|".join(words) + r")\b)"
        r"(?:st|nd|rd|th|\.?[ºª])?(?P<unit>\s?(?:" + "|".join(_UNITS.values()) + r"))?",
        re.IGNORECASE,
    )
    for lang, words in WORD_VALUES.items()
}
# Em and en dashes, and the figure dash and bar a model can write for them (#82). Never "-", and never the minus
# sign U+2212: those carry a number's sign.
_DASHES = r"\N{FIGURE DASH}\N{EN DASH}\N{EM DASH}\N{HORIZONTAL BAR}"
_SHORT = r"\N{FIGURE DASH}\N{EN DASH}"  # the ones a model writes for a hyphen: in a range, a score or a word
_D = rf"[{_DASHES}]"
# Unspaced between digits: "2-1". An em dash or a spaced one between numbers is an aside ("231, 8 clear"), not a
# score. After a minute mark ("34'-40'") the minus rule below gives the same hyphen.
_RANGE = re.compile(rf"(?<=\d)[{_SHORT}](?=\d)")
_HYPHEN = re.compile(rf"(?<=[^\W\d_])[{_SHORT}](?=[^\W\d_])")  # unspaced between letters: "Mid-table"
_MINUS = re.compile(rf"(?<!\w){_D}(?=\d)")  # no letter or digit before, a digit after: a minus, "Delta:-8"
# After a sentence's own punctuation, at the start of a line, or at the start: the dash and its spaces go. A
# space stays after punctuation, and a line break stays where the dash began a line ("Top three:\nPriya").
_SP = r"[^\S\n]"  # a space or a non-breaking space, never a line break
_STOP = re.compile(rf"(?:(?<=[.!?:;\N{{HORIZONTAL ELLIPSIS}}])|^)(?P<pre>\s*)(?:{_D}{_SP}*)+", re.MULTILINE)
_DROPPED = re.compile(  # nothing to join: at a line's end, beside a bracket, quote or other punctuation
    rf"{_SP}*(?:{_D}{_SP}*)+(?=$|[.,;:!?\N{{HORIZONTAL ELLIPSIS}}\)\]\"\N{{RIGHT DOUBLE QUOTATION MARK}}"
    rf"\N{{RIGHT-POINTING DOUBLE ANGLE QUOTATION MARK}}])"
    rf"|(?<=,){_SP}*{_D}(?:{_SP}*{_D})*"  # after a comma, keeping the space that follows
    rf"|(?<=[(\[\N{{LEFT DOUBLE QUOTATION MARK}}\N{{LEFT-POINTING DOUBLE ANGLE QUOTATION MARK}}]){_SP}*(?:{_D}{_SP}*)+",
    re.MULTILINE,
)
_ASIDE = re.compile(rf"{_SP}*{_D}(?:{_SP}*{_D})*{_SP}*")  # between words, a run of them, both ends of an aside


def _after_stop(m: re.Match[str]) -> str:
    if "\n" in m["pre"]:
        return m["pre"].replace(" ", "").replace("\t", "")  # the line breaks, without the spaces around them
    at_line_start = m.start() == 0 or m.string[m.start() - 1] == "\n"
    return "" if at_line_start else " "


def undash(text: str) -> str:
    """The text with no em or en dash, and every number's value and sign as written: "Down", an em dash and
    "8 IP" become "Down, 8 IP", which still reads as a loss (#107)."""
    text = _RANGE.sub("-", text)
    text = _HYPHEN.sub("-", text)
    text = _MINUS.sub("-", text)
    text = _STOP.sub(_after_stop, text)
    text = _DROPPED.sub("", text)
    return _ASIDE.sub(", ", text)


ROUNDING = 0.05  # how far a number may round its value: 283.5 IP may be "284", an xG of 0.62 is never "1" (#23)
# A decimal point between digits, which a decimal-comma language never writes: "0.13 xG" in Spanish (#85).
# "2.º" has no digit after its point, so places pass. "xG0.31" is read with its unit, so it is checked too.
# A number is read anywhere except inside a word ("v1.5") or after a decimal point, so "8 IP...99 IP",
# "Hola,0.31" and an English "8,99" are all checked (#107). _PATTERNS runs after Spanish digit commas
# become points; _DECIMAL_POINT reads the raw text. A chain such as "0.31.99" is refused as a whole.
_CHAINED = re.compile(r"(?<!\w)(?<!\d[.,])\d+(?:[.,]\d+){2,}")  # "0.31.99" hides its second number
_DECIMAL_POINT = re.compile(r"(?:(?<!\w)(?<!\d[.,])|(?<=xG))\d+\.\d+", re.IGNORECASE)


def _numbers(text: str, lang: str) -> tuple[str, list[re.Match[str]]]:
    if lang in DECIMAL_COMMA:
        text = re.sub(r"(?<=\d),(?=\d)", ".", text)
    return text, list(_PATTERNS.get(lang, _PATTERNS["en"]).finditer(text))


def number_tokens(text: str, lang: str = "en") -> list[str]:
    """The numbers a text states, with Spanish decimal commas read as points: "0,31 xG" is "0.31 xG"."""
    return [m.group().strip() for m in _numbers(text, lang)[1]]


def _matches(m: re.Match[str], text: str, item: dict[str, Any], lang: str) -> bool:
    """Whether one number in the text states this evidence item: its sign, its unit and its value."""
    value = item["value"]
    written = {next(u for pattern, u in _UNIT_OF if pattern.match(w.strip())) for w in (m["before"], m["unit"]) if w}
    # A written unit must be the item's own: an item with no unit (a pass's difficulty) is never "0.72 xG" (#87)
    if any(item.get("unit") != unit for unit in written):
        return False
    unit = next(iter(written), None) or item.get("unit")  # what the number states, written or as cited
    loss, gain = _SIGN_WORD.get(lang, _SIGN_WORD["en"])
    before = text[: m.start()]
    # Loss and gain words give an IP number its sign, whether or not the text writes "IP" (#45)
    negative = m["sign"] in ("-", "\u2212") or (unit == "ip" and loss.search(before) is not None)
    positive = m["sign"] == "+" or (unit == "ip" and gain.search(before) is not None)
    if (negative and value >= 0) or (positive and value < 0):
        return False
    words = WORD_VALUES.get(lang, WORD_VALUES["en"])
    if m["number"].lower() in words:
        return math.isclose(words[m["number"].lower()], abs(value))
    shown, places = float(m["number"]), len(m["number"].partition(".")[2])
    meant = abs(value) * 100 if unit == "ratio" else abs(value)
    rounded = float(Decimal(repr(meant)).quantize(Decimal(1).scaleb(-places), ROUND_HALF_UP))  # 282.5 is 283 (#49)
    return math.isclose(rounded, shown, abs_tol=1e-9) and abs(shown - meant) <= ROUNDING * meant + 1e-9


def number_problems(text: str, evidence: list[dict[str, Any]], cited: list[str], lang: str = "en") -> list[str]:
    known = {e["id"]: e for e in evidence}
    problems = [f"cites unknown evidence {i}" for i in cited if i not in known]
    items = [known[i] for i in cited if i in known]
    if lang in DECIMAL_COMMA:  # the parser reads both separators; the value may be right and still written wrong (#85)
        problems += [f"'{d}' uses a decimal point: in {LANGUAGES.get(lang, lang)} write '{d.replace('.', ',')}'"
                     for d in _DECIMAL_POINT.findall(text)]  # fmt: skip
    problems += [f"'{c}' is not one number: write each number on its own" for c in _CHAINED.findall(text)]
    text, numbers = _numbers(text, lang)
    for m in numbers:
        if not any(_matches(m, text, item, lang) for item in items):
            problems.append(f"'{m.group().strip()}' is not a value of any evidence you cited, with its sign and unit")
    return problems


PRONOUNS = {"I", "You", "Your"}
ALWAYS_ALLOWED = PRONOUNS | {"Wall", "OK"}  # capitalised but never a name; "Wall" is our tier name
# Ordinary words a sentence may start with, including the imperatives a pitch to the host opens with ("Show",
# "Put", "Muestra"). Any other capitalised word must be a name from this match, so "Vasquith scored again." fails
# like "He played like Vasquith." does (#23). A miss costs a retry, not a wrong name. Grown from live evals.
_STARTERS_EN = """
A About Above Absolutely Actually After Again Against Ah Ahead All Almost Already Also Although Always Amazing An
And Another Any Anything Around As At Back Bad Barely Basically Because Before Behind Below Besides Best Better
Between Beyond Big Bigger Biggest Both Brilliant Bring Busy But By Calm Can't Captain Card Cheers Choose Classic
Clean Clear Clearly Close Clutch Cracking Credit Crucial Day Despite Didn't Don't Down Draw Each Early Easy Eight
Enough Even Every Everyone Everything Fair Fantastic Feature Few Finally Fine First Five Flash For Fortunately Four
Fresh From Full Fulltime Game Give Gives Go Goal Good Great Half Halftime Happily Happy Hat He Hence Her Here Hers
Highlight His Hold However Huge If Important In Incredible Indeed Instant Instead Into Isn't It Its Just Keep Keeper
Key Kickoff Late Lead Leader Leading Let Level Loads Look Loss Lots Lovely Luckily Lucky Make Massive Maybe
Meanwhile Mid Mind Minute Moment More Most Much My Near Nearly Neat Need New Next Nice Nine No Nobody Not Nothing
Now Of Off Oh On One Only Oof Or Otherwise Ouch Our Over Overall Perhaps Phew Pick Please Plenty Plus Points
Probably Proof Pure Put Quiet Quite Rare Really Red Relief Remember Right Sadly Safe Same Save Saved Scare Second
Send Seven Sharp She Shock Shot Show Simply Since Six Small Smart So Solid Some Somehow Someone Something Sorry
Spotlight Stay Steady Still Strong Suddenly Superb Sure Sweet Swing Take Ten Thankfully Thanks That That'll The
Their Then There Therefore These They This Those Though Three Through Thus Tidy To Today Tonight Too Top Tough
Toward Towards Two Ugh Under Unfortunately Unlucky Up Use Very Wasn't Watch We Well What When Where While Who Why
Win With Without Won't Worse Worth Wow Yellow Yes Yet
"""
_STARTERS_ES = """
Abajo Además Ahora Ahí Al Algo Alguien Alivio Anda Antes Apenas Aprieta Arriba Así Atención Aun Aunque Ay Ayuda Aún
Baja Bajas Bastante Bien Brutal Buen Buena Bueno Cada Cae Calma Casi Cerca Claro Clave Como Con Contra Crucial
Cuando Cuidado Cuál Cuánta Cuánto Cómo Dale De Debajo Deja Del Delante Demasiado Derrota Descanso Desde Después
Destaca Detrás Disparo Doloroso Dos Duele Dura Durante Duro Dónde El Elige Ella Ellos Empate En Encima Enorme Enseña
Entonces Entre Eres Es Esa Ese Eso Espectacular Esta Este Esto Está Estás Excelente Favor Fenomenal Final Fue Gana
Ganas Genial Gol Golazo Golpe Gracias Gran Ha Hacia Han Has Hasta Hay Haz Hombre Hoy Importante Impresionante
Incluso Increíble La Las Le Les Llega Lleva Llevas Lo Los Luego Lástima Líder Madre Mal Mala Mantiene Mantienes
Medio Mejor Menos Menuda Menudo Merece Mi Mientras Minuto Mira Muestra Muy Más Nada Nadie Necesitas Ni Nueva Nuevo
Nunca Ocasión Ojo Otra Otro Oye Para Parada Paradón Pena Peor Pequeña Pequeño Perdón Pero Pierde Pierdes Poco Pon
Por Porque Primer Primera Primero Protege Pues Que Queda Quedas Quizá Quizás Qué Refuerza Remontada Saca Se Segunda
Segundo Según Si Siempre Sigue Sigues Sin Sobre Solo Su Sube Subes Suma Sumas Sus Susto Sí Tal También Tampoco Tan
Tanto Tarjeta Te Tiene Tienes Toca Todas Todavía Todo Todos Toma Total Trae Tranquila Tranquilidad Tranquilo Tras
Tremenda Tremendo Tres Tu Tus Tú Uf Un Una Usa Uy Va Vale Vas Vaya Venga Victoria Y Ya Él
"""
STARTERS = set(_STARTERS_EN.split()) | set(_STARTERS_ES.split())
_SENTENCES = re.compile(r"(?<=[.!?:;])\s+|\n+")
# Letters in any script; "Don't" stays whole, and each part of "Mid-table" is checked on its own.
_WORD = re.compile(r"[^\W\d_]+(?:['\u2019-][^\W\d_]+)*")


_CONTRACTION = re.compile(r"'(?:s|re|ve|ll|d|m)$")


def _name_parts(known: Sequence[str]) -> set[str]:
    return ALWAYS_ALLOWED | {part for name in known for part in re.split(r"[\s-]+", name)}


def names_problems(text: str, known: list[str]) -> list[str]:
    """Capitalised words must belong to a known name (or be an acronym of up to three letters, such as IP).

    A sentence may also start with an ordinary word from STARTERS; there capitals are ordinary.
    """
    allowed = _name_parts(known)
    # A league name typed in lower case ("jo", #178) is still that name when a sentence capitalises it
    allowed |= {part[:1].upper() + part[1:] for part in allowed}
    problems = []
    for sentence in _SENTENCES.split(text):
        for i, word in enumerate(_WORD.findall(sentence)):
            word = _CONTRACTION.sub("", word.replace("\u2019", "'"))  # "Priya's", "You're": the name or word
            for part in word.split("-"):
                if part.isupper() and len(part) > 3:  # "ZELKOV" is a name in capitals; "GOAL!" a shout (#49)
                    part = part.title()
                if not part[:1].isupper() or part.isupper() or part in allowed:  # IP, GK: acronyms
                    continue
                if i > 0:
                    problems.append(f"'{part}' is not a club, player or manager in this match")
                elif part not in STARTERS:
                    problems.append(f"'{part}' starts a sentence but is not a name in NAMES: if it is an ordinary"
                                    " word, start the sentence another way")  # fmt: skip
    return problems


# Betting, money and prizes (rule 4.4), and the other fantasy game's own terms, which we never borrow.
# Spanish too: apuesta, dinero, premio, bono, portería a cero. Not "bote" or "efectivo", which in football
# Spanish are also a ball's bounce and "effective".
_TONE = re.compile(r"\b(?:bets?|betting|wager\w*|gambl\w*|bookies?|bookmakers?|money|cash|prizes?|jackpot"
                   r"|bonus(?:es)?|clean[ -]sheets?"
                   r"|apuest\w*|apost\w*|dinero|premios?|bonos?|bonificaci[oó]n\w*|porter[ií]a a cero)\b|[£$€]",
                   re.IGNORECASE)  # fmt: skip


# Everyday words of one language that never belong in a text written in the other: the model once wrote
# "Para you", and "Gran save" reads as Spanish to a check that knows six words (#36). Names are removed first,
# so "Hollinsford United" never trips it. Words both languages share ("no", "final", "gol", "top") are left out.
_FOREIGN = {
    "es": r"you|your|the|and|with|for|of|this|that|his|her|their|our|it|its|is|are|was|were|be|from|but|just|after"
          r"|still|now|what|big|great|nice|save|saves|saved|goal|goals|shot|shots|keeper|points|lead|league|chance"
          r"|half|time|match|player|players|captain|win|won|lost|up|down|more|into|over|at|on|in|by|to|an|only|team",
    "en": r"el|la|los|las|del|que|para|por|tu|tus|su|sus|una|unos|unas|es|está|están|muy|pero|como|más|ya|sigue"
          r"|sigues|parada|puntos|partido|jugada|ocasión|descanso|puesto|portero|racha|también|tú|este|esta|momento"
          r"|merece|pantalla",
}  # fmt: skip
_FOREIGN_WORDS = {lang: re.compile(rf"\b(?:{words})\b", re.IGNORECASE) for lang, words in _FOREIGN.items()}


def language_problems(text: str, lang: str, known: Sequence[str] = ()) -> list[str]:
    """Common words of the other language: a sign the model mixed English and Spanish, or did not translate."""
    # Names and product terms come out first, never a pronoun: not even "You", the viewer's own name (#45)
    names = {part for name in known for part in re.split(r"[\s-]+", name)} - PRONOUNS | {"Wall", "OK"}
    for part in sorted(names, key=len, reverse=True):
        text = re.sub(rf"\b{re.escape(part)}\b", " ", text)
    other = "Spanish" if lang == "en" else "English"
    return [f"'{m}' is {other}: write it all in {LANGUAGES.get(lang, lang)}"
            for m in _FOREIGN_WORDS.get(lang, _FOREIGN_WORDS["es"]).findall(text)]  # fmt: skip


def tone_problems(text: str) -> list[str]:
    return [f"'{m}' is betting, money or prize language, or another game's term" for m in _TONE.findall(text)]


# Second person in English and Spanish: on the shared screen "you" is everyone in the room. First person: the
# agent speaking ("Show my moment") is no one the room knows (#138). "I" only as a word, not an initial ("Ian I.
# Moss"), and "us" only in lower case, not "US-born".
_SECOND_PERSON = re.compile(
    r"\b(?:you|your|yours|yourself|yourselves|tú|tu|tus|te|ti|contigo|usted|ustedes)\b", re.IGNORECASE
)
_FIRST_PERSON = re.compile(r"\bI\b(?!\.)|\bus\b|\b(?i:me|my|mine|myself|we|our|ours)\b")
ROOM_MAX_NUMBERS = 2  # one fact a line: "from 3 to 2" needs two, a captain swing stacked on xG needs four (#138)
# A captain's extra points and expected-against-actual are two facts, even written with two numbers (#138)
_CAPTAIN = re.compile(r"\b(?:captain\w*|armband)\b", re.IGNORECASE)
_EXPECTED = re.compile(r"\b(?:expected|actual)\b", re.IGNORECASE)
# An order to the host or the room ("Show Sam this", "Watch Priya climb", "Don't miss it") reads on the shared
# screen as one more voice speaking to the room: at the start of any sentence, after "please", after "you should"
# or "the host must" (advice about a manager, "Priya should pick a new captain", is a statement), or after
# a comma with its object, perhaps past a name ("Priya, show this", "Priya, give Sam this one"), so "Priya's keeper,
# pick of the week" stays a statement.
_ORDER_VERBS = (r"show|put|give|pick|choose|let|send|bring|spotlight|feature|highlight|look|watch"
                r"|don['\u2019]t\s+miss|do\s+not\s+miss")  # fmt: skip
_ORDER = re.compile(
    rf"(?:^|[.;!?]\s+)\W*(?:please\s+)?(?:{_ORDER_VERBS})\b"
    rf"|\bplease\s+(?:{_ORDER_VERBS})\b"
    rf"|\b(?:you|the\s+host|host|everyone|we)\s+(?:should|must|needs?\s+to|ha(?:ve|s)\s+to)\s+(?:{_ORDER_VERBS})\b"
    rf"|,\s*(?:{_ORDER_VERBS})\s+(?:[\w.'\u2019]+\s+)?(?:this|that|it|them|him|her)\b",
    re.IGNORECASE,
)
# The viewer is "you", never a third-person "You": the live smoke had "You has jumped" and "You's captain"
_YOU_AS_HE = re.compile(r"\bYou(?:['\u2019]s\b|\s+(?!(?:always|perhaps|sometimes)\b)\w+s\b)")
# Which way the swing went (#138): "a swing that can protect the lead" over a -3 IP red card passed every check.
# A loss is said with one of these; a gain must not use the unambiguous ones (a gain can still "drop" a rival).
_LOSS_WORDS = re.compile(r"(?<![\w.])-\d|\b(?:costs?|costing|lose|loses|lost|losing|drops?|dropped|dropping|blow"
                         r"|down|hurts?|hurting|setback|hit|hits|stings?|stinging|slips?|slipped|falls?|fell|minus"
                         r"|slides?|slid|sliding|shrinks?|shrank|shrinking|gone|gives?\s+away|concedes?|conceded"
                         r"|conceding)\b",
                         re.IGNORECASE)  # fmt: skip
_LOSS_ONLY_WORDS = re.compile(r"(?<![\w.])-\d|\b(?:costs?|costing|blow|hurts?|setback|minus)\b", re.IGNORECASE)


def _names(about: str) -> re.Pattern[str]:
    """The manager's name as written, in any case; "J.R." and "Sam!" too, since typed league names allow them."""
    if about == "You":
        return re.compile(r"\b(?:you|your)\b", re.IGNORECASE)
    return re.compile(rf"(?<!\w){re.escape(about.rstrip('.!?'))}(?!\w)", re.IGNORECASE)


def room_problems(text: str, about: str, delta: float = 0) -> list[str]:
    """A line the whole room reads on the group screen, about the manager named `about` whose swing this moment
    is `delta` IP (#138): third person, one fact, the right way round. "You" is the judge's own manager, the viewer
    at the screen, and is addressed as "you" there, as the host's reasons are."""
    third = f"write about {about} by name, in the third person"
    problems = [f"'{m}' is the agent speaking, and the room does not know it: {third}" for m in
                _FIRST_PERSON.findall(text)]  # fmt: skip
    if about != "You":
        problems += [f"'{m}' speaks to the reader, and everyone reads the shared screen: {third}"
                     for m in _SECOND_PERSON.findall(text)]  # fmt: skip
    if len(number_tokens(text)) > ROOM_MAX_NUMBERS:
        problems.append(f"the line for the shared screen carries one fact, with at most {ROOM_MAX_NUMBERS} numbers")
    if _CAPTAIN.search(text) and _EXPECTED.search(text):
        problems.append("one fact: the captain's armband or expected against actual, not both")
    if _ORDER.search(text):
        problems.append(f"a statement about {about}, not an order to the host or the room: the room reads it")
    if about == "You" and _YOU_AS_HE.search(text):
        problems.append('the manager named You is the viewer: "you have", "your captain", not "You has" or "You\'s"')
    if not _names(about).search(text):
        problems.append(f"name {about}: the line goes under {about}'s name on the shared screen and is about them")
    if delta < 0 and not _LOSS_WORDS.search(text):
        problems.append(f"this moment cost {about} {-delta:g} IP: say it is a loss (costs, drops, a blow)")
    if delta > 0 and _LOSS_ONLY_WORDS.search(text):
        problems.append(f"this moment gained {about} {delta:+g} IP: do not write it as a loss")
    return problems


CASUAL_MAX_CHARS = 120  # Spanish runs longer than English
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")

# style -> (sentences, numbers or None for any, characters)
Limits = dict[str, tuple[int, int | None, int]]
EXPLANATION: Limits = {"casual": (1, 1, CASUAL_MAX_CHARS), "analyst": (2, None, 280)}
SUMMARY: Limits = {"casual": (2, 2, 200), "analyst": (3, None, 280)}


def style_problems(text: str, style: str, limits: Limits = EXPLANATION, lang: str = "en") -> list[str]:
    """Analyst and casual must read as two genuinely different experiences (rule 4.1(e))."""
    max_sentences, max_numbers, max_chars = limits[style]
    sentences = [s for s in _SENTENCE_END.split(text.strip()) if s]
    numbers = len(number_tokens(text, lang))
    problems = []
    if len(sentences) > max_sentences:
        problems.append(f"{style} style is at most {max_sentences} sentence{'s' if max_sentences > 1 else ''}")
    if max_numbers is not None and numbers > max_numbers:
        problems.append(f"{style} style uses at most {max_numbers} number{'s' if max_numbers > 1 else ''}")
    if len(text) > max_chars:
        problems.append(f"{style} style is at most {max_chars} characters")
    return problems
