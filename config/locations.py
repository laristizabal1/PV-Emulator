"""
config/locations.py
===================
Preconfigured Colombian cities for the HMI.

Each entry has:
    name  — display name in the UI
    lat   — decimal latitude (positive = north)
    lon   — decimal longitude (negative = west)
    tamb  — representative ambient temperature °C (reference only; NASA POWER
            provides the real hourly T2M on download)
"""

LOCATIONS: list[dict] = [
    {"name": "Bogotá",         "lat":  4.71,  "lon": -74.07, "tamb": 14},
    {"name": "Medellín",       "lat":  6.25,  "lon": -75.56, "tamb": 22},
    {"name": "Barranquilla",   "lat": 10.96,  "lon": -74.78, "tamb": 28},
    {"name": "Cali",           "lat":  3.45,  "lon": -76.53, "tamb": 24},
    {"name": "Bucaramanga",    "lat":  7.12,  "lon": -73.12, "tamb": 23},
    {"name": "Leticia",        "lat": -4.21,  "lon": -69.94, "tamb": 27},
    {"name": "Riohacha",       "lat": 11.54,  "lon": -72.91, "tamb": 29},
    {"name": "Villa de Leyva", "lat":  5.63,  "lon": -73.52, "tamb": 17},
    {"name": "Custom",         "lat":  4.71,  "lon": -74.07, "tamb": 20},
]

# Index of "Custom" — used in callbacks to decide whether to show the manual
# lat/lon inputs.
CUSTOM_IDX: int = 8


def get(idx: int) -> dict:
    """Return the location by index. Raises IndexError if out of range."""
    return LOCATIONS[idx]


def get_coords(idx: int, custom_lat: float = None,
               custom_lon: float = None) -> tuple[float, float]:
    """
    Return (lat, lon) for the given index.
    If idx == CUSTOM_IDX and custom_lat/lon are passed, use those values.
    """
    if idx == CUSTOM_IDX and custom_lat is not None and custom_lon is not None:
        return float(custom_lat), float(custom_lon)
    loc = LOCATIONS[idx]
    return loc["lat"], loc["lon"]
