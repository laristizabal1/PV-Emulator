"""
Panel Factory — Build ModuleParams from any commercial PV datasheet.

Commercial datasheets express temperature coefficients in two conventions:

  ┌─────────────────────┬────────────────────────────┬──────────────┐
  │ Datasheet notation  │ Example (Trina Vertex 510W)│ Convention   │
  ├─────────────────────┼────────────────────────────┼──────────────┤
  │ αIsc = +0.04 %/°C   │ KI = 0.0004 × 12.42 A     │ Relative (%) │
  │ βVoc = −0.25 %/°C   │ KV = −0.0025 × 52.1 V     │ Relative (%) │
  ├─────────────────────┼────────────────────────────┼──────────────┤
  │ KI = +0.00675 A/°C  │ directly usable            │ Absolute     │
  │ KV = −0.14442 V/°C  │ directly usable            │ Absolute     │
  └─────────────────────┴────────────────────────────┴──────────────┘

This module auto-detects which convention is used and converts to the
absolute form required by ModuleParams (A/°C and V/°C).
"""

from .base import ModuleParams


def panel_from_datasheet(
    Isc: float,
    Voc: float,
    Imp: float,
    Vmp: float,
    KI: float,
    KV: float,
    Ns: int,
    noct: float = 45.0,
    coefficients_in_percent: bool | None = None,
) -> ModuleParams:
    """Create a ModuleParams from raw datasheet values.

    Parameters
    ----------
    Isc, Voc, Imp, Vmp : float
        STC electrical parameters (A, V).
    KI, KV : float
        Temperature coefficients of Isc and Voc.
    Ns : int
        Number of cells in the datasheet (total physical count).
    noct : float
        Nominal Operating Cell Temperature [°C].
    coefficients_in_percent : bool or None
        If True  → KI is %/°C, KV is %/°C (e.g. 0.04, -0.25)
        If False → KI is A/°C, KV is V/°C (e.g. 0.00675, -0.14442)
        If None  → auto-detect using |KI|: values ≥ 0.01 are %/°C.
                   (KI in A/°C is typically < 0.01; in %/°C it is 0.03–0.06)

    Returns
    -------
    ModuleParams with KI in A/°C and KV in V/°C.
    """
    # ── Determine convention ────────────────────────────────────────
    if coefficients_in_percent is None:
        # Auto-detect from KI (unambiguous: A/°C < 0.01, %/°C ≥ 0.01)
        is_percent = abs(KI) >= 0.01
    else:
        is_percent = coefficients_in_percent

    if is_percent:
        KI_abs = KI / 100.0 * Isc
        KV_abs = KV / 100.0 * Voc
    else:
        KI_abs = KI
        KV_abs = KV

    return ModuleParams(
        Isc_n=Isc,
        Voc_n=Voc,
        Imp_n=Imp,
        Vmp_n=Vmp,
        KI=KI_abs,
        KV=KV_abs,
        Ns=Ns,
        noct=noct,
    )

