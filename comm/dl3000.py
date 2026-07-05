"""
comm/dl3000.py
==============
Minimal driver for the Rigol DL3000 series electronic load (DL3021) over
USB-TMC via pyvisa. Used to AUTOMATE the curve-trace sweep: the load sets each
operating point (CC mode) and measures (V, I, P) at its own terminals, while the
EA-PS holds the model curve (comm/scpi.py, envelope="curve").

Transport
---------
The DL3021 USB port is USB-TMC (NOT a virtual COM port — pyserial does not work).
It needs pyvisa + a VISA backend:
  - NI-VISA (or Rigol UltraSigma, which bundles a VISA)  → backend "@ivi"  [robust]
  - pyvisa-py + pyusb + libusb                            → backend "@py"   [fallback]
open_resource_manager() tries @ivi first, then @py.

Resource name (from the bench unit, see the colleague's guide script):
    USB0::0x1AB1::0x0E11::DL3A243500940::INSTR
Rigol vendor id = 0x1AB1. The serial (DL3A...) differs per unit, so connect()
auto-discovers any Rigol USB instrument if no explicit resource is given. Set
PV_DL3000_RESOURCE to pin a specific one.

SCPI verified on the DL3021 (Rigol DL3000 programming guide + guide script):
    *IDN?                       identification
    SYST:ERR?                   error queue
    :FUNC CURR / :FUNC?         constant-current mode / query
    :CURR <A> / :CURR?          CC setpoint / query
    INPUT ON|OFF / INPUT?       input on/off / state ("0"/"1")
    MEAS:VOLT? / MEAS:CURR? / MEAS:POW?   measurements at the load terminals
    :MEAS:VOLT?;CURR?;POW?      combined measurement

Read-only use (identification + measurements) is safe with the input OFF. The
control methods (set_cc_mode/set_current/input_on) are for the experiment, not
the preflight check.
"""

from __future__ import annotations

import os

try:
    import pyvisa
    PYVISA_AVAILABLE = True
except ImportError:
    PYVISA_AVAILABLE = False

RIGOL_VENDOR_ID = "0x1AB1"
# Default resource of the bench unit; override with PV_DL3000_RESOURCE.
DEFAULT_RESOURCE = os.environ.get(
    "PV_DL3000_RESOURCE", "USB0::0x1AB1::0x0E11::DL3A243500940::INSTR")


def open_resource_manager():
    """
    Return (ResourceManager, backend_label). Prefer NI-VISA ("@ivi") which is the
    robust path for USB-TMC on Windows; fall back to pyvisa-py ("@py").

    Raises RuntimeError if pyvisa is missing or no backend can be loaded, with a
    message listing what failed so the caller can tell the user what to install.
    """
    if not PYVISA_AVAILABLE:
        raise RuntimeError(
            "pyvisa is not installed. Run: pip install pyvisa pyvisa-py pyusb")
    errors = []
    for backend, label in (("@ivi", "NI-VISA (@ivi)"), ("@py", "pyvisa-py (@py)")):
        try:
            rm = pyvisa.ResourceManager(backend)
            return rm, label
        except Exception as exc:                     # noqa: BLE001
            errors.append(f"{label}: {type(exc).__name__}: {exc}")
    raise RuntimeError(
        "No VISA backend could be loaded:\n  " + "\n  ".join(errors) +
        "\nInstall NI-VISA (or Rigol UltraSigma) for USB-TMC, or set up "
        "pyvisa-py + libusb.")


def find_rigol_resource(rm, prefer: str | None = None) -> str | None:
    """
    Pick the VISA resource to use. If `prefer` is given it is returned as-is
    (the instrument may be openable even if list_resources did not enumerate it).
    Otherwise the first Rigol USB instrument (vendor 0x1AB1) is returned, else the
    first resource found, else None.
    """
    if prefer:
        return prefer
    try:
        res = list(rm.list_resources())
    except Exception:                                # noqa: BLE001
        res = []
    rigol = [r for r in res if "1AB1" in r.upper()]
    if rigol:
        return rigol[0]
    return res[0] if res else None


class DL3000Load:
    """Rigol DL3000 (DL3021) electronic load over USB-TMC (pyvisa)."""

    def __init__(self, resource: str | None = None, timeout_ms: int = 5000):
        self.resource   = resource or DEFAULT_RESOURCE
        self.timeout_ms = timeout_ms
        self.backend    = None
        self._rm        = None
        self._inst      = None

    # ── Connection ────────────────────────────────────────────────────────────

    def connect(self, autodiscover: bool = True) -> str:
        """
        Open the load and return its *IDN?. If `autodiscover` and the configured
        resource is not present, fall back to the first Rigol USB instrument.
        """
        self._rm, self.backend = open_resource_manager()
        target = self.resource
        if autodiscover:
            try:
                present = list(self._rm.list_resources())
            except Exception:                        # noqa: BLE001
                present = []
            if target not in present:
                target = find_rigol_resource(self._rm, prefer=None) or target
        self._inst = self._rm.open_resource(target)
        self._inst.timeout = self.timeout_ms
        self.resource = target
        return self.idn()

    @property
    def connected(self) -> bool:
        return self._inst is not None

    def close(self):
        """Disable the input (safety) and release the instrument."""
        if self._inst is not None:
            try:
                self.input_off()
            except Exception:                        # noqa: BLE001
                pass
            try:
                self._inst.close()
            except Exception:                        # noqa: BLE001
                pass
            self._inst = None
        if self._rm is not None:
            try:
                self._rm.close()
            except Exception:                        # noqa: BLE001
                pass
            self._rm = None

    # ── Raw I/O ───────────────────────────────────────────────────────────────

    def write(self, cmd: str):
        self._inst.write(cmd)

    def query(self, cmd: str) -> str:
        return self._inst.query(cmd).strip()

    # ── Read-only (safe with input OFF) ─────────────────────────────────────────

    def idn(self) -> str:
        return self.query("*IDN?")

    def error(self) -> str:
        return self.query("SYST:ERR?")

    def function(self) -> str:
        return self.query(":FUNC?")

    def input_state(self) -> str:
        return self.query("INPUT?")

    def measure(self) -> tuple[float, float, float]:
        """Return (V, I, P) measured at the load terminals."""
        try:
            # Fully qualify each node: on the DL3021 the compound ";POW?" resolves
            # to the POWer setpoint, not MEAS:POW? — giving a constant bogus power.
            raw = self.query(":MEAS:VOLT?;:MEAS:CURR?;:MEAS:POW?")
            parts = [p for p in raw.replace(";", " ").replace(",", " ").split()]
            v, i, p = (float(parts[0]), float(parts[1]), float(parts[2]))
            return v, i, p
        except Exception:                            # noqa: BLE001
            v = float(self.query("MEAS:VOLT?"))
            i = float(self.query("MEAS:CURR?"))
            p = float(self.query("MEAS:POW?"))
            return v, i, p

    # ── Control (for the experiment — NOT used by the preflight check) ──────────

    def set_cc_mode(self):
        self.write(":FUNC CURR")

    def set_current(self, amps: float):
        self.write(f":CURR {float(amps):.4f}")

    def get_current_setpoint(self) -> float:
        return float(self.query(":CURR?"))

    def set_cr_mode(self):
        self.write(":FUNC RES")

    def set_resistance(self, ohms: float):
        self.write(f":RES {float(ohms):.4f}")

    def set_resistance_range(self, ohms_fs: float):
        """CR full-scale range. The DL3021 couples it to the voltage range:
        15 Ω ↔ 10 V input, 15000 Ω ↔ 150 V input. A day whose operating voltage
        exceeds 10 V (peak ≈ 19 V) MUST use the 15000 Ω range."""
        self.write(f":RES:RANG {float(ohms_fs):.0f}")

    def set_cp_mode(self):
        self.write(":FUNC POW")

    def set_power(self, watts: float):
        self.write(f":POW {float(watts):.4f}")

    def set_cv_mode(self):
        # NB: ":FUNC VOLT" returns -220 Parameter error on the DL3021 (unlike
        # ":FUNC CURR" which works). The accepted CV form is ":SOUR:FUNC VOLT".
        self.write(":SOUR:FUNC VOLT")

    def set_voltage(self, volts: float):
        self.write(f":VOLT {float(volts):.4f}")

    def input_on(self):
        self.write("INPUT ON")

    def input_off(self):
        self.write("INPUT OFF")
