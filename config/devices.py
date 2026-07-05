"""
config/devices.py
=================
Catalog of the platform's DEVICES UNDER TEST (DUT).

The EA-PS 10060-170 emulates a photovoltaic source (I-V curve via pvlib) and the
power device to be tested is connected at its output. This module describes the
supported DUT types and the metadata the rest of the platform needs to adapt to
each one:

  - which emulation ENVELOPE to present by default (starting point),
  - which MODES to compare when empirically DETERMINING the appropriate
    operating mode for the device (see tools/bench/transition_bench.py),
  - whether the device has its own operating-point TRACKING (MPPT) — this defines
    which analysis metrics make sense (MPPT efficiency vs setpoint-tracking
    fidelity),
  - the start-up power THRESHOLD used to discard the transient / dead zone in the
    post-analysis.

Usage pattern (mirror of config/modules_catalog.py):
    from config.devices import get_device, get_dropdown_options, DEFAULT_DEVICE_KEY

    dev = get_device("mppt_inverter")
    envelope = dev.envelope           # recommended starting envelope
    modes    = dev.candidate_modes    # search space for the appropriate mode

To add a new DUT: add an entry to CATALOG. It does not require touching the SCPI
control — the DUT only SELECTS among the existing envelopes
(direct / cp / curve) of comm/scpi.run_profile().
"""

from dataclasses import dataclass, field


@dataclass
class DeviceProfile:
    """Profile of a device under test (DUT)."""
    key:    str
    label:  str
    has_mppt: bool              # does the DUT seek its own operating point?
    envelope: str              # recommended STARTING envelope: cp/curve/direct
    candidate_modes: list[str] # modes to compare to determine the appropriate one
    p_min_w: float             # W — start-up threshold to discard the transient
    notes:  str = ""


# Valid modes = strategies from tools/bench/transition_bench.py
#   instant | ramp | ramp+drift | slope | cp | curve
# (the actual envelopes are direct/cp/curve; instant/ramp/ramp+drift/slope are
#  variants of the "direct" envelope with different transition dynamics).

# ── DUT catalog ───────────────────────────────────────────────────────────────
# Validation status: only "mppt_inverter" (cp envelope) and "eload" (curve
# envelope) were validated with real hardware. "generic" is an unvalidated
# bring-your-own fallback. Other device types (DC-DC converter, resistive bank)
# were not tested and are intentionally NOT listed to avoid overstating support.
CATALOG: dict[str, DeviceProfile] = {

    "mppt_inverter": DeviceProfile(
        key      = "mppt_inverter",
        label    = "MPPT inverter",
        has_mppt = True,
        # cp validated at ~1 % power error with the reference inverter
        # (GMI120L) in transition_bench 2026-06-10.
        envelope = "cp",
        candidate_modes = ["cp", "curve", "slope"],
        p_min_w  = 5.0,
        notes    = "Seeks the MPP on its own. Key metric: MPPT efficiency "
                   "(η = P_dc/P_set). The cp envelope guarantees each hour's "
                   "power; curve also presents the real I-V knee.",
    ),

    "eload": DeviceProfile(
        key      = "eload",
        label    = "Programmable electronic load",
        has_mppt = False,
        # In CV/CC the load sweeps the operating point: curve presents the true
        # I(V) point by point (basis of the curve-fidelity validation).
        envelope = "curve",
        candidate_modes = ["curve", "cp"],
        p_min_w  = 1.0,
        notes    = "Reference instrument to trace the emulator's I-V curve "
                   "(see tools/probe/curve_trace_manual.py). Metric: curve fidelity, "
                   "not MPPT efficiency.",
    ),

    "generic": DeviceProfile(
        key      = "generic",
        label    = "Generic / no assumptions",
        has_mppt = False,
        envelope = "curve",
        candidate_modes = ["curve", "cp", "direct", "slope"],
        p_min_w  = 1.0,
        notes    = "Bring-your-own uncharacterized DUT — NOT validated against "
                   "specific hardware. Starts on the curve envelope; sweep the "
                   "candidate modes with tools/bench/transition_bench.py to find "
                   "the best-fidelity mode for your device.",
    ),
}

DEFAULT_DEVICE_KEY: str = "mppt_inverter"


def get_dropdown_options() -> list[dict]:
    """Option list for the DUT dcc.Dropdown."""
    return [{"label": d.label, "value": k} for k, d in CATALOG.items()]


def get_device(key: str) -> DeviceProfile:
    """DeviceProfile for the given DUT (falls back to the default if missing)."""
    return CATALOG.get(key, CATALOG[DEFAULT_DEVICE_KEY])
