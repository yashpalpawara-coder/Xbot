"""Country for a found post. A place tag, then the account's country label, then a profile location that names a country."""

from __future__ import annotations

import re

_ABOUT = re.compile(r"Account based in[:\s]+([^\n]{2,80})", re.I)

# Display name, then extra spellings X uses.
_COUNTRIES: dict[str, tuple[str, ...]] = {
    "Afghanistan": (),
    "Albania": (),
    "Algeria": (),
    "Argentina": (),
    "Australia": (),
    "Austria": (),
    "Bangladesh": (),
    "Belgium": (),
    "Brazil": (),
    "Canada": (),
    "Chile": (),
    "China": ("People's Republic of China", "PRC"),
    "Colombia": (),
    "Czechia": ("Czech Republic",),
    "Denmark": (),
    "Egypt": (),
    "Finland": (),
    "France": (),
    "Germany": (),
    "Ghana": (),
    "Greece": (),
    "Hong Kong": (),
    "Hungary": (),
    "India": (),
    "Indonesia": (),
    "Iran": (),
    "Iraq": (),
    "Ireland": (),
    "Israel": (),
    "Italy": (),
    "Japan": (),
    "Kenya": (),
    "Malaysia": (),
    "Mexico": (),
    "Morocco": (),
    "Nepal": (),
    "Netherlands": ("The Netherlands", "Holland"),
    "New Zealand": (),
    "Nigeria": (),
    "Norway": (),
    "Pakistan": (),
    "Philippines": (),
    "Poland": (),
    "Portugal": (),
    "Qatar": (),
    "Romania": (),
    "Russia": ("Russian Federation",),
    "Saudi Arabia": (),
    "Singapore": (),
    "South Africa": (),
    "South Korea": ("Korea, Republic of", "Republic of Korea"),
    "Spain": (),
    "Sweden": (),
    "Switzerland": (),
    "Taiwan": (),
    "Thailand": (),
    "Turkey": ("Türkiye", "Turkiye"),
    "Ukraine": (),
    "United Arab Emirates": ("UAE",),
    "United Kingdom": ("UK", "Great Britain", "Britain"),
    "United States": ("USA", "US", "United States of America", "U.S.", "U.S.A."),
    "Vietnam": ("Viet Nam",),
}


def _key(value: str) -> str:
    text = " ".join(value.replace("·", " ").split()).strip(" .,-")
    text = re.sub(r"^the\s+", "", text, flags=re.I)
    return text.casefold()


_LOOKUP: dict[str, str] = {}
for _name, _aliases in _COUNTRIES.items():
    _LOOKUP[_key(_name)] = _name
    for _alias in _aliases:
        _LOOKUP[_key(_alias)] = _name


def canonical_country(raw: str) -> str:
    return _LOOKUP.get(_key(raw), "")


def country_from_about(text: str) -> str:
    """The country X prints under About this account."""
    match = _ABOUT.search(text or "")
    if not match:
        return ""
    return canonical_country(match.group(1))


def country_from_place(text: str) -> str:
    """A place label counts only when it names a country, such as 'London, United Kingdom'."""
    cleaned = " ".join((text or "").replace("·", " ").split())
    if not cleaned:
        return ""
    parts = [part.strip(" .") for part in cleaned.split(",")]
    for part in reversed(parts):
        found = canonical_country(part)
        if found:
            return found
    return ""
