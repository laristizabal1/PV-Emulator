"""
config/modules_catalog.py
==========================
Catalog of PV modules available in the HMI.

All modules use panel_from_datasheet() from models/panel_factory.py, which
auto-detects whether the KI/KV coefficients are in:
  - Relative convention (%/C)  e.g. KI=0.04, KV=-0.25
  - Absolute convention (A/C)  e.g. KI=0.00675, KV=-0.14442

To add a new module: add an entry to CATALOG with the raw datasheet values.
You do not need to convert the coefficients.

Required fields:
    label    — display name in the Dropdown
    Isc      [A]    — short-circuit current at STC
    Voc      [V]    — open-circuit voltage at STC
    Imp      [A]    — current at MPP at STC
    Vmp      [V]    — voltage at MPP at STC
    KI       [%/C or A/C] — current temperature coef. (auto-detected)
    KV       [%/C or V/C] — voltage temperature coef. (auto-detected, negative)
    Ns       [—]    — series cells (total physical count from the datasheet)
    noct     [C]    — nominal operating temperature

Note on Ns for half-cell modules:
    The diode models auto-detect half-cell modules (Voc/Ns < 0.50 V) and compute
    Ns_eff internally. Always use the total Ns from the datasheet.
"""

# ── Catalog ───────────────────────────────────────────────────────────────────
CATALOG: dict[str, dict] = {

    "renogy_rng_100d_ss": {
        "label": "Renogy RNG-100D-SS — 100 W mono-Si (36 cells)",
        "Isc":    5.21,
        "Voc":   24.30,
        "Imp":    4.91,
        "Vmp":   20.40,
        "KI":     0.05,      # %/C — relative convention (auto-detected)
        "KV":    -0.28,      # %/C — relative convention (auto-detected)
        "Ns":    36,
        "noct":  47.0,
    },

    "perc_550w": {
        "label": "PERC 550 W (half-cell, 144 cells) — Colombia reference",
        "Isc":   13.50,
        "Voc":   49.80,
        "Imp":   13.35,
        "Vmp":   41.20,
        "KI":     0.00675,   # A/C — absolute convention
        "KV":    -0.14442,   # V/C — absolute convention
        "Ns":   144,
        "noct":  45.0,
    },

    "kyocera_kc200gt": {
        "label": "Kyocera KC200GT — 200 W multi-Si",
        "Isc":   8.21,
        "Voc":   32.90,
        "Imp":   7.61,
        "Vmp":   26.30,
        "KI":    0.00318,    # A/C — absolute convention
        "KV":   -0.12300,    # V/C — absolute convention
        "Ns":   54,
        "noct":  47.0,
    },

    "canadian_cs6k_300": {
        "label": "Canadian Solar CS6K-300MS — 300 W mono-Si",
        "Isc":   8.93,
        "Voc":   38.20,
        "Imp":   8.41,
        "Vmp":   31.00,
        "KI":    0.00446,    # A/C — absolute convention
        "KV":   -0.11460,    # V/C — absolute convention
        "Ns":   60,
        "noct":  45.0,
    },

    "jinko_eagle_400": {
        "label": "Jinko Eagle 400 W mono PERC (half-cell)",
        "Isc":   10.46,
        "Voc":   49.52,
        "Imp":    9.89,
        "Vmp":   40.46,
        "KI":    0.00418,    # A/C — absolute convention
        "KV":   -0.14856,    # V/C — absolute convention
        "Ns":  120,
        "noct":  45.0,
    },

    "trina_vertex_510": {
        "label": "Trina Vertex 510 W (half-cell, 132 cells)",
        "Isc":   12.42,
        "Voc":   52.10,
        "Imp":   11.72,
        "Vmp":   43.50,
        "KI":     0.04,      # %/C — relative convention (auto-detected)
        "KV":    -0.25,      # %/C — relative convention (auto-detected)
        "Ns":   132,
        "noct":  45.0,
    },

    "custom": {
        "label": "Custom — enter parameters manually",
        "Isc":   13.50,
        "Voc":   47.00,
        "Imp":   13.00,
        "Vmp":   39.00,
        "KI":     0.00500,   # A/C — absolute convention
        "KV":    -0.13000,   # V/C — absolute convention
        "Ns":   144,
        "noct":  45.0,
    },
}

CUSTOM_MODULE_KEY:  str = "custom"
DEFAULT_MODULE_KEY: str = "renogy_rng_100d_ss"


def get_dropdown_options() -> list[dict]:
    """Option list for the modules dcc.Dropdown."""
    return [{"label": v["label"], "value": k} for k, v in CATALOG.items()]


def get_params(key: str) -> dict:
    """Raw catalog dict for the given module."""
    return CATALOG.get(key, CATALOG[DEFAULT_MODULE_KEY])


def to_module_params(key: str):
    """
    Convert the catalog entry into ModuleParams using panel_from_datasheet.
    Auto-detects the KI/KV coefficient convention.
    """
    from models.panel_factory import panel_from_datasheet
    d = get_params(key)
    return panel_from_datasheet(
        Isc  = d["Isc"],
        Voc  = d["Voc"],
        Imp  = d["Imp"],
        Vmp  = d["Vmp"],
        KI   = d["KI"],
        KV   = d["KV"],
        Ns   = d["Ns"],
        noct = d["noct"],
        # coefficients_in_percent=None -> auto-detect
    )