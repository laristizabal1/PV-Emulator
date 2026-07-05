"""
pipeline/profile.py
===================
Orchestrates the V_set / I_set / P_set setpoint profile computation.

Flow:
    NASA data → build() → apply_strategy() → list of steps ready for SeqLog
                                              or SCPI

Usage:
    from pipeline.profile import build, apply_strategy
    from models.single_diode import SingleDiodeModel
    from models.base import ModuleParams

    params  = ModuleParams(Isc_n=13.5, Voc_n=49.8, ...)
    model   = SingleDiodeModel(params)
    model.fit()

    full    = build(nasa_data, model, Ns_arr=1, Np_arr=1, tilt=10)
    profile = apply_strategy(full, strategy="day")
"""

import math
import logging
from models.base import PVModel

logger = logging.getLogger(__name__)


def _poa_hay_davies(nasa_data: list[dict], tilt: float,
                    surface_azimuth: float) -> list[float] | None:
    """
    GHI/DNI/DHI → POA transposition with the Hay-Davies (1980) model,
    vectorized via pvlib (irradiance.get_total_irradiance).

    Uses lat/lon embedded in the NASA records (added by nasa_power._parse) and
    the data's local standard time (time-standard=LST from NASA POWER; timezone
    approximated by longitude, 15°/h — exact for Colombia, lon≈−74 → UTC−5).
    Timestamps are centered at the half hour to reduce the solar-position bias in
    hourly averages.

    Returns a list of Gpoa [W/m²] aligned with nasa_data, or None if pvlib is
    unavailable or the records have no coordinates — in that case build() uses
    the simple geometric fallback.
    """
    if not nasa_data:
        return None
    lat = nasa_data[0].get("lat")
    lon = nasa_data[0].get("lon")
    if lat is None or lon is None:
        return None
    try:
        import pandas as pd
        from datetime import timezone, timedelta
        from pvlib import solarposition, irradiance
    except ImportError:
        return None

    tz = timezone(timedelta(hours=int(round(lon / 15.0))))
    times = pd.DatetimeIndex([
        pd.Timestamp(year=d["year"], month=d["month"], day=d["day"],
                     hour=d["hour"], minute=30)
        for d in nasa_data
    ]).tz_localize(tz)

    solpos    = solarposition.get_solarposition(times, lat, lon)
    dni_extra = irradiance.get_extra_radiation(times)
    zenith    = solpos["apparent_zenith"]

    ghi = pd.Series([d["ghi"] for d in nasa_data], index=times)
    dhi = pd.Series([d["dhi"] for d in nasa_data], index=times)

    # Closure DNI (GHI − DHI)/cos(z) instead of NASA POWER's DNI: NASA's
    # satellite components do not satisfy GHI = DHI + DNI·cos(z) (~20 % deficit
    # measured at noon in Cali), and using them directly depresses the POA. GHI
    # is the most reliable product → anchor there. pvlib irradiance.dni()
    # implements the closure with safety clips (zenith > 88° → 0, clear-sky
    # limits).
    dni_closure = irradiance.dni(ghi, dhi, zenith).fillna(0.0)

    poa = irradiance.get_total_irradiance(
        surface_tilt    = tilt,
        surface_azimuth = surface_azimuth,
        solar_zenith    = zenith,
        solar_azimuth   = solpos["azimuth"],
        dni = dni_closure,
        ghi = ghi,
        dhi = dhi,
        dni_extra = dni_extra,
        model = "haydavies",
    )
    g = poa["poa_global"].fillna(0.0).clip(lower=0.0)
    return [float(v) for v in g]


def build(nasa_data: list[dict],
          model:   PVModel,
          Ns_arr:  int   = 1,
          Np_arr:  int   = 1,
          tilt:    float = 10.0,
          V_max:   float = 60.0,
          I_max:   float = 170.0,
          attach_curve: bool = False,
          curve_pts:    int  = 40,
          surface_azimuth: float = 180.0,
          thermal_model: str = "faiman") -> list[dict]:
    """
    Compute Gpoa, Tcell and the electrical setpoints for each hourly point.

    Parameters
    ----------
    nasa_data : output of pipeline/nasa_power.fetch()
    model     : PVModel instance already fitted with model.fit()
    Ns_arr    : number of series modules in the array
    Np_arr    : number of parallel strings in the array
    tilt      : module tilt from horizontal [°]
    V_max     : hardware maximum voltage limit [V]
    I_max     : hardware maximum current limit [A]
    attach_curve : if True, attaches the model's I-V curve to each step
                   (curve_v/curve_i keys, `curve_pts` points) plus the hour's
                   Voc and Isc. Required by run_profile with envelope="cp" or
                   "curve". If the model does not implement iv_curve() the keys
                   are not added and execution degrades to the "direct" envelope
                   (the diode models all implement it).
    curve_pts    : points of the attached table (curve subsampling).
    surface_azimuth : array surface azimuth [°] for the POA transposition
                   (pvlib convention: 180 = south, 90 = east).
    thermal_model : cell-temperature model — "faiman" (DEFAULT; pvlib, uses the
                   hour's wind speed WS2M, more physical) or "noct" (simple NOCT).

    Returns
    -------
    List of dicts with the nasa_data fields plus:
        Gpoa  — effective plane-of-array irradiance [W/m²]
        Tcell — estimated cell temperature [°C]
        V_set — voltage setpoint for the source [V]
        I_set — current setpoint for the source [A]
        P_set — resulting power [W]
        (with attach_curve) curve_v, curve_i, Voc, Isc
    """
    # Import here to avoid an import cycle with models/
    from models.thermal import cell_temperature

    # ── POA transposition (once, vectorized) ──────────────────────────────────
    # Hay-Davies (1980) via pvlib — anisotropic model cited in the paper.
    # If pvlib is unavailable or the records have no lat/lon (old data serialized
    # without coordinates), fall back to the simple geometric model.
    gpoa_hd = _poa_hay_davies(nasa_data, tilt, surface_azimuth)
    if gpoa_hd is None:
        logger.warning(
            "Hay-Davies transposition unavailable (pvlib installed? "
            "records with lat/lon?) — using the simple geometric model.")

    result = []
    for i, d in enumerate(nasa_data):
        # ── Plane-of-array irradiance (POA) ──────────────────────────────────
        if gpoa_hd is not None:
            Gpoa = gpoa_hd[i]
        else:
            # Simple geometric fallback (historical pipeline model)
            cos_tilt = math.cos(math.radians(tilt))
            Gpoa = max(0.0, d["ghi"] + d["dni"] * 0.1 * (1.0 - cos_tilt))

        # ── Cell temperature (default NOCT; 'faiman' uses wind, via pvlib) ───
        Tcell = cell_temperature(Gpoa, d["T2M"], model.p.noct,
                                 wind=d.get("WS", 1.0), model=thermal_model)

        # ── Array MPP via the selected model ─────────────────────────────────
        mpp = model.get_mpp(
            G_poa    = Gpoa,
            T_cell   = Tcell,
            Ns_arr   = Ns_arr,
            Np_arr   = Np_arr,
            V_max_hw = V_max,
            I_max_hw = I_max,
        )

        step = {
            **d,                          # keep all NASA fields
            "Gpoa":  round(Gpoa,    0),
            "Tcell": round(Tcell,   1),
            "V_set": mpp.Vmp,
            "I_set": mpp.Imp,
            "P_set": mpp.Pmp,
        }

        # ── Hour's I-V curve (for the "cp" and "curve" envelopes) ───────────
        if attach_curve and mpp.Pmp > 0:
            curve = _hourly_curve(model, Gpoa, Tcell, Ns_arr, Np_arr,
                                  curve_pts, V_max, I_max)
            if curve:
                step.update(curve)

        result.append(step)

    return result


def _hourly_curve(model: PVModel, Gpoa: float, Tcell: float,
                  Ns_arr: int, Np_arr: int, curve_pts: int,
                  V_max: float, I_max: float) -> dict:
    """
    Subsampled I(V) table + the hour's Voc/Isc, ready to serialize into the
    profile step (dcc.Store / JSON). {} if the model does not implement
    iv_curve() or the curve is degenerate.
    """
    try:
        res = model.iv_curve(Gpoa, Tcell, Ns_arr, Np_arr, n_pts=200)
    except NotImplementedError:
        return {}
    if res.V_arr is None or len(res.V_arr) < 2:
        return {}

    n = len(res.V_arr)
    idxs = [round(j * (n - 1) / (curve_pts - 1)) for j in range(curve_pts)]
    curve_v = [round(min(float(res.V_arr[j]), V_max), 3) for j in idxs]
    curve_i = [round(min(float(res.I_arr[j]), I_max), 4) for j in idxs]

    # Voc: first voltage where the current drops to ~0 (the curve is swept up to
    # Voc_est·1.05, so the zero crossing is within range).
    Voc = curve_v[-1]
    for v, i in zip(curve_v, curve_i):
        if i <= 1e-4:
            Voc = v
            break

    return {
        "curve_v": curve_v,
        "curve_i": curve_i,
        "Voc":     round(min(Voc, V_max), 3),
        "Isc":     curve_i[0],
    }


def apply_strategy(profile: list[dict], strategy: str) -> list[dict]:
    """
    Filter or average the full profile according to the emulation strategy.

    Strategies
    ----------
    "full"    : returns the full profile unchanged
    "day"     : filters hours 6–18 (daytime window)
    "average" : builds a 24 h profile with the statistical average per hour of
                the day (useful to emulate a typical day)
    """
    if strategy == "full":
        return profile

    if strategy == "day":
        return [d for d in profile if 6 <= d["hour"] <= 18]

    if strategy == "average":
        # Accumulate sums per hour of day
        buckets: dict[int, dict] = {}
        # T2M (aire) y WS (viento) se conservan porque son los DRIVERS PRIMARIOS
        # del modelo térmico (Faiman): densify() interpola estos y RECALCULA Tcell,
        # en vez de interpolar la Tcell derivada (más fiel a la física).
        num_keys = ["V_set", "I_set", "P_set", "Gpoa", "Tcell", "ghi", "T2M", "WS"]
        # Voc/Isc only exist on sunlit hours (attach_curve adds them if
        # Pmp > 0): they are averaged separately, only over the records that
        # carry them, so they are not diluted by the sunless hours of the same
        # bucket. Without this the averaged profile lost the keys and the "cp"
        # envelope of run_profile degraded to "direct".
        env_keys = ["Voc", "Isc"]

        for d in profile:
            h = d["hour"]
            if h not in buckets:
                buckets[h] = {k: 0.0 for k in num_keys}
                buckets[h]["n"] = 0
                buckets[h]["hour"] = h
                for k in env_keys:
                    buckets[h][f"{k}_sum"] = 0.0
                    buckets[h][f"{k}_n"]   = 0
            for k in num_keys:
                buckets[h][k] += d.get(k, 0.0)
            buckets[h]["n"] += 1
            for k in env_keys:
                v = d.get(k)
                if v:
                    buckets[h][f"{k}_sum"] += v
                    buckets[h][f"{k}_n"]   += 1

        # Average and filter daylight hours (5–19)
        averaged = []
        for h, b in sorted(buckets.items()):
            if not (5 <= h <= 19):
                continue
            n = b["n"]
            step = {
                "hour":  h,
                "label": f"{h}h",
                "V_set": round(b["V_set"] / n, 3),
                "I_set": round(b["I_set"] / n, 3),
                "P_set": round(b["P_set"] / n, 1),
                "Gpoa":  round(b["Gpoa"]  / n, 0),
                "Tcell": round(b["Tcell"] / n, 1),
                "ghi":   round(b["ghi"]   / n, 0),
                "T2M":   round(b["T2M"]   / n, 2),   # aire (driver Faiman)
                "WS":    round(b["WS"]    / n, 2),   # viento (driver Faiman)
            }
            for k in env_keys:
                if b[f"{k}_n"] > 0:
                    step[k] = round(b[f"{k}_sum"] / b[f"{k}_n"], 3)
            averaged.append(step)
        return averaged

    # Fallback: return the profile unchanged
    return profile


def densify(profile: list[dict],
            model:   PVModel,
            points_per_hour: int,
            Ns_arr:  int   = 1,
            Np_arr:  int   = 1,
            V_max:   float = 60.0,
            I_max:   float = 170.0,
            attach_curve: bool = False,
            curve_pts:    int  = 40,
            thermal_model: str = "faiman") -> list[dict]:
    """
    Densifica un perfil horario (escalera) en una curva suave, fiel a la física.

    Interpola los DRIVERS METEOROLÓGICOS PRIMARIOS — irradiancia Gpoa, temperatura
    del aire T2M y viento WS — con PCHIP (Hermite cúbico MONÓTONO, shape-preserving:
    sigue la campana diurna sin overshoot, no empuja G/viento bajo 0). En cada
    sub-punto RECALCULA la temperatura de celda con el modelo térmico (Faiman usa
    G, T_aire y viento) y luego el MPP (y la curva I(V)) con `model`. No interpola
    la Tcell derivada: hereda la no linealidad real (Faiman ~ 1/(U0+U1·viento)).

    Si scipy no está disponible cae a interpolación LINEAL. Si el perfil no trae
    los drivers primarios (T2M/WS — p.ej. una sesión vieja), cae a interpolar la
    Tcell ya calculada (comportamiento previo).

    El MPP se mueve despacio → el MPPT del DUT lo sigue sin re-buscar en cada hora
    → mucho menos transitorio que la escalera. Acompañar con un dt_ms más chico al
    ejecutar (si no, el ensayo se alarga × points_per_hour).

    Parameters
    ----------
    profile         : perfil horario (cada paso trae 'hour', 'Gpoa' y — para el
                      recálculo físico — 'T2M' y 'WS'). El de build()/apply_strategy.
    model           : PVModel ya ajustado (model.fit()).
    points_per_hour : sub-puntos por intervalo horario. 1 (o <2) → perfil sin
                      tocar; 6 → un punto cada 10 min. El último punto se conserva.
    attach_curve    : si True, recalcula curve_v/curve_i + Voc/Isc por sub-punto
                      (requerido por las envolventes cp/curve de run_profile).
    thermal_model   : "faiman" (usa viento) o "noct" — para recomputar Tcell.

    Returns
    -------
    Lista de pasos densificada con el mismo esquema que build() (hour, label,
    V/I/P_set, Gpoa, Tcell y, con attach_curve, curve_v/curve_i + Voc/Isc).
    """
    if points_per_hour is None or points_per_hour < 2 or len(profile) < 2:
        return profile

    import numpy as np
    pts   = sorted(profile, key=lambda s: s.get("hour", 0))
    hours = [float(s["hour"]) for s in pts]

    # Fábrica de interpoladores: PCHIP (monótono) si hay scipy, lineal si no.
    try:
        from scipy.interpolate import PchipInterpolator

        def _mk(ys):
            fn = PchipInterpolator(hours, ys, extrapolate=False)
            return lambda x: float(fn(x))
    except Exception:                                    # pragma: no cover
        def _mk(ys):
            return lambda x: float(np.interp(x, hours, ys))

    G_of = _mk([float(s.get("Gpoa", 0.0)) for s in pts])

    # Recálculo físico solo si están los drivers primarios en TODOS los puntos.
    have_meteo = all(("T2M" in s and "WS" in s) for s in pts)
    if have_meteo:
        from models.thermal import cell_temperature
        Tair_of = _mk([float(s.get("T2M", 25.0)) for s in pts])
        Wind_of = _mk([float(s.get("WS",  1.0)) for s in pts])
    else:
        Tcell_of = _mk([float(s.get("Tcell", 25.0)) for s in pts])

    def _recompute(h: float) -> dict:
        G = max(G_of(h), 0.0)                            # clip de seguridad
        if have_meteo:
            Tair  = Tair_of(h)
            wind  = max(Wind_of(h), 0.0)
            Tcell = cell_temperature(G, Tair, model.p.noct,
                                     wind=wind, model=thermal_model)
        else:
            Tcell = Tcell_of(h)
        mpp = model.get_mpp(G_poa=G, T_cell=Tcell, Ns_arr=Ns_arr, Np_arr=Np_arr,
                            V_max_hw=V_max, I_max_hw=I_max)
        # label "{h:.4g}h": horas enteras quedan "13h" (compat. escalera), las
        # fraccionales "13.5h"/"13.17h" — paper_figs._hour_num parsea ambos.
        step = {
            "hour":  round(float(h), 4),
            "label": f"{h:.4g}h",
            "V_set": mpp.Vmp,
            "I_set": mpp.Imp,
            "P_set": mpp.Pmp,
            "Gpoa":  round(float(G),     0),
            "Tcell": round(float(Tcell), 1),
        }
        if attach_curve and mpp.Pmp > 0:
            curve = _hourly_curve(model, G, Tcell, Ns_arr, Np_arr,
                                  curve_pts, V_max, I_max)
            if curve:
                step.update(curve)
        return step

    out: list[dict] = []
    for a, b in zip(pts, pts[1:]):
        ha, hb = a["hour"], b["hour"]
        for k in range(points_per_hour):           # incluye el extremo izq. (k=0)
            f = k / points_per_hour
            out.append(_recompute(ha + f * (hb - ha)))
    out.append(_recompute(pts[-1]["hour"]))
    return out


def summary(profile: list[dict], dt_ms: int) -> dict:
    """
    Compute profile summary metrics for display in the HMI.

    Returns a dict with:
        peak_P   — peak power [W]
        peak_V   — peak voltage [V]
        peak_I   — peak current [A]
        total_E  — estimated total energy [kWh]
        lab_s    — profile duration in the lab [s]
        n_steps  — number of steps
    """
    if not profile:
        return {
            "peak_P": 0, "peak_V": 0, "peak_I": 0,
            "total_E": 0, "lab_s": 0, "n_steps": 0,
        }

    return {
        "peak_P":  max(d["P_set"] for d in profile),
        "peak_V":  max(d["V_set"] for d in profile),
        "peak_I":  max(d["I_set"] for d in profile),
        "total_E": round(sum(d["P_set"] for d in profile) / 1000.0, 3),
        "lab_s":   round(len(profile) * dt_ms / 1000.0, 1),
        "n_steps": len(profile),
    }
