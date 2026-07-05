"""
tools/probe/modbus_monitor.py
=============================
Standalone diagnostic tool — a Modbus TCP client that monitors in the console
the measurements published by the SCPI↔Modbus bridge of the EA-PS 10060-170
source. Refresh every 500 ms. Ctrl+C to exit.

It is not part of the app runtime; it is a verification script that connects to
the Modbus TCP server raised by `comm/bridge.py` and shows the registers from
the perspective of an external client (e.g. a SCADA).

Usage:
    python tools/probe/modbus_monitor.py                 # 127.0.0.1 : PV_MODBUS_PORT
    python tools/probe/modbus_monitor.py --host 192.168.1.10 --port 502
    python tools/probe/modbus_monitor.py 192.168.1.10 502   # positional (compat)

The default host is localhost because the bridge usually runs on the same
machine as the HMI. The default port is taken from the centralized config
(config.hardware.DEFAULT_MODBUS_PORT / PV_MODBUS_PORT variable).
"""

import argparse
import os
import struct
import sys
import time
from pathlib import Path

# Allows running the script directly (python tools/probe/modbus_monitor.py) by adding
# the project root to the path to import `config`.
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from config.hardware import DEFAULT_MODBUS_PORT  # noqa: E402

REFRESH = 0.5   # seconds between reads
_DEFAULT_CLIENT_HOST = "127.0.0.1"   # the bridge usually runs on the same machine


def regs_to_float(hi: int, lo: int) -> float:
    """Rebuild a big-endian Float32 from two holding registers."""
    return struct.unpack(">f", struct.pack(">HH", hi, lo))[0]


def clear() -> None:
    os.system("cls" if os.name == "nt" else "clear")


def parse_args(argv=None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Modbus TCP console monitor for the EA-PS 10060-170 bridge.")
    p.add_argument("host", nargs="?", default=None,
                   help=f"Bridge IP (def. {_DEFAULT_CLIENT_HOST})")
    p.add_argument("port", nargs="?", type=int, default=None,
                   help=f"Modbus port (def. {DEFAULT_MODBUS_PORT})")
    p.add_argument("--host", dest="host_opt", default=None,
                   help="Bridge IP (alternative to positional)")
    p.add_argument("--port", dest="port_opt", type=int, default=None,
                   help="Modbus port (alternative to positional)")
    args = p.parse_args(argv)
    # Precedence: --flag > positional > environment variable > default
    args.host = (args.host_opt or args.host
                 or os.getenv("PV_MODBUS_CLIENT_HOST") or _DEFAULT_CLIENT_HOST)
    args.port = (args.port_opt or args.port or DEFAULT_MODBUS_PORT)
    return args


def main(argv=None) -> int:
    args = parse_args(argv)
    host, port = args.host, args.port

    try:
        from pymodbus.client import ModbusTcpClient
    except ImportError:
        print("Instala pymodbus:  pip install pymodbus>=3.12")
        return 1

    print(f"Connecting to {host}:{port} ...")
    c = ModbusTcpClient(host, port=port)
    if not c.connect():
        print(f"Could not connect to {host}:{port}")
        return 1

    print("Connected. Ctrl+C to exit.\n")
    time.sleep(0.3)

    try:
        while True:
            t0 = time.perf_counter()

            rr = c.read_holding_registers(address=0, count=13, slave=1)
            if rr.isError():
                print(f"Error reading registers: {rr}")
                time.sleep(1)
                continue

            r = rr.registers
            V    = regs_to_float(r[0],  r[1])
            I    = regs_to_float(r[2],  r[3])
            P    = regs_to_float(r[4],  r[5])
            Vset = regs_to_float(r[6],  r[7])
            Iset = regs_to_float(r[8],  r[9])
            out  = r[10]
            err  = r[12]

            elapsed_ms = (time.perf_counter() - t0) * 1000

            clear()
            print("=" * 40)
            print("  EA-PS 10060-170  —  Modbus TCP Monitor")
            print(f"  {host}:{port}   refresh {int(REFRESH*1000)} ms")
            print("=" * 40)
            print(f"  V  measured: {V:>8.3f} V")
            print(f"  I  measured: {I:>8.3f} A")
            print(f"  P  measured: {P:>8.1f} W")
            print("-" * 40)
            print(f"  V  setpoint: {Vset:>8.3f} V")
            print(f"  I  setpoint: {Iset:>8.3f} A")
            print("-" * 40)
            print(f"  Output     : {'ON' if out else 'OFF'}")
            print(f"  SCPI error : {err}")
            print(f"  Latency    : {elapsed_ms:.1f} ms")
            print("=" * 40)

            wait = REFRESH - (time.perf_counter() - t0)
            if wait > 0:
                time.sleep(wait)

    except KeyboardInterrupt:
        print("\nMonitor stopped.")
    finally:
        c.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
