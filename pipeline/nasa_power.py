"""
pipeline/nasa_power.py
======================
Download and parsing of hourly data from the NASA POWER API.

Basic usage:
    from pipeline.nasa_power import fetch

    data = fetch(lat=4.71, lon=-74.07, start="20240315", end="20240317")
    # → list of dicts with: key, year, month, day, hour, label,
    #                        ghi, dni, dhi, T2M, WS

Usage with cache (avoids re-downloading the same range):
    from pipeline.nasa_power import fetch, DEFAULT_CACHE_DIR
    data = fetch(4.71, -74.07, "20240315", "20240317",
                 cache_dir=DEFAULT_CACHE_DIR)
"""

import json
import requests
from pathlib import Path

# Default cache directory — anchored to the project root (not the CWD), so it
# works regardless of where the app is launched from.
DEFAULT_CACHE_DIR = Path(__file__).resolve().parent.parent / "data" / "nasa_cache"

# Parameters downloaded from NASA POWER on each request
_PARAMS = ",".join([
    "ALLSKY_SFC_SW_DWN",   # GHI — global horizontal irradiance    [W/m²]
    "ALLSKY_SFC_SW_DIFF",  # DHI — horizontal diffuse component     [W/m²]
    "ALLSKY_SFC_SW_DNI",   # DNI — direct normal irradiance        [W/m²]
    "T2M",                 # air temperature at 2 m                [°C]
    "WS2M",                # wind speed at 2 m                     [m/s]
])

_BASE_URL = (
    "https://power.larc.nasa.gov/api/temporal/hourly/point"
    "?parameters={params}"
    "&community=RE"
    "&longitude={lon}"
    "&latitude={lat}"
    "&start={start}"
    "&end={end}"
    "&format=JSON"
    "&time-standard=LST"
)


# ── Public interface ──────────────────────────────────────────────────────────

def build_url(lat: float, lon: float, start: str, end: str) -> str:
    """
    Build the full NASA POWER API URL.

    Parameters
    ----------
    lat, lon : decimal coordinates
    start, end : dates in YYYYMMDD format (e.g. "20240315")
    """
    return _BASE_URL.format(
        params=_PARAMS, lon=lon, lat=lat, start=start, end=end
    )


def fetch(lat: float, lon: float, start: str, end: str,
          cache_dir: str | Path = None,
          timeout: int = 30) -> list[dict]:
    """
    Download hourly NASA POWER data for the given coordinates and dates.

    If cache_dir is specified, the raw JSON is saved to disk and reused on later
    calls with the same parameters, avoiding redundant API requests.

    Returns
    -------
    List of dicts with the fields:
        key, year, month, day, hour, label,
        ghi, dni, dhi, T2M, WS

    Raises
    ------
    ValueError  — if the API returns error messages
    requests.HTTPError — if the HTTP response is not 2xx
    """
    # ── Cache ─────────────────────────────────────────────────────────────────
    cache_file = None
    if cache_dir:
        cache_path = Path(cache_dir)
        cache_path.mkdir(parents=True, exist_ok=True)
        # Unique file name per coordinates and date range
        fname = f"nasa_{lat}_{lon}_{start}_{end}.json"
        cache_file = cache_path / fname
        if cache_file.exists():
            raw = json.loads(cache_file.read_text(encoding="utf-8"))
            return _parse(raw)

    # ── API request ───────────────────────────────────────────────────────────
    url = build_url(lat, lon, start, end)
    response = requests.get(url, timeout=timeout)
    response.raise_for_status()
    raw = response.json()

    # The API returns errors in "messages" when the parameters are invalid
    if "properties" not in raw:
        msgs = raw.get("messages", ["No NASA POWER data"])
        raise ValueError("; ".join(str(m) for m in msgs))

    # ── Save to cache ─────────────────────────────────────────────────────────
    if cache_file:
        cache_file.write_text(
            json.dumps(raw, ensure_ascii=False), encoding="utf-8"
        )

    return _parse(raw)


# ── Internal parsing ──────────────────────────────────────────────────────────

def _parse(json_data: dict) -> list[dict]:
    """
    Convert the NASA POWER JSON response into a list of normalized dicts.
    Filters invalid records (T2M < -100, NASA fill values).

    The time key has YYYYMMDDHH format (10 digits), e.g.
    "2024031514" = 14h on 15 March 2024.

    Each record includes lat/lon (taken from geometry.coordinates of the
    response) so the Hay-Davies POA transposition in profile.build() can compute
    the solar position without extra plumbing. It also works with existing cache
    files (the raw JSON carries geometry).
    """
    p       = json_data["properties"]["parameter"]
    t2m_map = p.get("T2M", {})

    coords = (json_data.get("geometry") or {}).get("coordinates") or []
    lon0 = coords[0] if len(coords) > 0 else None
    lat0 = coords[1] if len(coords) > 1 else None

    records = []
    for key, t2m in t2m_map.items():
        # NASA uses -999 as fill value when there is no data
        if t2m < -100:
            continue

        ghi = max(0.0, p.get("ALLSKY_SFC_SW_DWN",  {}).get(key, 0))
        dni = max(0.0, p.get("ALLSKY_SFC_SW_DNI",   {}).get(key, 0))
        dhi = max(0.0, p.get("ALLSKY_SFC_SW_DIFF",  {}).get(key, 0))
        ws  = p.get("WS2M", {}).get(key, 1.0)

        records.append({
            "key":   key,
            "year":  int(key[0:4]),
            "month": int(key[4:6]),
            "day":   int(key[6:8]),
            "hour":  int(key[8:10]),
            # Human-readable label for the X axes of the Plotly charts
            "label": f"{key[4:6]}/{key[6:8]} {key[8:10]}h",
            "ghi":   ghi,
            "dni":   dni,
            "dhi":   dhi,
            "T2M":   t2m,
            "WS":    ws,
            "lat":   lat0,
            "lon":   lon0,
        })

    return records
