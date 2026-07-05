"""
tools/probe/dl3021_check.py
===========================
PREFLIGHT connectivity check for the Rigol DL3021 (DL3000 series) over USB-TMC,
to run BEFORE the full curve-trace experiment. It answers one question: "can the
PC talk to the load?" — without ever driving it.

What it does (all READ-ONLY — never enables the input or sets a current):
  1. Reports the software stack: pyvisa, VISA backends (NI-VISA / pyvisa-py),
     libusb — so if anything is missing you know exactly what to install.
  2. Lists the VISA resources it can see.
  3. Opens the DL3021 (configured/auto-discovered resource) and exercises safe
     queries: *IDN?, SYST:ERR?, :FUNC?, INPUT?, and ONE measurement read.
  4. Prints a clear PASS / FAIL with a remediation checklist.

Run it today (instrument not connected) to confirm the software gap, and again
tomorrow at the bench (cable + NI-VISA in place) to get a green light before the
experiment.

Usage
-----
    python -m tools.probe.dl3021_check                 # full preflight
    python -m tools.probe.dl3021_check --list          # only list VISA resources
    python -m tools.probe.dl3021_check --resource "USB0::0x1AB1::0x0E11::DL3A243500940::INSTR"
"""

from __future__ import annotations

import sys
import argparse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from comm.dl3000 import (DL3000Load, open_resource_manager,          # noqa: E402
                         find_rigol_resource, DEFAULT_RESOURCE,
                         PYVISA_AVAILABLE)

OK   = "PASS"
BAD  = "FAIL"
WARN = "WARN"


def _line(tag: str, msg: str):
    print(f"  [{tag}] {msg}")


def report_stack() -> bool:
    """Print the software stack status. Return True if any backend is usable."""
    print("Software stack")
    print("-" * 60)
    if not PYVISA_AVAILABLE:
        _line(BAD, "pyvisa NOT installed → pip install pyvisa pyvisa-py pyusb")
        return False
    import pyvisa
    _line(OK, f"pyvisa {pyvisa.__version__}")

    backends = []
    ni_visa = False
    for backend, label in (("@ivi", "NI-VISA (@ivi)"), ("@py", "pyvisa-py (@py)")):
        try:
            pyvisa.ResourceManager(backend)
            _line(OK, f"{label}: available")
            backends.append(label)
            if backend == "@ivi":
                ni_visa = True
        except Exception as exc:                     # noqa: BLE001
            _line(WARN, f"{label}: not available ({type(exc).__name__})")

    # libusb only matters for the @py USB-TMC path
    libusb = False
    try:
        import usb.backend.libusb1 as l1
        libusb = bool(l1.get_backend())
        _line(OK if libusb else WARN,
              "libusb backend (for @py over USB): "
              + ("found" if libusb else "missing"))
    except Exception:                                # noqa: BLE001
        _line(WARN, "pyusb/libusb not available (only needed for the @py path)")

    if not backends:
        _line(BAD, "no VISA backend usable → install NI-VISA (or Rigol "
                   "UltraSigma) for USB-TMC.")
        return False

    # USB-TMC verdict: needs EITHER NI-VISA, OR pyvisa-py + a libusb backend.
    # pyvisa-py loading alone is NOT enough to talk to a USB instrument.
    if not ni_visa and not libusb:
        _line(BAD, "USB-TMC NOT possible yet: needs NI-VISA (recommended) OR "
                   "pyvisa-py + libusb. Install one before the bench.")
        return False
    return True


def list_resources() -> list[str]:
    print("\nVISA resources")
    print("-" * 60)
    try:
        rm, backend = open_resource_manager()
    except Exception as exc:                         # noqa: BLE001
        _line(BAD, str(exc))
        return []
    try:
        res = list(rm.list_resources())
    except Exception as exc:                         # noqa: BLE001
        _line(BAD, f"list_resources failed: {exc}")
        return []
    finally:
        try:
            rm.close()
        except Exception:                            # noqa: BLE001
            pass
    if not res:
        _line(WARN, f"none found (backend {backend}). Instrument off / not on "
                    "the USB-Device port / driver missing.")
    else:
        for r in res:
            tag = "RIGOL" if "1AB1" in r.upper() else "     "
            print(f"  [{tag}] {r}")
    return res


def probe_instrument(resource: str | None) -> bool:
    """Open the load and run safe read-only queries. Return True on success."""
    print("\nInstrument probe (read-only)")
    print("-" * 60)
    load = DL3000Load(resource=resource)
    try:
        idn = load.connect()
    except Exception as exc:                         # noqa: BLE001
        _line(BAD, f"could not open the load: {type(exc).__name__}: {exc}")
        return False

    _line(OK, f"connected via {load.backend}")
    _line(OK, f"resource: {load.resource}")
    is_rigol = "RIGOL" in idn.upper() or "DL30" in idn.upper()
    _line(OK if is_rigol else WARN, f"*IDN? → {idn}")
    if not is_rigol:
        _line(WARN, "IDN does not look like a Rigol DL3000 — wrong resource?")

    try:
        _line(OK, f"mode  :FUNC?  → {load.function()}")
        _line(OK, f"input INPUT?  → {load.input_state()}  (0=off, expected)")
        v, i, p = load.measure()
        _line(OK, f"measure → V={v:.3f} V  I={i:.3f} A  P={p:.3f} W")
        err = load.error()
        _line(OK if err.startswith(("0", "+0")) else WARN, f"SYST:ERR? → {err}")
    except Exception as exc:                         # noqa: BLE001
        _line(WARN, f"a query failed: {type(exc).__name__}: {exc}")
    finally:
        load.close()
        _line(OK, "input left OFF, instrument released")
    return is_rigol


def remediation():
    print("\nIf it did NOT pass, check in order:")
    print("  1. DL3021 powered on and USB cable on the REAR USB-Device (Type-B) "
          "port — not the front host (Type-A) port.")
    print("  2. Windows enumerates it (Device Manager → look for Rigol / "
          "USB Test and Measurement Device).")
    print("  3. NI-VISA (or Rigol UltraSigma) installed → enables the @ivi "
          "backend for USB-TMC.")
    print("  4. If using pyvisa-py instead: pip install pyusb + a libusb DLL, "
          "and bind the device to WinUSB/libusbK (Zadig).")
    print(f"  5. Pin the exact resource with PV_DL3000_RESOURCE "
          f"(default: {DEFAULT_RESOURCE}).")


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:                                # noqa: BLE001
        pass

    ap = argparse.ArgumentParser(
        description="Preflight USB-TMC connectivity check for the Rigol DL3021.")
    ap.add_argument("--resource", default=None,
                    help=f"VISA resource (default: auto / {DEFAULT_RESOURCE}).")
    ap.add_argument("--list", action="store_true",
                    help="Only list VISA resources and exit.")
    args = ap.parse_args()

    print("=" * 60)
    print("DL3021 USB-TMC PREFLIGHT")
    print("=" * 60)

    stack_ok = report_stack()

    if args.list:
        list_resources()
        return 0 if stack_ok else 1

    if not stack_ok:
        remediation()
        print("\nRESULT: FAIL (software stack incomplete).")
        return 1

    res = list_resources()
    passed = probe_instrument(args.resource) if res or args.resource else False

    print("\n" + "=" * 60)
    if passed:
        print("RESULT: PASS — the PC can talk to the DL3021. Ready for the "
              "experiment.")
        print("=" * 60)
        return 0
    remediation()
    print("\nRESULT: FAIL — see the checklist above.")
    print("=" * 60)
    return 1


if __name__ == "__main__":
    sys.exit(main())
