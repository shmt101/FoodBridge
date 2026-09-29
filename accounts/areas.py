"""Built-in gazetteer of Australian areas.

FoodBridge is a low-scale platform, so instead of calling a geocoding API we ship
a small curated list of areas with their approximate centre points. That is enough
for the area filter, "nearest first" ordering, offer matching and driver distances.

Distances are straight-line (great-circle) kilometres between area centres, so treat
them as a guide rather than a route length.
"""
import math
from collections import OrderedDict, namedtuple

from django.utils.text import slugify

Area = namedtuple("Area", "key label state lat lng")

STATES = OrderedDict([
    ("NSW", "New South Wales"), ("VIC", "Victoria"), ("QLD", "Queensland"),
    ("WA", "Western Australia"), ("SA", "South Australia"), ("TAS", "Tasmania"),
    ("ACT", "Australian Capital Territory"), ("NT", "Northern Territory"),
])

# (label, latitude, longitude) - approximate area centres.
_RAW = OrderedDict([
    ("NSW", [
        ("Sydney CBD", -33.8688, 151.2093), ("Surry Hills", -33.8850, 151.2110),
        ("Newtown", -33.8960, 151.1790), ("Redfern", -33.8930, 151.2040),
        ("Bondi", -33.8915, 151.2767), ("Randwick", -33.9140, 151.2420),
        ("Mascot", -33.9260, 151.1920), ("Manly", -33.7969, 151.2840),
        ("Chatswood", -33.7960, 151.1830), ("Ryde", -33.8150, 151.1050),
        ("Epping", -33.7720, 151.0820), ("Hornsby", -33.7040, 151.0990),
        ("Castle Hill", -33.7290, 151.0040), ("Parramatta", -33.8150, 151.0011),
        ("Auburn", -33.8490, 151.0330), ("Homebush", -33.8640, 151.0870),
        ("Strathfield", -33.8730, 151.0940), ("Burwood", -33.8770, 151.1040),
        ("Blacktown", -33.7710, 150.9080), ("Penrith", -33.7510, 150.6942),
        ("Bankstown", -33.9170, 151.0350), ("Liverpool", -33.9200, 150.9230),
        ("Hurstville", -33.9670, 151.1030), ("Sutherland", -34.0310, 151.0570),
        ("Cronulla", -34.0580, 151.1520), ("Campbelltown", -34.0650, 150.8140),
        ("Gosford", -33.4250, 151.3420), ("Newcastle", -32.9283, 151.7817),
        ("Wollongong", -34.4278, 150.8931),
    ]),
    ("VIC", [
        ("Melbourne CBD", -37.8136, 144.9631), ("Richmond", -37.8230, 145.0010),
        ("St Kilda", -37.8676, 144.9800), ("Brunswick", -37.7670, 144.9610),
        ("Footscray", -37.8000, 144.9000), ("Box Hill", -37.8190, 145.1220),
        ("Dandenong", -37.9870, 145.2150), ("Geelong", -38.1499, 144.3617),
    ]),
    ("QLD", [
        ("Brisbane CBD", -27.4698, 153.0251), ("South Brisbane", -27.4810, 153.0170),
        ("Fortitude Valley", -27.4570, 153.0350), ("Ipswich", -27.6140, 152.7610),
        ("Gold Coast (Surfers Paradise)", -28.0027, 153.4300),
        ("Sunshine Coast (Maroochydore)", -26.6500, 153.0900),
        ("Toowoomba", -27.5598, 151.9507), ("Townsville", -19.2590, 146.8169),
        ("Cairns", -16.9186, 145.7781),
    ]),
    ("WA", [
        ("Perth CBD", -31.9505, 115.8605), ("Fremantle", -32.0569, 115.7439),
        ("Joondalup", -31.7450, 115.7660), ("Rockingham", -32.2770, 115.7300),
        ("Mandurah", -32.5269, 115.7217),
    ]),
    ("SA", [
        ("Adelaide CBD", -34.9285, 138.6007), ("Glenelg", -34.9800, 138.5150),
        ("Salisbury", -34.7600, 138.6420), ("Mount Gambier", -37.8284, 140.7807),
    ]),
    ("TAS", [("Hobart", -42.8821, 147.3272), ("Launceston", -41.4332, 147.1441)]),
    ("ACT", [
        ("Canberra (Civic)", -35.2809, 149.1300), ("Belconnen", -35.2380, 149.0660),
        ("Tuggeranong", -35.4200, 149.0900),
    ]),
    ("NT", [("Darwin", -12.4634, 130.8456), ("Alice Springs", -23.6980, 133.8807)]),
])

AREAS = OrderedDict()
for _state, _rows in _RAW.items():
    for _label, _lat, _lng in _rows:
        _key = f"{_state.lower()}-{slugify(_label)}"
        AREAS[_key] = Area(_key, _label, _state, _lat, _lng)


def get_area(key):
    return AREAS.get(key or "")


def area_label(key, with_state=True):
    area = get_area(key)
    if not area:
        return ""
    return f"{area.label}, {area.state}" if with_state else area.label


def area_choices(blank_label="Select your area…"):
    """Grouped choices (rendered as <optgroup> per state) for a <select>."""
    groups = [("", blank_label)]
    for state, name in STATES.items():
        rows = [(a.key, a.label) for a in AREAS.values() if a.state == state]
        groups.append((name, rows))
    return groups


def state_choices(blank_label="All states"):
    return [("", blank_label)] + [(code, name) for code, name in STATES.items()]


def distance_km(key_a, key_b):
    """Great-circle distance between two areas in km (1 dp), or None if either is unknown."""
    a, b = get_area(key_a), get_area(key_b)
    if not a or not b:
        return None
    if a.key == b.key:
        return 0.0
    lat1, lat2 = math.radians(a.lat), math.radians(b.lat)
    dlat, dlng = lat2 - lat1, math.radians(b.lng - a.lng)
    h = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlng / 2) ** 2
    return round(2 * 6371.0 * math.asin(math.sqrt(h)), 1)
