"""
tools/probe/curve_trace_auto.py
===============================
AUTOMATIC I-V curve fidelity sweep — the programmed version of
curve_trace_manual.py. The EA-PS holds the model curve (envelope="curve",
mirror I->V loop) while the DL3021 sweeps CC setpoints and measures (V, I) at
its own terminals over USB-TMC. Then it reports the same fidelity metrics +
figure as the manual tool.

Two instruments, two control loops, ONE PC:
  - EA-PS  : comm.scpi.run_profile in a background thread. curve_drive="current"
             => the source is a VOLTAGE source governed by the curve (measures I,
             commands VOLT = V_model(I)). This is the latch-free pairing for a
             constant-current load.
  - DL3021 : comm.dl3000.DL3000Load in the main thread. CC mode, steps each
             I_set, waits --dwell s, reads (V, I, P).
They share no state — independent serial (EA) and USB-TMC (DL) links.

Sequence (energizes hardware): connect both -> EA loop ON (sits at Voc, I=0) ->
DL input ON -> sweep CC setpoints -> DL input OFF -> stop EA loop -> analyze.

DL3021 CC range: the sweep crosses the LOW (0-4 A) / HIGH (0-40 A) boundary if
Isc > 4 A. Set the CC range on the front panel to cover Isc BEFORE starting
(switching range needs the input off). The tool prints Isc and the max setpoint.

Usage
-----
    # dry-run: setpoints + curve, no hardware
    python -m tools.probe.curve_trace_auto --model single_diode --G 1000 --T 25 --dry-run

    # live sweep at the bench
    python -m tools.probe.curve_trace_auto --model single_diode --G 1000 --T 25 \\
        --n 14 --dwell 2.0
"""

from __future__ import annotations

import sys
import csv
import time
import threading
import argparse
from pathlib import Path
from datetime import datetime

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from config.hardware import DEFAULT_PORT, DEFAULT_BAUD                # noqa: E402
from config.modules_catalog import get_params, DEFAULT_MODULE_KEY    # noqa: E402
from comm.scpi import SCPIController, autodetect_port                 # noqa: E402
from comm.dl3000 import DL3000Load                                    # noqa: E402
from tools.probe.curve_hold import build_step, MODELS                # noqa: E402
from tools.probe.curve_trace_manual import (                         # noqa: E402
    reference_curve, suggested_setpoints, suggested_setpoints_current,
    analyze, RESULTS_DIR)


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    ap = argparse.ArgumentParser(
        description="Automatic I-V curve fidelity sweep (EA-PS curve loop + "
                    "DL3021 CC sweep over USB-TMC).")
    ap.add_argument("--model", default="single_diode", choices=list(MODELS))
    ap.add_argument("--module", default=DEFAULT_MODULE_KEY)
    ap.add_argument("--G", type=float, default=1000.0, help="Irradiance [W/m²].")
    ap.add_argument("--T", type=float, default=25.0, help="Cell temperature [°C].")
    ap.add_argument("--Ns", type=int, default=1)
    ap.add_argument("--Np", type=int, default=1)
    ap.add_argument("--mode", choices=["cc", "cv"], default="cc",
                    help="DL3021 load mode. cc: sweep current, EA mirror I->V loop "
                         "(default). cv: sweep voltage, EA V->I loop — resolves the "
                         "near-vertical wall the CC sweep cannot sample.")
    ap.add_argument("--n", type=int, default=14, help="Number of setpoints.")
    ap.add_argument("--dwell", type=float, default=2.0,
                    help="Settle+measure time per setpoint [s].")
    ap.add_argument("--rapid-ms", type=int, default=200,
                    help="EA I->V loop period [ms].")
    ap.add_argument("--port", default=None, help="EA serial port (def. autodetect).")
    ap.add_argument("--resource", default=None,
                    help="DL3021 VISA resource (def. auto-discover).")
    ap.add_argument("--out", type=Path, default=RESULTS_DIR)
    ap.add_argument("--dry-run", action="store_true",
                    help="Print setpoints/curve and exit (no hardware).")
    args = ap.parse_args()

    # Reference curve (for setpoints + analysis) and EA loop step.
    V_arr, I_arr, mpp, model_name = reference_curve(
        args.model, args.module, args.G, args.T, args.Ns, args.Np)
    step, _ = build_step(args.model, args.module, args.G, args.T, args.Ns, args.Np)

    # Mode wiring: cc → DL sweeps current + EA mirror I->V loop;
    #              cv → DL sweeps voltage + EA V->I loop (resolves the wall).
    # DL methods are resolved by NAME after the load is connected (below).
    if args.mode == "cv":
        iset        = suggested_setpoints(V_arr, I_arr, mpp, n=args.n)
        curve_drive = "voltage"
        unit        = "V"
        mode_method, point_method = "set_cv_mode", "set_voltage"
    else:
        iset        = suggested_setpoints_current(V_arr, I_arr, mpp, n=args.n)
        curve_drive = "current"
        unit        = "A"
        mode_method, point_method = "set_cc_mode", "set_current"

    Voc, Isc = float(V_arr[-1]), float(I_arr[0])
    label = get_params(args.module)["label"]
    meta = {
        "model_key": args.model, "model_name": model_name,
        "module_key": args.module, "module_label": label,
        "G": args.G, "T": args.T, "Ns": args.Ns, "Np": args.Np,
    }

    print(f"Model:   {model_name}   Module: {label} (Ns={args.Ns} Np={args.Np})")
    print(f"Point:   G={args.G} W/m²  T={args.T} °C")
    print(f"Curve:   Voc={Voc:.2f} V  Isc={Isc:.3f} A  "
          f"MPP={mpp.Pmp:.1f} W @ V={mpp.Vmp:.2f} I={mpp.Imp:.3f}")
    print(f"Sweep:   {args.mode.upper()} — {len(iset)} setpoints "
          f"{iset[0]:.3f} → {iset[-1]:.3f} {unit}  (dwell {args.dwell:.1f} s)")
    if args.mode == "cc" and Isc > 4.0:
        print(f"  NOTE: Isc>4 A — set DL3021 CC range to HIGH (0-40 A) on the "
              f"panel before starting (range switch needs input OFF).")

    if args.dry_run:
        print(f"\n[dry-run] setpoints ({unit}): {iset}")
        print("[dry-run] no hardware touched.")
        return 0

    # ── Connect EA-PS ─────────────────────────────────────────────────────────
    port = autodetect_port(args.port or DEFAULT_PORT)
    if port is None:
        print("ERROR: no EA serial port detected.")
        return 1
    ctrl = SCPIController(port=port, baud=DEFAULT_BAUD)
    try:
        print(f"\nEA-PS:   {ctrl.connect().strip()}")
    except Exception as exc:                              # noqa: BLE001
        print(f"ERROR connecting EA-PS: {exc}")
        return 1
    ctrl.set_protections(opp=200.0)
    print("EA-PS:   OPP=200 W")

    # ── Connect DL3021 ────────────────────────────────────────────────────────
    load = DL3000Load(resource=args.resource)
    try:
        print(f"DL3021:  {load.connect()}")
    except Exception as exc:                              # noqa: BLE001
        print(f"ERROR connecting DL3021: {exc}")
        ctrl.disconnect()
        return 1

    # EA loop dt must outlast the whole sweep; we stop it early when done.
    dt_ms = int((len(iset) * (args.dwell + 0.5) + 6.0) * 1000)
    measured: list[tuple[float, float]] = []

    def _ea_loop():
        try:
            ctrl.run_profile([step], dt_ms=dt_ms, envelope="curve",
                             curve_drive=curve_drive, rapid_ms=args.rapid_ms)
        except Exception as exc:                          # noqa: BLE001
            print(f"[EA loop error] {type(exc).__name__}: {exc}")

    set_mode  = getattr(load, mode_method)
    set_point = getattr(load, point_method)
    t = threading.Thread(target=_ea_loop, daemon=True)
    try:
        set_mode()
        load.input_off()
        t.start()
        time.sleep(1.0)               # let the EA loop seed at the safe rail

        print(f"\n{'set':>8} {'V_meas':>8} {'I_meas':>8} {'P_meas':>8}")
        print("-" * 36)
        load.input_on()
        for sp in iset:
            set_point(sp)
            time.sleep(args.dwell)
            v, i, p = load.measure()
            measured.append((v, i))
            print(f"{sp:>8.3f} {v:>8.3f} {i:>8.3f} {p:>8.3f}")
    except KeyboardInterrupt:
        print("\n[manual stop]")
    finally:
        try:
            load.input_off()
        finally:
            load.close()
        ctrl.stop()                   # ends the EA loop
        t.join(timeout=5.0)
        ctrl.disconnect()
        print("\nDL input OFF, EA output OFF, both released.")

    if not measured:
        print("No measurements collected.")
        return 1

    # ── Persist + analyze (same metrics/figure as the manual tool) ─────────────
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir = (args.out if args.out.suffix == "" else args.out.parent) \
        / f"trace_auto_{args.model}_{ts}"
    out_dir.mkdir(parents=True, exist_ok=True)
    csv_path = out_dir / "measured.csv"
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow([f"# auto {args.mode.upper()} sweep {model_name} | {label} | "
                    f"G={args.G} T={args.T} | Voc={Voc:.2f} Isc={Isc:.3f}"])
        w.writerow([f"set_{unit}", "V_meas", "I_meas"])
        for (v, i), sp in zip(measured, iset):
            w.writerow([f"{sp:.3f}", f"{v:.4f}", f"{i:.4f}"])
    print(f"Raw sweep: {csv_path}")

    analyze(measured, V_arr, I_arr, mpp, meta, out_dir)
    return 0


if __name__ == "__main__":
    sys.exit(main())
