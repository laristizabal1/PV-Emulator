"""
config/hardware.py
==================
HMI hardware constants and color palette.

Import in any module like:
    from config.hardware import V_MAX, I_MAX, DT_MIN, C

Portable configuration
----------------------
Connection parameters (serial port, source IP, Modbus port) are read from
environment variables with a default value. To run on another machine just
define the variables — without touching the code:

    PV_SERIAL_PORT  (e.g. COM4, /dev/ttyUSB0)   — source serial port
    PV_SERIAL_BAUD  (e.g. 115200)               — SCPI baudrate
    PV_MODBUS_HOST  (e.g. 0.0.0.0)              — Modbus TCP server bind
    PV_MODBUS_PORT  (e.g. 502)                  — Modbus TCP server port

They can be set in a `.env` file (loaded by the OS/IDE) or exported in the
terminal before launching the app.
"""

import os

# ── EA-PS 10060-170 electrical limits ────────────────────────────────────────
# Source: EA-PS 10000 3U manual, document 06230820_EN
V_MAX:  float = 60.0    # V  — maximum output voltage
I_MAX:  float = 170.0   # A  — maximum output current
P_MAX:  float = 5000.0  # W  — nominal maximum power
DT_MIN: int   = 200     # ms — minimum stable interval between commands
                         #      experimentally validated (doc. section VI)

# The "direct" envelope transition strategies (instant/ramp/ramp+drift/slope)
# live in tools/bench/transition_strategies.py — a bench-only research comparison.
# The adaptive command order (CURR before VOLT when current drops) is ALWAYS
# active in set_output_fast, independent of any strategy.

# ── Emulation envelope (global fallback) ─────────────────────────────────────
# Shape the source presents to the device under test (DUT) during a profile.
# The APPROPRIATE envelope depends on the DUT: in the HMI it is derived from the
# selected DUT (config/devices.py); this value is only the fallback when no DUT.
#   "cp"     — VOLT=Voc / CURR=Isc / POW=Pmp (native constant-power loop).
#              Validated in transition_bench 2026-06-10: P_err ~1 % with a
#              reference MPPT inverter (GMI120L). Requires a profile built with
#              attach_curve=True.
#   "direct" — VOLT=Vmp / CURR=Imp (rectangle, historical behavior;
#              P_err 23-34 % measured with that same reference inverter).
#   "curve"  — cp + software I(V) loop, device-agnostic (see bench).
# To empirically determine the mode for a new DUT: tools/transition_bench.
ENVELOPE_MODE: str = os.getenv("PV_ENVELOPE", "cp")

# ── Serial connection (USB/COM) ──────────────────────────────────────────────
# Override with environment variables or from the HMI/CLI.
DEFAULT_PORT:    str   = os.getenv("PV_SERIAL_PORT", "COM3")
DEFAULT_BAUD:    int   = int(os.getenv("PV_SERIAL_BAUD", "115200"))
DEFAULT_TIMEOUT: float = 2.0   # s

# ── Network connection (Modbus TCP server) ───────────────────────────────────
DEFAULT_MODBUS_HOST:  str = os.getenv("PV_MODBUS_HOST", "0.0.0.0")     # bind servidor
DEFAULT_MODBUS_PORT:  int = int(os.getenv("PV_MODBUS_PORT", "502"))

# ── HMI typography ───────────────────────────────────────────────────────────
# DM Sans for UI, JetBrains Mono for numbers/measurements/SCPI commands.
# Loaded by app.py via external_stylesheets (Google Fonts).
FONT: str = "'DM Sans', -apple-system, 'Segoe UI', sans-serif"
MONO: str = "'JetBrains Mono', 'Consolas', monospace"

# ── HMI color palette ─────────────────────────────────────────────────────────
# Approved visual system (redesign): slate header/sidebar, light content,
# dark SCPI monitor with green readouts, green accent. Historical keys are
# kept (the tab_*.py read them) and remapped to the new palette; the new keys
# (sidebar, monitor*, green, mono…) serve the header/sidebar and the dark
# monitor.
C: dict[str, str] = {
    # Backgrounds
    "bg":          "#eceff3",   # content background
    "white":       "#ffffff",
    "panel":       "#ffffff",
    # Borders
    "border":      "#dde2e9",   # card border
    "borderLight": "#eef1f5",   # soft divider
    "inputBorder": "#d5dbe3",
    # Primary accent (green)
    "accent":      "#16a34a",
    "accentDark":  "#15803d",
    "accentLight": "#ecf7f0",   # light green background (success / selected)
    "accentBg":    "#ecf7f0",
    "accentBorder":"#bfe6cd",
    "green":       "#4ade80",   # green readout of the dark monitor
    "greenDim":    "#7ee2a0",   # "connected" text on slate
    "pulse":       "#22c55e",   # live status dot
    # Text (on light)
    "text":        "#1b2430",
    "textMed":     "#3b4654",   # section labels (uppercase)
    "dim":         "#7c8794",   # secondary text
    "label":       "#9aa4b1",   # dim text / units
    # Text (on slate/dark)
    "onDark":      "#eef2f7",
    "onDarkDim":   "#8b97a6",
    "onDarkDimmer":"#6b7685",
    # Semantic
    "red":         "#dc2626",
    "redLight":    "#fdecec",
    "blue":        "#2563eb",
    "blueLight":   "#eff6ff",
    "purple":      "#7c3aed",
    "cyan":        "#06b6d4",
    "orange":      "#ea580c",
    "amber":       "#f59e0b",
    # Chart grid
    "grid":        "#eef1f5",
    "track":       "#e3e8ee",   # slider track
    # Slate (header + sidebar)
    "header":      "#1c2531",
    "sidebar":     "#1c2531",
    "sidebarLabel":"#586475",
    "sidebarText": "#9aa4b1",
    # Dark SCPI monitor
    "monitorBg":      "#161c26",
    "monitorCard":    "#1e2632",
    "monitorBorder":  "#2a3340",
    "monitorTerm":    "#0e1219",
    "monitorTermBd":  "#232c39",
}
