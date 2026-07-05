"""
tools/probe/meas_probe.py
=========================
SCPI measurement probe for the EA-PS 10060-170 source.

Diagnoses why the EAMonitor reads V/I/P as zero: captures the RAW RESPONSE of
each measurement-command variant, both with query() (2 s timeout) and with
query_fast() (150 ms timeout, the one the monitor uses). This shows:
  • whether the instrument responds at all and in what format ("12.34 V", "12.34", …),
  • whether query_fast() returns "" because 150 ms is too short (→ the monitor parses 0),
  • which measurement command is correct for this instrument/firmware.

The DC output stays OFF: it is safe to run without a load. Voltage/current
readings will be ~0 with the output off, but what matters here is the FORMAT and
LATENCY of the response, not the value.

Usage:
    python tools/probe/meas_probe.py             # auto-detected port
    python tools/probe/meas_probe.py --port COM4  # explicit port
    python tools/probe/meas_probe.py --on         # turns output on (with a load!)
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from comm.scpi import SCPIController, autodetect_port
from config.hardware import DEFAULT_PORT, DEFAULT_BAUD

# Measurement commands to test (order = monitor preference)
MEAS_COMMANDS = [
    "MEAS:ALL?",
    "MEAS:VOLT?",
    "MEAS:CURR?",
    "MEAS:POW?",
    "MEAS:SCAL:VOLT?",
    "MEAS:SCAL:CURR?",
]


def _probe(ctrl: SCPIController, cmd: str, reps: int = 3) -> None:
    """Print raw response and latency with query (2 s) and query_fast (150 ms)."""
    print(f"\n── {cmd} ──")
    for label, fn in (
        ("query      (2.0 s)", lambda: ctrl.query(cmd)),
        ("query_fast (150 ms)", lambda: ctrl.query_fast(cmd)),
    ):
        for r in range(reps):
            t0   = time.perf_counter()
            raw  = fn()
            dt   = (time.perf_counter() - t0) * 1000
            shown = repr(raw) if raw else "'' (EMPTY)"
            print(f"  {label}  #{r+1}  {dt:6.1f} ms  →  {shown}")


def main() -> int:
    p = argparse.ArgumentParser(description="EA-PS MEAS command probe")
    p.add_argument("--port", default=None, help="Serial port (def. autodetect)")
    p.add_argument("--reps", type=int, default=3, help="Reads per command")
    p.add_argument("--on", action="store_true",
                   help="Turn the DC output on (use only with a load connected)")
    args = p.parse_args()

    port = args.port or autodetect_port(DEFAULT_PORT)
    if port is None:
        print("ERROR: no serial port detected.")
        return 1

    ctrl = SCPIController(port=port, baud=DEFAULT_BAUD)
    try:
        idn = ctrl.connect()
    except Exception as exc:
        print(f"ERROR connecting on {port}: {exc}")
        return 1

    print(f"Connected on {port}: {idn.strip()}")

    if args.on:
        print("\n[!] Turning DC output on (make sure a load is connected).")
        ctrl.set_output_fast(5.0, 1.0, on=True)
        time.sleep(0.5)
    else:
        print("\nDC output OFF — evaluating format/latency, not the value.")

    try:
        for cmd in MEAS_COMMANDS:
            _probe(ctrl, cmd, reps=args.reps)
    finally:
        ctrl.disconnect()

    print("\nReading: if query() returns a number but query_fast() returns "
          "'' (EMPTY), the monitor's 150 ms timeout is too short for this "
          "instrument → raise it in comm/monitor.py (query_fast timeout).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
