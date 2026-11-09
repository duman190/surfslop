"""The surf spots, Capitola to Fort Point, south to north.

Per spot:
  offshore   the compass direction an offshore wind blows FROM (so the beach faces offshore + 180)
  exposure   how much of an open-ocean swell's breaking height reaches the spot, for swell from the
             S (195°), W (265°) and NW (300°). First guesses from how the coast lies; fusion.py
             re-scales each open source against Surfline's spot heights when Surfline is on.
  surfline   Surfline's spot id          sf      surf-forecast.com's break name
  featured   on the home page preview (the Friday poll's seven)
"""

from __future__ import annotations

import re
from dataclasses import dataclass

TZ = "America/Los_Angeles"


@dataclass(frozen=True)
class Region:
    id: str
    name: str
    offshore_point: tuple  # where the open-ocean wave models are read (lat, lon)
    tide_station: str      # NOAA CO-OPS
    tide_name: str
    buoys: tuple           # NDBC, nearest first


REGIONS = {r.id: r for r in (
    Region("santa-cruz", "Santa Cruz", (36.85, -122.30), "9413745", "Santa Cruz", ("46042", "46236")),
    Region("north-coast", "North Coast", (36.85, -122.30), "9413745", "Santa Cruz", ("46042", "46012")),
    Region("half-moon-bay", "Half Moon Bay", (37.40, -122.70), "9414131", "Pillar Point Harbor", ("46012", "46026")),
    Region("pacifica", "Pacifica", (37.70, -122.75), "9414275", "Ocean Beach", ("46026", "46237")),
    Region("san-francisco", "San Francisco", (37.70, -122.75), "9414275", "Ocean Beach", ("46026", "46237")),
)}

BUOYS = {
    "46042": "Monterey (27 nm WNW)",
    "46236": "Monterey Canyon Outer",
    "46012": "Half Moon Bay (24 nm SSW of SF)",
    "46026": "San Francisco (18 nm W)",
    "46237": "San Francisco Bar",
}


@dataclass(frozen=True)
class Spot:
    id: str
    name: str
    region: str
    lat: float
    lon: float
    offshore: int
    exposure: tuple
    surfline: str | None = None
    sf: str | None = None
    featured: bool = False
    aka: str = ""
    about: str = ""

    @property
    def faces(self) -> int:
        return (self.offshore + 180) % 360


def _s(*args, **kwargs) -> Spot:
    return Spot(*args, **kwargs)


SPOTS = (
    # --- Santa Cruz (south-facing; Point Santa Cruz blocks most NW swell, more so further east)
    _s("capitola", "Capitola", "santa-cruz", 36.9715, -121.9486, 315, (.45, .20, .10),
       "5842041f4e65fad6a7708ddf", "Capitola-Jetty", about="Soft beginner waves by the jetty; needs a big NW or a S swell."),
    _s("privates", "Privates", "santa-cruz", 36.9643, -121.9602, 315, (.55, .30, .16),
       "641c7e5395774e076bf4dd4c", "Santa-Cruz-41st-Privates", about="Reef below the cliffs east of the Hook."),
    _s("the-hook", "The Hook", "santa-cruz", 36.9590, -121.9640, 315, (.65, .38, .23),
       "584204204e65fad6a7709996", "Santa-Cruz-41st-Privates", aka="41st Ave", about="Kelp-groomed right at the end of 41st Ave."),
    _s("jacks", "Jack's", "santa-cruz", 36.9572, -121.9683, 315, (.55, .30, .16),
       "5842041f4e65fad6a770880b", "Pleasure-Point-Insides", aka="38th Ave", about="Mellow longboard reef at 38th Ave."),
    _s("pleasure-point", "Pleasure Point", "santa-cruz", 36.9538, -121.9726, 340, (.75, .50, .33),
       "5842041f4e65fad6a7708807", "Pleasure-Point-First-Peak", featured=True, aka="First Peak, Sewer Peak",
       about="Long right point over reef; several peaks, works on most tides."),
    _s("rockview", "Rockview", "santa-cruz", 36.9540, -121.9746, 19, (.70, .50, .33),
       "6418e487a34c333f4319067d", "Santa-Cruz-Rockview-St", about="Reef just west of Pleasure Point."),
    _s("wind-and-sea", "Wind and Sea", "santa-cruz", 36.9539, -121.9767, 35, (.70, .50, .39),
       "6418e48193265ba4351912cb", None),
    _s("26th-ave", "26th Ave", "santa-cruz", 36.9572, -121.9818, 68, (.70, .50, .39),
       "5842041f4e65fad6a770898a", "Santa-Cruz-26th", about="Beach break; closes out when it gets big."),
    _s("sunny-cove", "Sunny Cove", "santa-cruz", 36.9600, -121.9896, 1, (.50, .35, .23),
       "640a3f944eb375c353945a57", None),
    _s("blacks-beach", "Blacks Beach", "santa-cruz", 36.9595, -121.9942, 36, (.60, .45, .33),
       "640a3f9599dd44782b032f46", None),
    _s("cowells", "Cowells", "santa-cruz", 36.9592, -122.0249, 292, (.35, .25, .16),
       "5842041f4e65fad6a7708806", "Cowells-Cove", about="Gentle beginner wave inside Steamer Lane; best at low tide."),
    _s("steamer-lane", "Steamer Lane", "santa-cruz", 36.9519, -122.0252, 331, (.80, .75, .59),
       "5842041f4e65fad6a7708805", "Steamer-Lane-Middle-Peak", featured=True, aka="The Lane, The Point, Middle Peak, The Slot",
       about="Right point off the lighthouse; takes NW through S swell and holds size."),
    _s("mitchells-cove", "Mitchell's Cove", "santa-cruz", 36.9515, -122.0421, 0, (.70, .55, .39),
       "5842041f4e65fad6a7708987", "Mitchells-Cove"),
    _s("swift-street", "Swift Street", "santa-cruz", 36.9491, -122.0476, 45, (.50, .35, .23),
       "61c3a280c41237ad6c1ea112", None),
    _s("natural-bridges", "Natural Bridges", "santa-cruz", 36.9491, -122.0612, 30, (.70, .55, .39),
       "5842041f4e65fad6a7708986", "Natural-Bridges-State-Beach"),
    # --- North Coast (open to the NW and W, windy by midday)
    _s("three-mile", "Three Mile", "north-coast", 36.9613, -122.1141, 0, (.45, .65, .59),
       "5e618490277db7f194f080fa", None),
    _s("four-mile", "Four Mile", "north-coast", 36.9651, -122.1253, 4, (.45, .65, .59),
       "5842041f4e65fad6a7708981", "Four-Mile", featured=True,
       about="Right reef-point north of town; a short walk in, cleaner than the open beaches."),
    _s("davenport", "Davenport", "north-coast", 37.0235, -122.2191, 50, (.45, .70, .59),
       "5842041f4e65fad6a7708983", "Davenport-Landing", aka="Davenport Landing"),
    _s("scott-creek", "Scott Creek", "north-coast", 37.0412, -122.2334, 52, (.45, .70, .59),
       "5842041f4e65fad6a7708982", "Scott-Creek-Beach"),
    _s("waddell-creek", "Waddell Creek", "north-coast", 37.0952, -122.2805, 50, (.45, .70, .59),
       "5842041f4e65fad6a7708980", "Waddell-Creek", featured=True,
       about="Exposed beach and reef; picks up everything, blown out by the afternoon northwesterly."),
    _s("ano-nuevo", "Año Nuevo", "north-coast", 37.1158, -122.3147, 350, (.50, .70, .59),
       "5842041f4e65fad6a770897f", "Ano-Nuevo", aka="Ano Nuevo"),
    # --- Half Moon Bay
    _s("gazos-creek", "Gazos Creek", "half-moon-bay", 37.1632, -122.3653, 92, (.50, .75, .70),
       "61c3a7a1277a933173241ebb", "Gazos-Creek"),
    _s("pigeon-point", "Pigeon Point", "half-moon-bay", 37.1814, -122.3927, 23, (.50, .75, .78),
       "5842041f4e65fad6a770897e", None),
    _s("pescadero", "Pescadero", "half-moon-bay", 37.2731, -122.4127, 106, (.50, .75, .78),
       "5842041f4e65fad6a770897d", "Pomponio-State-Beach"),
    _s("tunitas-creek", "Tunitas Creek", "half-moon-bay", 37.3571, -122.4069, 67, (.50, .75, .78),
       "5842041f4e65fad6a7708972", "San-Gregorio-State-Beach"),
    _s("hmb-state-beach", "Half Moon Bay State Beach", "half-moon-bay", 37.4829, -122.4549, 68, (.55, .60, .47),
       "5842041f4e65fad6a770896f", "Francis-Beach", aka="Francis Beach, Dunes, Kelly Ave"),
    _s("half-moon-bay", "Half Moon Bay", "half-moon-bay", 37.4990, -122.4710, 45, (.55, .50, .47),
       "5842041f4e65fad6a7708970", "Princeton-Breakwater", featured=True, aka="Surfer's Beach, Princeton Jetty, El Granada, HMB",
       about="Surfer's Beach beside the Princeton breakwater; Pillar Point takes the edge off NW swell."),
    _s("mavericks", "Mavericks", "half-moon-bay", 37.4917, -122.5042, 20, (.40, .70, .78),
       "5842041f4e65fad6a7708801", "Mavericks", aka="Maverick's", about="Big-wave reef off Pillar Point. Experts only."),
    _s("montara", "Montara", "half-moon-bay", 37.5473, -122.5161, 130, (.50, .70, .70),
       "5842041f4e65fad6a7708974", "Montara"),
    # --- Pacifica
    _s("pedro-point", "Pedro Point", "pacifica", 37.5988, -122.5144, 142, (.40, .60, .74),
       "5842041f4e65fad6a7708978", "Pedro-Point"),
    _s("linda-mar", "Pacifica", "pacifica", 37.5988, -122.5046, 124, (.35, .50, .44),
       "5842041f4e65fad6a7708976", "Linda-Mar-1", featured=True, aka="Linda Mar, Lindamar, Pacifica State Beach",
       about="Linda Mar: sheltered beach break, smaller and friendlier at the south end."),
    _s("linda-mar-north", "Linda Mar North", "pacifica", 37.6024, -122.5010, 125, (.30, .42, .37),
       "5cbf8d85e7b15800014909e8", None),
    _s("rockaway", "Rockaway", "pacifica", 37.6108, -122.4973, 113, (.45, .70, .74),
       "5842041f4e65fad6a7708979", "Rockaway"),
    _s("sharp-park", "Sharp Park", "pacifica", 37.6342, -122.4951, 99, (.45, .70, .74),
       "5842041f4e65fad6a770897c", "Sharp-Park"),
    # --- San Francisco
    _s("ocean-beach-south", "Ocean Beach South", "san-francisco", 37.7417, -122.5104, 81, (.55, .85, .90),
       "5842041f4e65fad6a77087f9", "Ocean-Beach", aka="Sloat"),
    _s("ocean-beach", "Ocean Beach", "san-francisco", 37.7528, -122.5101, 81, (.50, .75, .75),
       "638e32a4f052ba4ed06d0e3e", "Ocean-Beach", featured=True, aka="OB, Noriega, Central Ocean Beach",
       about="Heavy, shifting beach break with strong currents; best on a small swell with an east wind."),
    _s("ocean-beach-north", "Ocean Beach North", "san-francisco", 37.7752, -122.5131, 80, (.45, .68, .66),
       "5d9b68deab58860001c7359e", "Kellys-Cove", aka="Kelly's Cove, VFW"),
    _s("fort-point", "Fort Point", "san-francisco", 37.8100, -122.4751, 0, (.10, .35, .33),
       "5842041f4e65fad6a770897a", "Fort-Point", about="Left under the Golden Gate; needs a big swell and an incoming tide."),
)

BY_ID = {s.id: s for s in SPOTS}
# The home page preview, in the order of the Friday poll's forecast card.
FEATURED = tuple(BY_ID[i] for i in ("steamer-lane", "pleasure-point", "four-mile", "waddell-creek",
                                    "half-moon-bay", "linda-mar", "ocean-beach"))


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", text.lower().replace("'", "").replace("ñ", "n")).strip()


def find(query: str) -> Spot | None:
    """A spot by id, name or nickname ("OB", "linda mar", "surfers beach")."""
    q = _norm(query)
    if not q:
        return None
    slug = q.replace(" ", "-")
    if slug in BY_ID:
        return BY_ID[slug]
    matches = search(query)
    return matches[0] if matches else None


def search(query: str) -> list:
    q = _norm(query)
    if not q:
        return list(SPOTS)
    scored = []
    for spot in SPOTS:
        names = [_norm(spot.name)] + [_norm(a) for a in spot.aka.split(",") if a.strip()]
        if q in names:
            rank = 0
        elif any(n.startswith(q) for n in names):
            rank = 1
        elif any(q in n for n in names):
            rank = 2
        elif q in _norm(REGIONS[spot.region].name):
            rank = 3
        else:
            continue
        scored.append((rank, not spot.featured, spot))
    return [s for _, _, s in sorted(scored, key=lambda x: x[:2])]


def public(spot: Spot) -> dict:
    region = REGIONS[spot.region]
    return {
        "id": spot.id, "name": spot.name, "aka": spot.aka, "about": spot.about,
        "region": region.name, "region_id": region.id, "lat": spot.lat, "lon": spot.lon,
        "faces_deg": spot.faces, "offshore_wind_from_deg": spot.offshore, "featured": spot.featured,
    }
