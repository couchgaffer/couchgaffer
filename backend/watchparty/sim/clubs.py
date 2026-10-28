"""The invented clubs (rule 4.7: synthetic, no real clubs or players).

HOME and AWAY play the single match; the other four join them in a gameweek (#94). Every club and player
name was checked by web search against real clubs and players (#147).

Each player has a formation slot (a, y): a is progress towards the opponent's goal (0 own goal line,
1 theirs), y is across the pitch. The simulator mirrors a for the away side. The simulator takes the
first player as the keeper and the ninth as the usual kickoff taker, so both keep those places.
"""

from typing import Any

Slot = tuple[float, float]

HOME: dict[str, Any] = {
    "id": "t_hollinsford",
    "name": "Hollinsford United",
    "short_name": "HOL",
    "colour": "#1f5fbf",
    "formation": "4-3-3",
    "players": [
        ("Tom Ashcombe", 1, "GK", (0.04, 0.50)),
        ("Reece Holloway", 2, "DEF", (0.22, 0.15)),
        ("Joel Okonta", 4, "DEF", (0.19, 0.38)),
        ("Mateus Varela", 5, "DEF", (0.19, 0.62)),
        ("Callum Bryer", 3, "DEF", (0.22, 0.85)),
        ("Kwame Adomako", 6, "MID", (0.38, 0.50)),
        ("Ollie Fenwick", 8, "MID", (0.43, 0.30)),
        ("Rafael Dunmore", 10, "MID", (0.43, 0.70)),
        ("Jonah Pryce", 9, "FWD", (0.66, 0.50)),
        ("Tayo Brennan", 7, "FWD", (0.62, 0.20)),
        ("Luca Ferrante", 11, "FWD", (0.62, 0.80)),
    ],
}

AWAY: dict[str, Any] = {
    "id": "t_saltmere",
    "name": "Saltmere Rovers",
    "short_name": "SAL",
    "colour": "#b8322a",
    "formation": "4-2-3-1",
    "players": [
        ("Rhys Mallory", 1, "GK", (0.04, 0.50)),
        ("Danny Oduya", 2, "DEF", (0.22, 0.15)),
        ("Lasse Vikholm", 5, "DEF", (0.19, 0.38)),
        ("Marcus Eyre", 6, "DEF", (0.19, 0.62)),
        ("Theo Langford", 3, "DEF", (0.22, 0.85)),
        ("Ade Fashola", 4, "MID", (0.36, 0.38)),
        ("Conor Blythe", 8, "MID", (0.36, 0.62)),
        ("Elliot Marsden", 10, "MID", (0.55, 0.50)),
        ("Dylan Crossfield", 9, "FWD", (0.70, 0.50)),
        ("Malik Tennant", 7, "MID", (0.53, 0.20)),
        ("Jules Ravier", 11, "FWD", (0.55, 0.80)),
    ],
}


KESTLEBURY: dict[str, Any] = {
    "id": "t_kestlebury",
    "name": "Kestlebury Athletic",
    "short_name": "KES",
    "colour": "#e0559c",
    "formation": "4-4-2",
    "players": [
        ("Nathan Coldridge", 1, "GK", (0.04, 0.50)),
        ("Harvey Quennell", 2, "DEF", (0.22, 0.15)),
        ("Tobi Adekanmi", 5, "DEF", (0.19, 0.38)),
        ("Anders Kjellvang", 6, "DEF", (0.19, 0.62)),
        ("Ewan Thursby", 3, "DEF", (0.22, 0.85)),
        ("Arlo Pennick", 7, "MID", (0.42, 0.15)),
        ("Declan Marrowby", 4, "MID", (0.38, 0.40)),
        ("Idris Bensaoud", 8, "MID", (0.38, 0.60)),
        ("Benji Cortland", 9, "FWD", (0.64, 0.40)),
        ("Kit Hallowell", 11, "MID", (0.42, 0.85)),
        ("Ruben Ostrander", 10, "FWD", (0.62, 0.60)),
    ],
}

RAVENSHOLME: dict[str, Any] = {
    "id": "t_ravensholme",
    "name": "Ravensholme Town",
    "short_name": "RAV",
    "colour": "#6b2fa3",
    "formation": "3-5-2",
    "players": [
        ("Gareth Pilling", 1, "GK", (0.04, 0.50)),
        ("Femi Oyelaran", 4, "DEF", (0.20, 0.28)),
        ("Cian Dorrity", 5, "DEF", (0.18, 0.50)),
        ("Aaron Weatherill", 6, "DEF", (0.20, 0.72)),
        ("Louis Treharne", 2, "MID", (0.40, 0.10)),
        ("Niko Halvard", 8, "MID", (0.36, 0.38)),
        ("Sami Elkarim", 14, "MID", (0.36, 0.62)),
        ("Will Ashenden", 3, "MID", (0.40, 0.90)),
        ("Kwesi Mardle", 9, "FWD", (0.66, 0.40)),
        ("Josh Brightmore", 10, "MID", (0.48, 0.50)),
        ("Harry Ferrabee", 11, "FWD", (0.64, 0.60)),
    ],
}

PELLBROOK: dict[str, Any] = {
    "id": "t_pellbrook",
    "name": "Pellbrook City",
    "short_name": "PEL",
    "colour": "#d98a1c",
    "formation": "4-1-4-1",
    "players": [
        ("Lewis Farrimond", 1, "GK", (0.04, 0.50)),
        ("Jamal Peverell", 2, "DEF", (0.22, 0.15)),
        ("Oskar Brandvik", 5, "DEF", (0.19, 0.38)),
        ("Ryan Tolhurst", 6, "DEF", (0.19, 0.62)),
        ("Finn Gatcombe", 3, "DEF", (0.22, 0.85)),
        ("Moses Adebanjo", 4, "MID", (0.32, 0.50)),
        ("Charlie Wrenfield", 7, "MID", (0.46, 0.15)),
        ("Hugo Castellane", 8, "MID", (0.44, 0.38)),
        ("Archie Bellweather", 9, "FWD", (0.68, 0.50)),
        ("Ellis Morcombe", 10, "MID", (0.44, 0.62)),
        ("Tyrese Halden", 11, "MID", (0.46, 0.85)),
    ],
}

GRINDLESTONE: dict[str, Any] = {
    "id": "t_grindlestone",
    "name": "Grindlestone Wanderers",
    "short_name": "GRI",
    "colour": "#1b8a8f",
    "formation": "5-3-2",
    "players": [
        ("Joe Ashbrigg", 1, "GK", (0.04, 0.50)),
        ("Jordan Haltby", 2, "DEF", (0.26, 0.10)),
        ("Kieran Stopford", 4, "DEF", (0.18, 0.32)),
        ("Emeka Nwosah", 5, "DEF", (0.17, 0.50)),
        ("Ben Lowther-Croft", 6, "DEF", (0.18, 0.68)),
        ("Ross Ellingham", 3, "DEF", (0.26, 0.90)),
        ("Tariq Holcombe", 8, "MID", (0.40, 0.30)),
        ("Matty Brockhurst", 7, "MID", (0.36, 0.50)),
        ("Leon Marchbank", 9, "FWD", (0.64, 0.42)),
        ("Yannick Dufresne", 10, "MID", (0.40, 0.70)),
        ("Alfie Rookwood", 11, "FWD", (0.62, 0.60)),
    ],
}

CLUBS: tuple[dict[str, Any], ...] = (HOME, AWAY, KESTLEBURY, RAVENSHOLME, PELLBROOK, GRINDLESTONE)


def _club(side: str, club: dict[str, Any] | None) -> dict[str, Any]:
    """The injected club, or the single match's club for that side."""
    if club is not None:
        return club
    return HOME if side == "home" else AWAY


def team(side: str, club: dict[str, Any] | None = None) -> dict[str, Any]:
    """The team as MatchMeta wants it. Ids stay p_{side}_{n} whichever club plays."""
    club = _club(side, club)
    players = [
        {"id": f"p_{side}_{n}", "name": name, "shirt": shirt, "position": pos}
        for n, (name, shirt, pos, _) in enumerate(club["players"], start=1)
    ]
    return {**{k: club[k] for k in ("id", "name", "short_name", "colour", "formation")}, "players": players}


def slots(side: str, club: dict[str, Any] | None = None) -> dict[str, Slot]:
    club = _club(side, club)
    return {f"p_{side}_{n}": slot for n, (*_, slot) in enumerate(club["players"], start=1)}
