"""Countries and regions in job locations and requirement phrases ("must be based in the EU or the UK")."""

import re

# ISO code -> (name used in labels, other names). Names are matched as whole words, ignoring case.
COUNTRIES: dict[str, tuple[str, tuple[str, ...]]] = {
    "AT": ("Austria", ()), "BE": ("Belgium", ()), "BG": ("Bulgaria", ()), "HR": ("Croatia", ()),
    "CY": ("Cyprus", ()), "CZ": ("Czechia", ("Czech Republic",)), "DK": ("Denmark", ()), "EE": ("Estonia", ()),
    "FI": ("Finland", ()), "FR": ("France", ()), "DE": ("Germany", ("Deutschland",)), "GR": ("Greece", ()),
    "HU": ("Hungary", ()), "IE": ("Ireland", ("Republic of Ireland",)), "IT": ("Italy", ()), "LV": ("Latvia", ()),
    "LT": ("Lithuania", ()), "LU": ("Luxembourg", ()), "MT": ("Malta", ()), "NL": ("the Netherlands", ("Netherlands", "Holland")),
    "PL": ("Poland", ()), "PT": ("Portugal", ()), "RO": ("Romania", ()), "SK": ("Slovakia", ()), "SI": ("Slovenia", ()),
    "ES": ("Spain", ()), "SE": ("Sweden", ()), "IS": ("Iceland", ()), "LI": ("Liechtenstein", ()), "NO": ("Norway", ()),
    "CH": ("Switzerland", ()), "GB": ("the UK", ("United Kingdom", "Great Britain", "England", "Scotland", "Wales",
                                               "Northern Ireland")),
    "RS": ("Serbia", ()), "BA": ("Bosnia and Herzegovina", ("Bosnia",)), "ME": ("Montenegro", ()),
    "MK": ("North Macedonia", ("Macedonia",)), "AL": ("Albania", ()), "XK": ("Kosovo", ()), "UA": ("Ukraine", ()),
    "MD": ("Moldova", ()), "BY": ("Belarus", ()), "RU": ("Russia", ()), "TR": ("Türkiye", ("Turkey", "Turkiye")),
    "AD": ("Andorra", ()), "MC": ("Monaco", ()), "SM": ("San Marino", ()), "GE": ("Georgia", ()),
    "AM": ("Armenia", ()), "AZ": ("Azerbaijan", ()),
    "US": ("the US", ("United States", "United States of America")), "CA": ("Canada", ()), "AU": ("Australia", ()),
    "NZ": ("New Zealand", ()), "IN": ("India", ()), "IL": ("Israel", ()), "AE": ("the UAE", ("United Arab Emirates",)),
    "SA": ("Saudi Arabia", ()), "QA": ("Qatar", ()), "EG": ("Egypt", ()), "MA": ("Morocco", ()), "TN": ("Tunisia", ()),
    "ZA": ("South Africa", ()), "NG": ("Nigeria", ()), "KE": ("Kenya", ()), "BR": ("Brazil", ()), "MX": ("Mexico", ()),
    "AR": ("Argentina", ()), "SG": ("Singapore", ()), "JP": ("Japan", ()), "CN": ("China", ()),
    "PH": ("the Philippines", ("Philippines",)), "PK": ("Pakistan", ()),
}
# Short forms that are ordinary words in lower case ("us"), so they only count in capitals.
ABBREVIATIONS = {"UK": "GB", "U.K.": "GB", "US": "US", "U.S.": "US", "USA": "US", "UAE": "AE",
                 "EU": "EU", "EEA": "EU", "EMEA": "EMEA"}
# Cities and metro areas LinkedIn uses without a country ("Greater Madrid Metropolitan Area").
CITIES = {
    "Madrid": "ES", "Barcelona": "ES", "Valencia": "ES", "Seville": "ES", "Munich": "DE", "Frankfurt": "DE",
    "Berlin": "DE", "Hamburg": "DE", "Cologne": "DE", "Stuttgart": "DE", "Hannover": "DE", "Braunschweig": "DE",
    "Lyon": "FR", "Paris": "FR", "Strasbourg": "FR", "Toulouse": "FR", "Milan": "IT", "Rome": "IT", "Turin": "IT",
    "Dublin": "IE", "Namur": "BE", "Brussels": "BE", "Antwerp": "BE", "Arnhem": "NL", "Nijmegen": "NL",
    "Amsterdam": "NL", "Rotterdam": "NL", "Eindhoven": "NL", "London": "GB", "Manchester": "GB", "Edinburgh": "GB",
    "Lisbon": "PT", "Porto": "PT", "Warsaw": "PL", "Krakow": "PL", "Kraków": "PL", "Wroclaw": "PL", "Wrocław": "PL",
    "Vienna": "AT", "Zurich": "CH", "Zürich": "CH", "Geneva": "CH", "Belgrade": "RS", "Novi Sad": "RS",
    "Zagreb": "HR", "Ljubljana": "SI", "Sofia": "BG", "Bucharest": "RO", "Budapest": "HU", "Prague": "CZ",
    "Vilnius": "LT", "Riga": "LV", "Tallinn": "EE", "Helsinki": "FI", "Stockholm": "SE", "Copenhagen": "DK",
    "Oslo": "NO", "Athens": "GR", "Thessaloniki": "GR",
}
REGION_WORDS = {"European Union": "EU", "European Economic Area": "EU", "Europe": "EUROPE", "European": "EUROPE", "Western Balkans": "BALKANS",
                "Balkans": "BALKANS"}

_EU = {"AT", "BE", "BG", "HR", "CY", "CZ", "DK", "EE", "FI", "FR", "DE", "GR", "HU", "IE", "IT", "LV", "LT", "LU",
       "MT", "NL", "PL", "PT", "RO", "SK", "SI", "ES", "SE"}
_EUROPE = _EU | {"IS", "LI", "NO", "CH", "GB", "RS", "BA", "ME", "MK", "AL", "XK", "UA", "MD", "BY", "RU", "TR",
                 "AD", "MC", "SM", "GE", "AM", "AZ"}
REGIONS: dict[str, frozenset[str]] = {
    "EU": frozenset(_EU | {"IS", "LI", "NO", "CH"}),  # EU, EEA and Switzerland: free movement for EU citizens
    "UK": frozenset({"GB"}), "US": frozenset({"US"}), "CA": frozenset({"CA"}), "AU": frozenset({"AU"}),
    "EUROPE": frozenset(_EUROPE),
    "EMEA": frozenset(_EUROPE | {"IL", "AE", "SA", "QA", "EG", "MA", "TN", "ZA", "NG", "KE"}),
    "BALKANS": frozenset({"RS", "BA", "ME", "MK", "AL", "XK", "HR", "SI"}),
}
REGION_NAMES = {"EU": "the EU", "EUROPE": "Europe", "EMEA": "EMEA", "BALKANS": "the Balkans"}


def _pattern(names, flags=0) -> re.Pattern:
    # Longest first, so "Northern Ireland" wins over "Ireland" and "European Union" over "Europe".
    alternatives = "|".join(re.escape(n) for n in sorted(names, key=len, reverse=True))
    return re.compile(rf"(?<![\w.])(?:{alternatives})(?![\w])", flags)


_COUNTRY_CODES = {name.casefold(): code for code, (name, others) in COUNTRIES.items()
                  for name in (name.removeprefix("the "), *others)}
_COUNTRY = _pattern([n for code, (name, others) in COUNTRIES.items() for n in (name.removeprefix("the "), *others)],
                    re.IGNORECASE)
_ABBREVIATION = _pattern(ABBREVIATIONS)
_CITY = _pattern(CITIES, re.IGNORECASE)
_CITY_CODES = {name.casefold(): code for name, code in CITIES.items()}
_REGION = _pattern(REGION_WORDS, re.IGNORECASE)
_REGION_CODES = {name.casefold(): code for name, code in REGION_WORDS.items()}


def _countries(text: str) -> list[tuple[int, str]]:
    found = [(m.start(), _COUNTRY_CODES[m.group(0).casefold()]) for m in _COUNTRY.finditer(text)]
    found += [(m.start(), ABBREVIATIONS[m.group(0)]) for m in _ABBREVIATION.finditer(text)
              if ABBREVIATIONS[m.group(0)] in COUNTRIES]
    return sorted(found)


def place_of(location: str | None) -> str | None:
    """The country (ISO code) or region (`EU`, `EUROPE`, `EMEA`) of a LinkedIn location string, if recognisable."""
    if not location:
        return None
    countries = _countries(location)
    if countries:
        return countries[-1][1]  # "London, England, UK": the most general part comes last
    city = _CITY.search(location)
    if city:
        return _CITY_CODES[city.group(0).casefold()]
    abbreviations = [ABBREVIATIONS[m.group(0)] for m in _ABBREVIATION.finditer(location)]
    regions = [_REGION_CODES[m.group(0).casefold()] for m in _REGION.finditer(location)]
    return (abbreviations + regions or [None])[0]


def places_in(text: str) -> list[str]:
    """Every country or region named in a phrase, in order, once each."""
    found = _countries(text)
    found += [(m.start(), _CITY_CODES[m.group(0).casefold()]) for m in _CITY.finditer(text)]
    found += [(m.start(), ABBREVIATIONS[m.group(0)]) for m in _ABBREVIATION.finditer(text)
              if ABBREVIATIONS[m.group(0)] not in COUNTRIES]
    found += [(m.start(), _REGION_CODES[m.group(0).casefold()]) for m in _REGION.finditer(text)]
    return list(dict.fromkeys(code for _, code in sorted(found)))


def place_name(code: str) -> str:
    if code in REGION_NAMES:
        return REGION_NAMES[code]
    return COUNTRIES[code][0] if code in COUNTRIES else code
