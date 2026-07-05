"""
tools/probe/curve_hold.py
=========================
HOLD a single I-V curve point on the EA-PS in the `curve` envelope so you can
sweep CV by hand on the DL3021 (DL3000) and validate curve fidelity manually.

The manual validation (tools/probe/curve_trace_manual.py) needs the emulator to
present ONE model curve at a fixed (G, T) for a couple of minutes while you set
each CV voltage on the load and read (V, I). A normal profile runs each step for
dt and stops; this helper builds a SINGLE step (same production path as
pipeline.profile.build → run_profile) and keeps the I(V) loop running for
`--hold-s` seconds (or until Ctrl-C).

It exercises the redesigned `curve` loop in comm/scpi.run_profile: the source
acts as a current source governed by the model curve (measures V, commands
CURR = I_model(V)), with the start-up anti-latch seed window. Sweeping
--rapid-ms / --seed-hold-ms here is how you tune the loop against the DL3021.

Workflow
--------
  1) Generate the data sheet (no hardware):
       python -m tools.probe.curve_trace_manual --template --model single_diode \\
           --G 1000 --T 25
  2) At the bench, hold the curve (this tool):
       python -m tools.probe.curve_hold --model single_diode --G 1000 --T 25 \\
           --hold-s 300
  3) Set the DL3021 in the load mode that matches --load-mode and, for each
     setpoint of the sheet, wait ~2 s and record V_meas / I_meas. Ctrl-C here
     when done (output goes OFF).
       --load-mode cv|cr  → V->I loop  (source = current source, you sweep V/R)
       --load-mode cc     → I->V loop  (source = voltage source, you sweep I)
     CC needs the mirror loop; without it a CC load latches the source.
  4) Analyze:
       python -m tools.probe.curve_trace_manual --measured <filled.csv> \\
           --model single_diode --G 1000 --T 25

Usage
-----
    python -m tools.probe.curve_hold [--model single_diode] [--module KEY]
        [--G 1000] [--T 25] [--Ns 1] [--Np 1]
        [--hold-s 300] [--rapid-ms 200] [--seed-hold-ms N]
        [--port COM3] [--dry-run]
"""

from __future__ import annotations

import sys
import argparse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from config.hardware import DEFAULT_PORT, DEFAULT_BAUD, V_MAX, I_MAX  # noqa: E402
from config.modules_catalog import to_module_params, get_params, DEFAULT_MODULE_KEY  # noqa: E402
from models.simplified import SimplifiedModel       # noqa: E402
from models.single_diode import SingleDiodeModel    # noqa: E402
from models.two_diode import TwoDiodeModel          # noqa: E402
from pipeline.profile import _hourly_curve          # noqa: E402
from comm.scpi import SCPIController, autodetect_port  # noqa: E402

MODELS = {
    "simplified":   ("Simplified", SimplifiedModel),
    "single_diode": ("Single diode", SingleDiodeModel),
    "two_diode":    ("Two diodes", TwoDiodeModel),
}


def build_step(model_key: str, module_key: str, G: float, T: float,
               Ns: int, Np: int) -> tuple[dict, str]:
    """One profile step with the model curve attached (production path)."""
    model_name, ModelClass = MODELS[model_key]
    model = ModelClass(to_module_params(module_key))
    model.fit()
    mpp = model.get_mpp(G_poa=G, T_cell=T, Ns_arr=Ns, Np_arr=Np,
                        V_max_hw=V_MAX, I_max_hw=I_MAX)
    if mpp.Pmp <= 0:
        sys.exit("ERROR: degenerate curve (Pmp<=0) at this (G, T).")
    curve = _hourly_curve(model, G, T, Ns, Np, curve_pts=40,
                          V_max=V_MAX, I_max=I_MAX)
    if not curve:
        sys.exit(f"ERROR: model '{model_key}' does not provide an I(V) curve.")
    step = {
        "label": "hold",
        "V_set": mpp.Vmp, "I_set": mpp.Imp, "P_set": mpp.Pmp,
        **curve,                       # curve_v, curve_i, Voc, Isc
    }
    return step, model_name


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    ap = argparse.ArgumentParser(
        description="Hold one I-V curve on the EA-PS (curve envelope) for manual "
                    "CV sweep on the DL3021.")
    ap.add_argument("--model", default="single_diode", choices=list(MODELS),
                    help="Reference curve model.")
    ap.add_argument("--module", default=DEFAULT_MODULE_KEY,
                    help="Module key in the catalog.")
    ap.add_argument("--G", type=float, default=1000.0, help="Irradiance [W/m²].")
    ap.add_argument("--T", type=float, default=25.0, help="Cell temperature [°C].")
    ap.add_argument("--Ns", type=int, default=1, help="Series modules.")
    ap.add_argument("--Np", type=int, default=1, help="Parallel modules.")
    ap.add_argument("--load-mode", default="cv", choices=["cv", "cr", "cc"],
                    help="DL3021 load mode you will sweep. cv/cr → V->I loop "
                         "(default); cc → I->V mirror loop (avoids CC latch).")
    ap.add_argument("--hold-s", type=float, default=300.0,
                    help="Seconds to hold the curve (Ctrl-C to stop sooner).")
    ap.add_argument("--rapid-ms", type=int, default=200,
                    help="I(V) loop period [ms]. Sweep to characterize the "
                         "source's SCPI bandwidth.")
    ap.add_argument("--seed-hold-ms", type=int, default=None,
                    help="Anti-latch seed window [ms]. Default: max(2·rapid, 250).")
    ap.add_argument("--port", default=None, help="Serial port (def. autodetect).")
    ap.add_argument("--dry-run", action="store_true",
                    help="Print the step/curve and exit (no source).")
    args = ap.parse_args()

    step, model_name = build_step(args.model, args.module, args.G, args.T,
                                  args.Ns, args.Np)
    label = get_params(args.module)["label"]

    curve_drive = "current" if args.load_mode == "cc" else "voltage"
    loop_desc = ("I->V (source=voltage src; sweep CC current on the load)"
                 if curve_drive == "current"
                 else "V->I (source=current src; sweep CV voltage / CR on the load)")

    Voc, Isc = step["Voc"], step["Isc"]
    print(f"Model:   {model_name}")
    print(f"Module:  {label}  (Ns={args.Ns} Np={args.Np})")
    print(f"Point:   G={args.G} W/m²  T={args.T} °C")
    print(f"Curve:   Voc={Voc:.2f} V  Isc={Isc:.3f} A  "
          f"MPP={step['P_set']:.1f} W @ V={step['V_set']:.2f} I={step['I_set']:.3f}")
    print(f"Load:    {args.load_mode.upper()}  →  {loop_desc}")
    print(f"Loop:    rapid={args.rapid_ms} ms  "
          f"seed_hold={args.seed_hold_ms if args.seed_hold_ms is not None else 'auto'} ms"
          f"  hold={args.hold_s:.0f} s")
    print()

    if args.dry_run:
        print(f"curve_v ({len(step['curve_v'])} pts): {step['curve_v']}")
        print(f"curve_i ({len(step['curve_i'])} pts): {step['curve_i']}")
        print("\n[dry-run] source not connected.")
        return 0

    port = args.port or DEFAULT_PORT
    resolved = autodetect_port(port)
    if resolved is None:
        print("ERROR: no serial port detected.")
        return 1
    if resolved != port:
        print(f"Port '{port}' unavailable — using '{resolved}'.")
        port = resolved

    ctrl = SCPIController(port=port, baud=DEFAULT_BAUD)
    try:
        idn = ctrl.connect()
        print(f"Connected: {idn.strip()}")
    except Exception as exc:
        print(f"ERROR connecting: {exc}")
        return 1

    sweep_var = "I_set" if curve_drive == "current" else "V_set"
    print(f"\nHolding the curve. On the DL3021 ({args.load_mode.upper()} mode) "
          f"sweep {sweep_var} from the data sheet, wait ~2 s each, record (V, I).")
    print("Press Ctrl-C to stop (output OFF).\n")
    try:
        ctrl.run_profile([step], dt_ms=int(args.hold_s * 1000),
                          envelope="curve", rapid_ms=args.rapid_ms,
                          curve_seed_hold_ms=args.seed_hold_ms,
                          curve_drive=curve_drive, verbose=True)
    except KeyboardInterrupt:
        print("\n[manual stop]")
    finally:
        ctrl.output_off()
        ctrl.disconnect()
        print("Output OFF, disconnected.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
