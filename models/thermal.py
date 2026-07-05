# models/thermal.py
"""
Cell-temperature models.

  faiman      — Faiman (2008), delegated to pvlib.temperature.faiman. Uses wind
                speed (NASA POWER WS2M), more physical than NOCT. **DEFAULT** of
                the platform (pipeline.profile.build).
  noct        — simple textbook NOCT model (no exact pvlib equivalent; kept
                manual). Alternative.

Use `cell_temperature(..., model=...)` to select; build() exposes it via the
`thermal_model` parameter (default "faiman").
"""

from __future__ import annotations


def noct_cell_temp(G_poa: float, T_amb: float, noct: float) -> float:
    """
    NOCT (Normal Operating Cell Temperature) model.
    Estimates the cell temperature from the plane-of-array irradiance and the
    ambient temperature.

    Equation (8) of the project document.

    Parameters
    ----------
    G_poa : effective POA irradiance [W/m²]
    T_amb : ambient temperature [°C]
    noct  : nominal operating cell temperature of the module [°C]
             (provided by the manufacturer, typically 42–48 °C)

    Returns
    -------
    Tcell : estimated cell temperature [°C]
    """
    return T_amb + G_poa * ((noct - 20.0) / 800.0)


def faiman_cell_temp(G_poa: float, T_amb: float,
                     wind: float,
                     u0: float = 25.0,
                     u1: float = 6.84) -> float:
    """
    Faiman model — more accurate than NOCT because it includes wind speed.

    Delegates to ``pvlib.temperature.faiman`` (NREL/Sandia reference
    implementation). The formula T_amb + G_poa / (u0 + u1·wind) is identical to
    the previous in-house version (verified max difference = 0 over a G/T/wind
    grid); using pvlib removes the hand-rolled model as a point of doubt.

    Defaults u0=25.0, u1=6.84 are pvlib's (glass/cell/polymer, open rack).
    """
    from pvlib.temperature import faiman as _faiman   # lazy: avoids import cost
    return float(_faiman(G_poa, T_amb, wind, u0=u0, u1=u1))


def cell_temperature(G_poa: float, T_amb: float, noct: float,
                     wind: float = 1.0, model: str = "faiman") -> float:
    """
    Dispatch cell-temperature estimation by model name.

    model="faiman" → faiman_cell_temp (pvlib, uses wind speed). DEFAULT.
    model="noct"   → noct_cell_temp (simple NOCT, no wind).
    """
    if model == "noct":
        return noct_cell_temp(G_poa, T_amb, noct)
    if model == "faiman":
        return faiman_cell_temp(G_poa, T_amb, wind)
    raise ValueError(f"unknown thermal model {model!r} (use 'noct' or 'faiman')")
