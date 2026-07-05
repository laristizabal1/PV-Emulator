"""
hmi/callbacks/
==============
Dash callbacks grouped by functional domain.
Each module exposes a register(app) function.

    nasa_cb        — location + NASA POWER download        (4 callbacks)
    array_cb       — PV array sliders + verification        (2 callbacks)
    profile_cb     — profile computation + export CSV       (2 callbacks)
    scpi_cb        — real control of the EA-PS 10060-170    (5 callbacks)
    summary_cb     — summary tab + top header               (2 callbacks)
    diagnostics_cb — session diagnostics viewer
"""
from hmi.callbacks import (nasa_cb, array_cb, profile_cb, scpi_cb,
                           summary_cb, diagnostics_cb)


def register_all(app) -> None:
    """
    Register all callbacks on the Dash app.
    Call once from app.py after creating the instance:

        import hmi.callbacks as cb
        cb.register_all(app)
    """
    nasa_cb.register(app)
    array_cb.register(app)
    profile_cb.register(app)
    scpi_cb.register(app)
    summary_cb.register(app)
    diagnostics_cb.register(app)


__all__ = [
    "nasa_cb", "array_cb", "profile_cb", "scpi_cb", "summary_cb",
    "diagnostics_cb", "register_all",
]
