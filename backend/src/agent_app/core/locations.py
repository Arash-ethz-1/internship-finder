"""Turn a board's location string into somewhere you can filter on.

Boards write locations as free prose. Across the corpus you find every one of
``Zurich``, ``Zürich``, ``Zurich, Switzerland``, ``CH-Zurich``,
``Remote - EMEA``, ``London; Berlin`` and ``Multiple Locations``. Stored raw,
those are eight different places, so "internships in Europe" cannot be asked
at all and "Zurich" silently returns a fraction of the Zurich postings.

This module resolves a raw string into zero or more :class:`ParsedLocation`,
each with a city, an ISO 3166-1 alpha-2 country and a coarse region. It is
deliberately a lookup table rather than a geocoding service: the corpus is
job postings in a few hundred cities, an offline table covers almost all of
it, and a wrong-but-confident geocode is worse than an honest ``None``.

Unresolved input is never dropped. It is stored with ``raw`` set and the rest
``None``, so a location the table does not know still shows in the UI and is
still visible as a gap.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

# Coarse buckets. Deliberately few: these exist so you can say "Europe" or
# "North America" in one click, not to model geography.
REGIONS: tuple[str, ...] = (
    "europe",
    "north_america",
    "south_america",
    "asia",
    "middle_east",
    "africa",
    "oceania",
)

REGION_LABELS: dict[str, str] = {
    "europe": "Europe",
    "north_america": "North America",
    "south_america": "South America",
    "asia": "Asia",
    "middle_east": "Middle East",
    "africa": "Africa",
    "oceania": "Oceania",
}

# ISO alpha-2 -> (display name, region). Europe is complete; the rest covers
# the countries that actually appear in tech job boards.
COUNTRIES: dict[str, tuple[str, str]] = {
    # --- Europe -------------------------------------------------------------
    "AL": ("Albania", "europe"),
    "AT": ("Austria", "europe"),
    "BA": ("Bosnia and Herzegovina", "europe"),
    "BE": ("Belgium", "europe"),
    "BG": ("Bulgaria", "europe"),
    "BY": ("Belarus", "europe"),
    "CH": ("Switzerland", "europe"),
    "CY": ("Cyprus", "europe"),
    "CZ": ("Czechia", "europe"),
    "DE": ("Germany", "europe"),
    "DK": ("Denmark", "europe"),
    "EE": ("Estonia", "europe"),
    "ES": ("Spain", "europe"),
    "FI": ("Finland", "europe"),
    "FR": ("France", "europe"),
    "GB": ("United Kingdom", "europe"),
    "GR": ("Greece", "europe"),
    "HR": ("Croatia", "europe"),
    "HU": ("Hungary", "europe"),
    "IE": ("Ireland", "europe"),
    "IS": ("Iceland", "europe"),
    "IT": ("Italy", "europe"),
    "LT": ("Lithuania", "europe"),
    "LU": ("Luxembourg", "europe"),
    "LV": ("Latvia", "europe"),
    "MD": ("Moldova", "europe"),
    "ME": ("Montenegro", "europe"),
    "MK": ("North Macedonia", "europe"),
    "MT": ("Malta", "europe"),
    "NL": ("Netherlands", "europe"),
    "NO": ("Norway", "europe"),
    "PL": ("Poland", "europe"),
    "PT": ("Portugal", "europe"),
    "RO": ("Romania", "europe"),
    "RS": ("Serbia", "europe"),
    "RU": ("Russia", "europe"),
    "SE": ("Sweden", "europe"),
    "SI": ("Slovenia", "europe"),
    "SK": ("Slovakia", "europe"),
    "UA": ("Ukraine", "europe"),
    # --- North America ------------------------------------------------------
    "CA": ("Canada", "north_america"),
    "CR": ("Costa Rica", "north_america"),
    "MX": ("Mexico", "north_america"),
    "PA": ("Panama", "north_america"),
    "US": ("United States", "north_america"),
    # --- South America ------------------------------------------------------
    "AR": ("Argentina", "south_america"),
    "BR": ("Brazil", "south_america"),
    "CL": ("Chile", "south_america"),
    "CO": ("Colombia", "south_america"),
    "PE": ("Peru", "south_america"),
    "UY": ("Uruguay", "south_america"),
    # --- Asia ---------------------------------------------------------------
    "CN": ("China", "asia"),
    "HK": ("Hong Kong", "asia"),
    "ID": ("Indonesia", "asia"),
    "IN": ("India", "asia"),
    "JP": ("Japan", "asia"),
    "KR": ("South Korea", "asia"),
    "MY": ("Malaysia", "asia"),
    "PH": ("Philippines", "asia"),
    "SG": ("Singapore", "asia"),
    "TH": ("Thailand", "asia"),
    "TW": ("Taiwan", "asia"),
    "VN": ("Vietnam", "asia"),
    # --- Middle East --------------------------------------------------------
    "AE": ("United Arab Emirates", "middle_east"),
    "IL": ("Israel", "middle_east"),
    "QA": ("Qatar", "middle_east"),
    "SA": ("Saudi Arabia", "middle_east"),
    "TR": ("Turkey", "middle_east"),
    # --- Africa -------------------------------------------------------------
    "EG": ("Egypt", "africa"),
    "GH": ("Ghana", "africa"),
    "KE": ("Kenya", "africa"),
    "MA": ("Morocco", "africa"),
    "NG": ("Nigeria", "africa"),
    "ZA": ("South Africa", "africa"),
    # --- Oceania ------------------------------------------------------------
    "AU": ("Australia", "oceania"),
    "NZ": ("New Zealand", "oceania"),
}

# Everything a board might write instead of the alpha-2 code. Keys are
# normalised (lowercase, accents stripped) by :func:`_key`.
COUNTRY_ALIASES: dict[str, str] = {
    "usa": "US",
    "u s a": "US",
    "u s": "US",
    "us": "US",
    "united states": "US",
    "united states of america": "US",
    "america": "US",
    "uk": "GB",
    "u k": "GB",
    "great britain": "GB",
    "britain": "GB",
    "england": "GB",
    "scotland": "GB",
    "wales": "GB",
    "northern ireland": "GB",
    "united kingdom": "GB",
    "deutschland": "DE",
    "germany": "DE",
    "schweiz": "CH",
    "suisse": "CH",
    "svizzera": "CH",
    "switzerland": "CH",
    "osterreich": "AT",
    "austria": "AT",
    "nederland": "NL",
    "holland": "NL",
    "the netherlands": "NL",
    "netherlands": "NL",
    "belgie": "BE",
    "belgique": "BE",
    "belgium": "BE",
    "france": "FR",
    "espana": "ES",
    "spain": "ES",
    "italia": "IT",
    "italy": "IT",
    "portugal": "PT",
    "ireland": "IE",
    "republic of ireland": "IE",
    "sverige": "SE",
    "sweden": "SE",
    "norge": "NO",
    "norway": "NO",
    "danmark": "DK",
    "denmark": "DK",
    "suomi": "FI",
    "finland": "FI",
    "island": "IS",
    "iceland": "IS",
    "polska": "PL",
    "poland": "PL",
    "czech republic": "CZ",
    "czechia": "CZ",
    "cesko": "CZ",
    "slovakia": "SK",
    "slovenia": "SI",
    "croatia": "HR",
    "hrvatska": "HR",
    "serbia": "RS",
    "romania": "RO",
    "bulgaria": "BG",
    "greece": "GR",
    "hellas": "GR",
    "hungary": "HU",
    "magyarorszag": "HU",
    "estonia": "EE",
    "latvia": "LV",
    "lithuania": "LT",
    "ukraine": "UA",
    "luxembourg": "LU",
    "malta": "MT",
    "cyprus": "CY",
    "canada": "CA",
    "mexico": "MX",
    "brasil": "BR",
    "brazil": "BR",
    "argentina": "AR",
    "chile": "CL",
    "colombia": "CO",
    "india": "IN",
    "bharat": "IN",
    "china": "CN",
    "prc": "CN",
    "japan": "JP",
    "nippon": "JP",
    "south korea": "KR",
    "korea": "KR",
    "republic of korea": "KR",
    "singapore": "SG",
    "hong kong": "HK",
    "taiwan": "TW",
    "vietnam": "VN",
    "viet nam": "VN",
    "thailand": "TH",
    "malaysia": "MY",
    "indonesia": "ID",
    "philippines": "PH",
    "israel": "IL",
    "united arab emirates": "AE",
    "uae": "AE",
    "turkey": "TR",
    "turkiye": "TR",
    "saudi arabia": "SA",
    "qatar": "QA",
    "egypt": "EG",
    "morocco": "MA",
    "nigeria": "NG",
    "kenya": "KE",
    "ghana": "GH",
    "south africa": "ZA",
    "australia": "AU",
    "new zealand": "NZ",
    "aotearoa": "NZ",
}

# Multi-country shorthands. These resolve to a region with no country, which
# is exactly right: "Remote - EMEA" is a real answer to "where", and pinning
# it to one country would be an invention.
REGION_ALIASES: dict[str, str] = {
    "europe": "europe",
    "european union": "europe",
    "eu": "europe",
    "eea": "europe",
    "emea": "europe",
    "dach": "europe",
    "benelux": "europe",
    "nordics": "europe",
    "nordic": "europe",
    "scandinavia": "europe",
    "iberia": "europe",
    "baltics": "europe",
    "balkans": "europe",
    "central europe": "europe",
    "eastern europe": "europe",
    "western europe": "europe",
    "southern europe": "europe",
    "northern europe": "europe",
    "uk and ireland": "europe",
    "north america": "north_america",
    "namer": "north_america",
    "latam": "south_america",
    "latin america": "south_america",
    "south america": "south_america",
    "apac": "asia",
    "asia pacific": "asia",
    "asia": "asia",
    "sea": "asia",
    "southeast asia": "asia",
    "south east asia": "asia",
    "middle east": "middle_east",
    "mena": "middle_east",
    "africa": "africa",
    "oceania": "oceania",
    "anz": "oceania",
    "amer": "north_america",
    "americas": "north_america",
    "us remote": "north_america",
}

# Parenthetical asides are always about *how* you work there, never where:
# "San Francisco (On-site)", "Remote-Friendly (Travel-Required)".
_PARENS = re.compile(r"\([^)]*\)")


def _key(value: str) -> str:
    """Normalise for lookup: lowercase, accents stripped, runs of space collapsed."""
    decomposed = unicodedata.normalize("NFKD", value)
    stripped = "".join(c for c in decomposed if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]+", " ", stripped.lower()).strip()


# City -> country, as data rather than code: one line per country, cities
# comma-separated. Europe first and in depth, because that is the market this
# search is for; elsewhere, the cities that show up on these boards.
_CITY_TABLE = """
CH: Zurich, Zuerich, Geneva, Genf, Geneve, Basel, Bern, Berne, Lausanne, Lugano, Zug, Winterthur
CH: St Gallen, Sankt Gallen, Neuchatel
DE: Berlin, Munich, Muenchen, Hamburg, Frankfurt, Frankfurt am Main, Cologne, Koeln, Stuttgart
DE: Duesseldorf, Dusseldorf, Leipzig, Dresden, Hannover, Nuremberg, Nuernberg, Karlsruhe
DE: Heidelberg, Bonn, Mannheim, Aachen, Bremen, Essen, Dortmund, Muenster, Munster, Freiburg
DE: Potsdam, Darmstadt, Tuebingen, Tubingen, Ulm, Augsburg, Bielefeld, Jena, Regensburg
AT: Vienna, Wien, Graz, Linz, Salzburg, Innsbruck, Klagenfurt
GB: London, Manchester, Edinburgh, Cambridge, Oxford, Bristol, Birmingham, Leeds, Glasgow
GB: Cardiff, Belfast, Sheffield, Nottingham, Newcastle, Liverpool, Brighton, Reading, Cheltenham
GB: Southampton, Coventry, York, Bath, Milton Keynes
IE: Dublin, Cork, Galway, Limerick
NL: Amsterdam, Rotterdam, The Hague, Den Haag, Utrecht, Eindhoven, Delft, Groningen, Leiden
NL: Nijmegen, Wageningen, Enschede, Tilburg, Haarlem
BE: Brussels, Bruxelles, Brussel, Antwerp, Antwerpen, Ghent, Gent, Leuven, Liege, Bruges, Brugge
BE: Mechelen, Namur
FR: Paris, Lyon, Toulouse, Grenoble, Marseille, Bordeaux, Lille, Nantes, Nice, Montpellier
FR: Strasbourg, Rennes, Sophia Antipolis, Saclay, Palaiseau, Villeurbanne, Aix en Provence
ES: Madrid, Barcelona, Valencia, Seville, Sevilla, Bilbao, Malaga, Zaragoza, Granada, Palma
ES: Alicante, San Sebastian, Santander
PT: Lisbon, Lisboa, Porto, Braga, Coimbra, Aveiro, Funchal
IT: Milan, Milano, Rome, Roma, Turin, Torino, Bologna, Florence, Firenze, Naples, Napoli, Padua
IT: Padova, Pisa, Trento, Genoa, Genova, Trieste, Catania, Bari
SE: Stockholm, Gothenburg, Goteborg, Malmo, Lund, Uppsala, Linkoping, Vasteras, Umea
NO: Oslo, Bergen, Trondheim, Stavanger, Tromso
DK: Copenhagen, Kobenhavn, Aarhus, Odense, Aalborg, Lyngby
FI: Helsinki, Espoo, Tampere, Oulu, Turku, Vantaa, Jyvaskyla
IS: Reykjavik
PL: Warsaw, Warszawa, Krakow, Cracow, Wroclaw, Gdansk, Poznan, Lodz, Katowice, Szczecin, Gdynia
PL: Rzeszow, Lublin
CZ: Prague, Praha, Brno, Ostrava, Plzen, Pilsen
SK: Bratislava, Kosice, Zilina
HU: Budapest, Debrecen, Szeged, Pecs
RO: Bucharest, Bucuresti, Cluj, Cluj Napoca, Timisoara, Iasi, Brasov, Sibiu
BG: Sofia, Plovdiv, Varna, Burgas
GR: Athens, Athina, Thessaloniki, Patras, Heraklion
HR: Zagreb, Split, Rijeka, Osijek
RS: Belgrade, Beograd, Novi Sad, Nis
SI: Ljubljana, Maribor
EE: Tallinn, Tartu
LV: Riga
LT: Vilnius, Kaunas, Klaipeda
UA: Kyiv, Kiev, Lviv, Kharkiv, Odesa, Odessa, Dnipro
LU: Luxembourg City, Esch sur Alzette
MT: Valletta, Sliema
CY: Nicosia, Limassol, Larnaca
RU: Moscow, Saint Petersburg, St Petersburg, Novosibirsk, Kazan
US: New York, New York City, NYC, Brooklyn, San Francisco, SF, South San Francisco, Palo Alto
US: Mountain View, Menlo Park, Sunnyvale, San Jose, Santa Clara, Cupertino, Redwood City
US: San Mateo, Oakland, Berkeley, Los Angeles, Santa Monica, San Diego, Seattle, Bellevue
US: Redmond, Portland, Austin, Dallas, Houston, Chicago, Boston, Cambridge MA, Somerville
US: Denver, Boulder, Atlanta, Miami, Washington, Washington DC, Arlington, McLean, Reston
US: Philadelphia, Pittsburgh, Detroit, Ann Arbor, Minneapolis, Nashville, Charlotte, Raleigh
US: Durham, Phoenix, Tempe, Salt Lake City, Las Vegas, Sacramento, Irvine, Pasadena, Ithaca
US: Princeton, New Haven, Madison, Columbus, Kansas City, St Louis, Indianapolis, Baltimore
US: Richmond, Orlando, Tampa, Jacksonville, Hoboken, Jersey City, Stamford, Greenwich
CA: Toronto, Vancouver, Montreal, Ottawa, Calgary, Edmonton, Waterloo, Kitchener, Quebec City
CA: Victoria, Halifax, Mississauga
MX: Mexico City, Guadalajara, Monterrey, Queretaro
BR: Sao Paulo, Rio de Janeiro, Belo Horizonte, Porto Alegre, Curitiba, Florianopolis, Recife
BR: Brasilia
AR: Buenos Aires, Cordoba, Rosario
CL: Santiago, Valparaiso
CO: Bogota, Medellin, Cali
IN: Bangalore, Bengaluru, Hyderabad, Pune, Mumbai, Delhi, New Delhi, Gurgaon, Gurugram, Noida
IN: Chennai, Kolkata, Ahmedabad, Jaipur, Kochi, Chandigarh
SG: Singapore
HK: Hong Kong, Kowloon
JP: Tokyo, Osaka, Kyoto, Yokohama, Nagoya, Fukuoka
KR: Seoul, Busan, Incheon, Pangyo
CN: Beijing, Shanghai, Shenzhen, Guangzhou, Hangzhou, Chengdu, Nanjing, Suzhou, Wuhan, Xian
TW: Taipei, Hsinchu, Taichung
VN: Hanoi, Ho Chi Minh City, Saigon, Da Nang
TH: Bangkok, Chiang Mai
MY: Kuala Lumpur, Penang, Cyberjaya
ID: Jakarta, Bandung, Surabaya
PH: Manila, Makati, Cebu, Taguig
IL: Tel Aviv, Jerusalem, Haifa, Herzliya, Ramat Gan, Beer Sheva
AE: Dubai, Abu Dhabi
SA: Riyadh, Jeddah
QA: Doha
TR: Istanbul, Ankara, Izmir
EG: Cairo, Alexandria, Giza
MA: Casablanca, Rabat, Marrakech
NG: Lagos, Abuja
KE: Nairobi
GH: Accra
ZA: Cape Town, Johannesburg, Pretoria, Durban
AU: Sydney, Melbourne, Brisbane, Perth, Canberra, Adelaide
NZ: Auckland, Wellington, Christchurch
"""


# German (and Nordic) names reach us spelled two ways: with the umlaut, and
# with the ASCII digraph. Accent-stripping turns "München" into "munchen" while
# the table spells it "Muenchen" -> "muenchen", so neither finds the other
# unless both are indexed.
_DIGRAPHS = (("ue", "u"), ("oe", "o"), ("ae", "a"), ("aa", "a"), ("ss", "s"))


def _digraph_variant(key: str) -> str | None:
    """ "muenchen" -> "munchen". ``None`` when the key has no digraph to fold."""
    folded = key
    for digraph, letter in _DIGRAPHS:
        folded = folded.replace(digraph, letter)
    return folded if folded != key else None


def _load_cities(table: str) -> dict[str, str]:
    """Parse :data:`_CITY_TABLE` into a normalised city -> country lookup."""
    cities: dict[str, str] = {}
    for line in table.strip().splitlines():
        code, _, names = line.partition(":")
        country = code.strip()
        for name in names.split(","):
            key = _key(name)
            if not key:
                continue
            cities[key] = country
            variant = _digraph_variant(key)
            # An existing entry wins: a real city always beats a fold of
            # another city's name.
            if variant and variant not in cities:
                cities[variant] = country
    return cities


CITIES: dict[str, str] = _load_cities(_CITY_TABLE)

US_STATES: frozenset[str] = frozenset(
    _key(s)
    for s in (
        "AL AK AZ AR CA CO CT DE FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MS MO MT NE NV "
        "NH NJ NM NY NC ND OH OK OR PA RI SC SD TN TX UT VT VA WA WV WI WY DC"
    ).split()
) | frozenset(
    # Split on comma, not whitespace: half of these are two words, and
    # splitting on space silently dropped "North Carolina" and its kin.
    _key(s)
    for s in (
        "Alabama, Alaska, Arizona, Arkansas, California, Colorado, Connecticut, Delaware, "
        "Florida, Georgia, Hawaii, Idaho, Illinois, Indiana, Iowa, Kansas, Kentucky, "
        "Louisiana, Maine, Maryland, Massachusetts, Michigan, Minnesota, Mississippi, "
        "Missouri, Montana, Nebraska, Nevada, New Hampshire, New Jersey, New Mexico, "
        "New York State, North Carolina, North Dakota, Ohio, Oklahoma, Oregon, Pennsylvania, "
        "Rhode Island, South Carolina, South Dakota, Tennessee, Texas, Utah, Vermont, "
        "Virginia, Washington State, Washington, West Virginia, Wisconsin, Wyoming, "
        "District of Columbia"
    ).split(",")
)

CANADIAN_PROVINCES: frozenset[str] = frozenset(
    _key(s)
    for s in (
        "ON, QC, BC, AB, MB, SK, NS, NB, PE, Ontario, Quebec, Alberta, Manitoba, "
        "British Columbia, Saskatchewan, Nova Scotia, New Brunswick"
    ).split(",")
)

# Alpha-3 codes, which a few boards use instead of alpha-2. Only the ones that
# cannot be confused with a city or a US state.
COUNTRY_ALPHA3: dict[str, str] = {
    "usa": "US",
    "can": "CA",
    "mex": "MX",
    "gbr": "GB",
    "deu": "DE",
    "che": "CH",
    "aut": "AT",
    "fra": "FR",
    "esp": "ES",
    "prt": "PT",
    "ita": "IT",
    "nld": "NL",
    "bel": "BE",
    "irl": "IE",
    "swe": "SE",
    "nor": "NO",
    "dnk": "DK",
    "fin": "FI",
    "pol": "PL",
    "cze": "CZ",
    "rou": "RO",
    "grc": "GR",
    "ind": "IN",
    "sgp": "SG",
    "jpn": "JP",
    "kor": "KR",
    "aus": "AU",
    "nzl": "NZ",
    "bra": "BR",
    "zaf": "ZA",
    "isr": "IL",
}

# Strings that say "no particular place". They are not locations and produce
# no row, but they are also not failures -- `remote` on the posting is where
# that information already lives.
PLACELESS: frozenset[str] = frozenset(
    _key(s)
    for s in (
        "remote",
        "fully remote",
        "remote first",
        "work from home",
        "wfh",
        "anywhere",
        "worldwide",
        "global",
        "distributed",
        "multiple locations",
        "various locations",
        "various",
        "multiple",
        "flexible",
        "hybrid",
        "on site",
        "onsite",
        "office",
        "hq",
        "headquarters",
        "tbd",
        "n a",
        "none",
        "unknown",
        "other",
        "field",
        "travel",
    )
)

# Metro-area and office phrasings that name a real city with a suffix. Mapped
# rather than stripped, so the display name stays the city people know.
CITY_ALIASES: dict[str, tuple[str, str]] = {
    "bay area": ("San Francisco", "US"),
    "san francisco bay area": ("San Francisco", "US"),
    "sf bay area": ("San Francisco", "US"),
    "silicon valley": ("San Francisco", "US"),
    "greater london": ("London", "GB"),
    "greater boston": ("Boston", "US"),
    "nyc metro": ("New York", "US"),
    "dc metro": ("Washington", "US"),
    "randstad": ("Amsterdam", "NL"),
    "rhine main": ("Frankfurt", "DE"),
}

# Words that decorate a location without being part of it.
_NOISE = re.compile(
    r"\b(remote|hybrid|on-?site|in-?office|in office|optional|based|preferred|"
    r"area|region|metro|metropolitan|metropolitain|office|hq|headquarters|campus|"
    r"greater|city of|full-?time|part-?time|flexible|only|or|and)\b",
    re.IGNORECASE,
)

# Separators between two *different* places. Comma is not here on purpose: it
# is far more often "City, Country" than it is a list.
_SPLIT = re.compile(r"\s*(?:[;|/•·]|\bor\b|\band\b|\n|\r)\s*", re.IGNORECASE)

# A dash inside one segment. Boards use it both ways -- "CH-Zurich",
# "Massachusetts - Boston", "UK - London" put the general part first, while
# "Zurich - Basel" is a list. Which one it is depends on whether both sides
# name a city, so this only splits and the caller decides.
_DASH = re.compile(r"\s*[-–—]\s*")


@dataclass(frozen=True)
class ParsedLocation:
    """One resolved place. ``raw`` is always the board's own words."""

    raw: str
    city: str | None = None
    country: str | None = None
    region: str | None = None

    @property
    def resolved(self) -> bool:
        """Did anything beyond the raw string come out of this."""
        return self.country is not None or self.region is not None

    @property
    def label(self) -> str:
        """How to show this in the UI, best available detail first."""
        if self.city and self.country:
            return f"{self.city}, {COUNTRIES[self.country][0]}"
        if self.country:
            return COUNTRIES[self.country][0]
        if self.region:
            return REGION_LABELS[self.region]
        return self.raw


def region_for_country(code: str | None) -> str | None:
    """The region a country sits in, or ``None`` for an unknown code."""
    entry = COUNTRIES.get((code or "").upper())
    return entry[1] if entry else None


def _canonical_city(key: str) -> str:
    """Title-case a normalised city key for display."""
    return " ".join(word.capitalize() for word in key.split())


def _is_city(text: str) -> bool:
    """Would this part resolve to a city on its own."""
    return _key(text) in CITY_ALIASES or _key(_NOISE.sub(" ", text)) in CITIES


def _scan_for_place(text: str) -> tuple[str | None, str] | None:
    """Find a known city or country sitting inside a longer phrase.

    Returns ``(city, country)`` for the longest match, so "Anywhere in France"
    finds the country and "Moorgate London" finds the city. Matching is on
    whole words over the normalised string, which keeps "Indiana" out of
    "India" and vice versa.
    """
    words = _key(text).split()
    if not words:
        return None

    # Longest first: "new york" must beat "york".
    for size in range(min(3, len(words)), 0, -1):
        for start in range(len(words) - size + 1):
            phrase = " ".join(words[start : start + size])
            if phrase in CITY_ALIASES:
                return CITY_ALIASES[phrase]
            if phrase in CITIES:
                return (_canonical_city(phrase), CITIES[phrase])
            code = COUNTRY_ALIASES.get(phrase)
            if code:
                return (None, code)
    return None


def _resolve_segment(segment: str) -> ParsedLocation | None:
    """Resolve one already-split place string."""
    raw = segment.strip(" \t-–—,")
    if not raw:
        return None

    # Commas run specific -> general ("Zurich, Switzerland", "Austin, TX, USA").
    # Dashes may run either way, so flatten them into the same part list and
    # let the walk below sort out which part is which.
    parts: list[str] = []
    for comma_part in segment.split(","):
        parts.extend(p.strip() for p in _DASH.split(comma_part) if p.strip())
    if not parts:
        parts = [segment.strip()]

    city: str | None = None
    country: str | None = None
    region: str | None = None

    for part in parts:
        # Both keys matter: the alias tables carry phrases like "bay area"
        # whose last word the noise filter would otherwise eat.
        full_key = _key(part)
        key = _key(_NOISE.sub(" ", part))
        if full_key in CITY_ALIASES:
            key = full_key
        if not key or key in PLACELESS or len(key) < 2:
            continue

        if country is None:
            code = (
                COUNTRY_ALIASES.get(key)
                or COUNTRY_ALPHA3.get(key)
                or (key.upper() if key.upper() in COUNTRIES else None)
            )
            if code:
                country = code
                continue

        if region is None and key in REGION_ALIASES:
            region = REGION_ALIASES[key]
            continue

        if city is None and key in CITY_ALIASES:
            city, alias_country = CITY_ALIASES[key]
            country = country or alias_country
            continue

        if city is None and key in CITIES:
            city = _canonical_city(key)
            if country is None:
                country = CITIES[key]
            continue

        # A US state or Canadian province pins the country when the city is
        # one the table has never seen.
        if country is None and key in US_STATES:
            country = "US"
            continue
        if country is None and key in CANADIAN_PROVINCES:
            country = "CA"
            continue

        # An unrecognised first part is most likely the city.
        if city is None and part is parts[0] and len(key) > 2:
            city = part.strip()

    # Last resort: a known place sitting inside a longer phrase, as in
    # "Moorgate London", "Paris Offices" or "Anywhere in France". Only tried
    # when the structured walk found nothing, because on a well-formed string
    # a substring scan is more likely to be wrong than right.
    if country is None and region is None:
        found = _scan_for_place(raw)
        if found is not None:
            # The scan replaces the guess outright. It only runs when the
            # structured walk found nothing, so the guess it displaces is the
            # weakest kind -- "the first fragment, whatever it was" -- and
            # keeping it produces labels like "Anywhere in France, France".
            city, country = found

    if country is not None:
        region = region or region_for_country(country)

    if city is None and country is None and region is None:
        # Nothing resolved. Keep it anyway: an unrecognised place is a gap to
        # see, not a row to throw away.
        key = _key(raw)
        if not key or key in PLACELESS:
            return None
        return ParsedLocation(raw=raw)

    return ParsedLocation(raw=raw, city=city, country=country, region=region)


def parse_locations(raw: str | None) -> list[ParsedLocation]:
    """Resolve a board's location string into zero or more places.

    Zero is a real answer: ``"Remote"`` says nothing about where, and the
    posting's ``remote`` flag already carries that.
    """
    if not raw or not raw.strip():
        return []

    out: list[ParsedLocation] = []
    seen: set[str] = set()
    for segment in _SPLIT.split(_PARENS.sub(" ", raw)):
        for parsed in _resolve_dashed(segment):
            # Two segments naming the same place are one place, however
            # differently they were written -- "Berlin; Berlin, Germany". For an
            # unresolved segment the raw string is all we have to tell one from
            # another, so it stays part of the key there.
            marker = (
                f"{parsed.city}|{parsed.country}|{parsed.region}"
                if parsed.resolved
                else f"raw|{parsed.raw.lower()}"
            )
            if marker in seen:
                continue
            seen.add(marker)
            out.append(parsed)
    return out


def _resolve_dashed(segment: str) -> list[ParsedLocation]:
    """Resolve one segment, splitting on dash only when it separates two cities.

    ``"Massachusetts - Boston"`` is one place written general-first;
    ``"Zurich - Basel"`` is two. The difference is whether both sides name a
    city on their own, which is cheap to check and right far more often than
    either fixed rule.
    """
    sides = [s for s in (part.strip() for part in _DASH.split(segment)) if s]
    if len(sides) > 1 and all(_is_city(side) for side in sides):
        resolved = [_resolve_segment(side) for side in sides]
        return [r for r in resolved if r is not None]

    single = _resolve_segment(segment)
    return [single] if single is not None else []
