"""
tools/probe/quick_profile_test.py
=================================
Quick diagnostic test of the PV profile on the EA-PS 10060-170 source.

Extracts 10 representative steps of the daily average profile (sunrise, noon,
sunset) and runs them with a short dt_ms to confirm in ~30-60 s that:

  1. The OUTP ON fix works: the output is not re-enabled on every step.
  2. The regulation mode alternates CV/CC depending on the operating point.
  3. V_meas and I_meas follow V_set/I_set with a reasonable error (< 10%).

The output is a console table — no files, no charts.

Usage:
    python tools/probe/quick_profile_test.py                 # 10 steps, dt=3000 ms
    python tools/probe/quick_profile_test.py --steps 20 --dt-ms 5000
    python tools/probe/quick_profile_test.py --port COM3
    python tools/probe/quick_profile_test.py --dry-run       # without connecting the source
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from config.hardware import DEFAULT_PORT, DEFAULT_BAUD, V_MAX, I_MAX
from config.modules_catalog import to_module_params, get_params, DEFAULT_MODULE_KEY
from models.single_diode import SingleDiodeModel
from pipeline.nasa_power import fetch as nasa_fetch, DEFAULT_CACHE_DIR
from pipeline.profile import build as build_profile, apply_strategy
from comm.scpi import SCPIController, autodetect_port
from tools.bench import transition_strategies

ORIGINAL_LAT  = 3.45
ORIGINAL_LON  = -76.53
ORIGINAL_START = "20240315"
ORIGINAL_END   = "20250317"


def _sample_profile(profile: list[dict], n: int) -> list[dict]:
    """
    Extract n steps spread across the day: first the steps with power > 0 in
    order, then distribute them evenly.
    """
    day_steps = [s for s in profile if s["P_set"] > 0]
    if not day_steps:
        return profile[:n]
    if len(day_steps) <= n:
        return day_steps
    step = max(1, len(day_steps) // n)
    return day_steps[::step][:n]


def main() -> int:
    p = argparse.ArgumentParser(
        description="Quick PV profile diagnostic test (10 steps)",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    p.add_argument("--steps",  type=int, default=10,
                   help="Number of steps to run")
    p.add_argument("--dt-ms",  type=int, default=5000,
                   help="ms per step. Use >=10000 if the MPPT does not stabilize V_meas")
    p.add_argument("--rapid-ms", type=int, default=0,
                   help="If >0, resend VOLT/CURR every N ms within the step. "
                        "Use >=200 with --slope-pct. 0=disabled.")
    p.add_argument("--slope-pct", type=int, default=0,
                   help="If >0, emulate the PV curve knee slope: in the zone "
                        "[slope_pct%%·V_set, V_set] the current decreases "
                        "linearly. Requires --rapid-ms >=200. "
                        "Values: 90 (err≈-10%%), 95 (err≈-5%%). 0=disabled.")
    p.add_argument("--port",   default=None,
                   help="Serial port (def. autodetect)")
    p.add_argument("--dry-run", action="store_true",
                   help="Only shows the profile steps, does not connect the source")
    args = p.parse_args()

    module = to_module_params(DEFAULT_MODULE_KEY)
    label  = get_params(DEFAULT_MODULE_KEY)["label"]
    print(f"Panel: {label}")
    print(f"  Vmp={module.Vmp_n} V  Imp={module.Imp_n} A  "
          f"Pmpp={module.Vmp_n*module.Imp_n:.1f} W\n")

    # Construir perfil promedio
    print("Downloading/loading NASA POWER data...")
    nasa_data = nasa_fetch(lat=ORIGINAL_LAT, lon=ORIGINAL_LON,
                           start=ORIGINAL_START, end=ORIGINAL_END,
                           cache_dir=DEFAULT_CACHE_DIR)
    model = SingleDiodeModel(module)
    model.fit()
    full    = build_profile(nasa_data, model, Ns_arr=1, Np_arr=1,
                            tilt=10.0, V_max=V_MAX, I_max=I_MAX)
    profile = apply_strategy(full, strategy="average")
    sample  = _sample_profile(profile, args.steps)

    print(f"Full profile: {len(profile)} steps | sample: {len(sample)} steps")
    print(f"dt per step: {args.dt_ms} ms | estimated total time: "
          f"{len(sample)*args.dt_ms/1000:.0f} s\n")

    # Mostrar tabla del plan
    print(f"{'#':>3} {'Hour':>6} {'V_set':>7} {'I_set':>7} {'P_set':>7}")
    print("-" * 38)
    for i, step in enumerate(sample, 1):
        print(f"{i:>3} {step.get('label',''):>6} "
              f"{step['V_set']:>7.2f} {step['I_set']:>7.3f} {step['P_set']:>7.1f}")
    print()

    if args.dry_run:
        print("[dry-run] The source was not connected.")
        return 0

    # Conectar fuente
    port = args.port or DEFAULT_PORT
    resolved = autodetect_port(port)
    if resolved is None:
        print("ERROR: No serial port detected.")
        return 1
    if resolved != port:
        print(f"Port '{port}' unavailable — using '{resolved}'")
        port = resolved

    ctrl = SCPIController(port=port, baud=DEFAULT_BAUD)
    try:
        idn = ctrl.connect()
        print(f"Connected: {idn.strip()}\n")
    except Exception as exc:
        print(f"ERROR connecting: {exc}")
        return 1

    if args.slope_pct > 0:
        if args.rapid_ms < 200:
            print(f"NOTE: --slope-pct requires --rapid-ms >=200 (current: {args.rapid_ms}).")
            print("       Adjusting rapid_ms to 200 ms automatically.\n")
            args.rapid_ms = 200
        print(f"Adaptive slope mode: knee zone = [{args.slope_pct}%·V_set, V_set], "
              f"{args.rapid_ms} ms cycles\n")
    elif args.rapid_ms > 0:
        print(f"Fast switching mode: VOLT/CURR re-asserted every {args.rapid_ms} ms\n")
    else:
        print("Standard mode (one send per step).\n")
    print("Running diagnostic profile (verbose=True)...")
    print("Watch the 'Mode' column: it should be CV or CC+CV with Err_V < -10%.\n")
    try:
        transition_strategies.run_profile(ctrl, sample, args.dt_ms, verbose=True,
                         rapid_ms=args.rapid_ms, slope_pct=args.slope_pct)
    except KeyboardInterrupt:
        print("\n[manual interruption]")
        ctrl.output_off()
    finally:
        ctrl.disconnect()

    print("\nDiagnostic complete.")
    print("  OK  : Mode='CV' with Err_V < ±8% (source controls voltage correctly).")
    print("  CC  : I_meas ≈ I_set but large negative Err_V → MPPT determines V.")
    print("  ???  : transient; increase --dt-ms to give the MPPT time to converge.")
    print("  If V_meas stalls at ~14V, try --dt-ms 10000 --rapid-ms 100.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
