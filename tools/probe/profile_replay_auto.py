"""
tools/probe/profile_replay_auto.py
==================================
FULL-DAY profile reproduction on the EA-PS with the DL3021 as a DETERMINISTIC
load (no MPPT). The source replays the hourly staircase in the `curve` envelope
(mirror I->V loop, the latch-free pairing validated by curve_trace_auto.py); the
load imposes a chosen load LINE and we compare its measured operating point to
the model's curve ∩ load-line.

Why a deterministic load: it takes the MPPT out of the equation. Whatever
transient the load sees is the EMULATOR's, not a DUT MPPT's — so a run here is
the floor that the MPPT-inverter run (experiments/) sits on top of; the
difference isolates the inverter's control dynamics.

Load modes (the load LINE that intersects each hour's curve)
------------------------------------------------------------
    cc  I = const (horizontal line). Robust, but the operating point only moves
        in V across the day — the current is pinned, so ΔI is not exercised.
        Default I = 0.9·min daytime Isc (the proven-stable band). --track-mpp
        makes it follow Imp(t) instead (known to hit the unstable knee).
    cr  I = V/R (line through the origin). ONE fixed R ≈ Vmp/Imp: as the curve
        steps the intersection slides in BOTH V and I, like the inverter but
        with no hunting. No per-step reprogramming (no latch).
    cp  V·I = P, P = Pmp(t) (hyperbola tracking the MPP power). Closest to what
        the inverter draws; sits at the knee, so highest collapse risk.

Two buses, no contention: EA over serial (run_profile in a thread), DL over
USB-TMC (sampled fast in the main thread). The EAMonitor is NOT used.

Usage
-----
    python -m tools.probe.profile_replay_auto --load-mode cc   # robust baseline
    python -m tools.probe.profile_replay_auto --load-mode cr   # both V and I
    python -m tools.probe.profile_replay_auto --load-mode cp   # MPP power
    python -m tools.probe.profile_replay_auto --load-mode cr --dry-run
"""

from __future__ import annotations

import sys
import csv
import time
import threading
import argparse
import statistics
from pathlib import Path
from datetime import datetime

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from config.hardware import DEFAULT_PORT, DEFAULT_BAUD, V_MAX, I_MAX  # noqa: E402
from config.modules_catalog import to_module_params, get_params, DEFAULT_MODULE_KEY  # noqa: E402
from models.single_diode import SingleDiodeModel                  # noqa: E402
from pipeline.nasa_power import fetch as nasa_fetch, DEFAULT_CACHE_DIR  # noqa: E402
from pipeline.profile import build as build_profile, apply_strategy, _hourly_curve  # noqa: E402
from comm.scpi import SCPIController, autodetect_port              # noqa: E402
from comm.dl3000 import DL3000Load                                 # noqa: E402

RESULTS_DIR = Path(__file__).resolve().parent / "profile_replay_results"

# Cali (experiment 20260625_115402): cached year
# data/nasa_cache/nasa_3.45_-76.53_20240315_20250317.json, averaged typical day.
DAY_LAT, DAY_LON = 3.45, -76.53
DAY_START, DAY_END = "20240315", "20250317"

# mode -> (mode-setter, setpoint-setter, unit) on DL3000Load.
SETTERS = {
    "cc": ("set_cc_mode", "set_current",    "A"),
    "cr": ("set_cr_mode", "set_resistance", "Ω"),
    "cp": ("set_cp_mode", "set_power",      "W"),
}


def expected_op(step: dict, mode: str, sp: float) -> tuple[float, float] | None:
    """Model operating point (V*, I*) = curve ∩ load line, or None if there is
    no intersection (e.g. CC current above the hour's Isc, or a dark hour)."""
    cv, ci = step.get("curve_v"), step.get("curve_i")
    if not cv or not ci or sp <= 0:
        return None
    if mode == "cc":
        if sp > step.get("Isc", 0):
            return None
        return (SCPIController._interp_voltage(sp, cv, ci), sp)
    # Load-line current at each sampled voltage.
    if mode == "cr":                                   # I = V / R
        line = [v / sp for v in cv]
    else:                                              # cp: I = P / V
        line = [(sp / v if v > 1e-6 else float("inf")) for v in cv]
    # Highest-voltage sign change of (curve_i - line) = the right-branch
    # intersection where a CR/CP load settles on a PV source.
    best = None
    for k in range(len(cv) - 1):
        d0, d1 = ci[k] - line[k], ci[k + 1] - line[k + 1]
        if d0 * d1 < 0:
            f = d0 / (d0 - d1)
            best = (cv[k] + f * (cv[k + 1] - cv[k]),
                    ci[k] + f * (ci[k + 1] - ci[k]))
    return best


def build_setpoints(profile, args, raw):
    """(seq, desc): per-step load setpoint in the mode's unit + a label."""
    mode = args.load_mode
    if mode == "cc":
        if args.track_mpp:
            return [round(s["I_set"], 3) for s in profile], "CC tracking Imp(t)"
        if args.i_load is not None:
            i_fixed = args.i_load
        else:
            day_isc = [s["Isc"] for s in profile if s.get("Gpoa", 0) >= 100]
            i_fixed = round(0.9 * min(day_isc), 2) if day_isc else 0.5
        return [i_fixed] * len(profile), f"CC fixed I={i_fixed:.3f} A"
    if mode == "cr":
        if args.track_mpp:
            # R_mpp(t) = Vmp/Imp per hour: keeps the op at ~0.9·Isc (the MPP,
            # stable band) all day instead of drifting into the Isc wall. Dark
            # hours -> open (large R) so the load draws ~0.
            seq = [round(s["V_set"] / s["I_set"], 2) if s.get("I_set", 0) > 0.05
                   else 15000.0 for s in profile]
            return seq, "CR tracking R_mpp(t)=Vmp/Imp"
        if args.r_load is not None:
            R = args.r_load
        else:
            # R = Vmp / peak daytime Imp: sits at MPP at peak sun, drifts toward
            # Voc (right branch, off the Isc wall) as the curve shrinks.
            ipk = max((s["I_set"] for s in profile if s.get("Gpoa", 0) >= 100),
                      default=raw["Imp"])
            R = round(raw["Vmp"] / ipk, 2)
        return [R] * len(profile), f"CR fixed R={R:.2f} Ω"
    # cp: k·Pmp (k<1) so the hyperbola cuts the curve (a right-branch point just
    # below MPP) instead of kissing it tangentially at exactly Pmp.
    seq = [round(args.cp_frac * s.get("P_set", 0.0), 1) for s in profile]
    return seq, f"CP tracking {args.cp_frac:.2f}·Pmp(t)"


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    ap = argparse.ArgumentParser(
        description="Full-day profile replay on the EA-PS with the DL3021 as a "
                    "deterministic CC/CR/CP load.")
    ap.add_argument("--load-mode", default="cc", choices=["cc", "cr", "cp"],
                    help="Load line: cc=const current, cr=const resistance, "
                         "cp=const power (tracks Pmp).")
    ap.add_argument("--module", default=DEFAULT_MODULE_KEY)
    ap.add_argument("--lat", type=float, default=DAY_LAT, help="Site latitude.")
    ap.add_argument("--lon", type=float, default=DAY_LON, help="Site longitude.")
    ap.add_argument("--date", default=DAY_START, help="Start day YYYYMMDD.")
    ap.add_argument("--end", default=DAY_END, help="End day YYYYMMDD.")
    ap.add_argument("--strategy", default="average", choices=["average", "day"],
                    help="'average' = typical-day mean over the range (Cali "
                         "experiment); 'day' = filter daylight hours of one day.")
    ap.add_argument("--Ns", type=int, default=1)
    ap.add_argument("--Np", type=int, default=1)
    ap.add_argument("--dt-ms", type=int, default=7000, help="ms per hourly step.")
    ap.add_argument("--rapid-ms", type=int, default=200, help="EA loop period [ms].")
    ap.add_argument("--curve-damp", type=float, default=0.2,
                    help="Mirror-loop under-relaxation (0<α≤1). <1 damps the "
                         "high-R limit cycle at low sun. 1.0 = undamped.")
    ap.add_argument("--i-offset", type=float, default=0.10,
                    help="EA-PS current-sense offset [A] to subtract in the mirror "
                         "loop (measured +0.10 A high on this unit).")
    ap.add_argument("--sample-ms", type=int, default=100,
                    help="DL sampling period [ms] (fast enough to see the edge).")
    ap.add_argument("--i-load", type=float, default=None,
                    help="cc: fixed CC current [A]. Default 0.9·min daytime Isc.")
    ap.add_argument("--r-load", type=float, default=None,
                    help="cr: fixed resistance [Ω]. Default Vmp/peak-Imp (at MPP).")
    ap.add_argument("--cp-frac", type=float, default=0.9,
                    help="cp: setpoint = frac·Pmp (frac<1 for a clean crossing).")
    ap.add_argument("--track-mpp", action="store_true",
                    help="cc: track Imp(t); cr: track R_mpp(t)=Vmp/Imp per hour "
                         "(keeps the op at the MPP, off the Isc wall).")
    ap.add_argument("--port", default=None, help="EA serial port (def. autodetect).")
    ap.add_argument("--resource", default=None, help="DL3021 VISA resource.")
    ap.add_argument("--out", type=Path, default=RESULTS_DIR)
    ap.add_argument("--dry-run", action="store_true",
                    help="Build + print the plan, no hardware.")
    args = ap.parse_args()
    mode = args.load_mode

    module = to_module_params(args.module)
    raw    = get_params(args.module)
    label  = raw["label"]
    model  = SingleDiodeModel(module)
    model.fit()

    print(f"Loading NASA POWER data ({args.lat},{args.lon} {args.date}-{args.end})...")
    nasa = nasa_fetch(lat=args.lat, lon=args.lon, start=args.date, end=args.end,
                      cache_dir=DEFAULT_CACHE_DIR)
    full = build_profile(nasa, model, Ns_arr=args.Ns, Np_arr=args.Np,
                         tilt=10.0, V_max=V_MAX, I_max=I_MAX, attach_curve=True)
    profile = apply_strategy(full, strategy=args.strategy)
    if not profile:
        print("ERROR: empty profile.")
        return 1

    # "average" drops curve_v/curve_i (it only averages Voc/Isc); the mirror
    # I->V loop and expected_op() need the table, so regenerate it per averaged
    # step from its Gpoa/Tcell. "day" keeps the curves from build().
    for s in profile:
        if "curve_v" not in s and s.get("P_set", 0) > 0:
            s.update(_hourly_curve(model, s["Gpoa"], s["Tcell"],
                                   args.Ns, args.Np, 40, V_MAX, I_MAX))

    seq, load_desc = build_setpoints(profile, args, raw)
    unit = SETTERS[mode][2]

    print(f"Module:  {label} (Ns={args.Ns} Np={args.Np})")
    print(f"Day:     {len(profile)} hourly steps | dt={args.dt_ms} ms | "
          f"~{len(profile)*args.dt_ms/1000:.0f} s | sample {args.sample_ms} ms")
    print(f"Load:    DL3021 {load_desc}\n")

    print(f"{'h':>5} {'Gpoa':>6} {'Isc':>6} {'set':>7} {'Vexp':>6} {'Iexp':>6} {'Pset':>7}")
    print("-" * 50)
    for k, s in enumerate(profile):
        op = expected_op(s, mode, seq[k])
        vx, ix = op if op else (float("nan"), float("nan"))
        flag = "" if op else "  no-cross"
        print(f"{s.get('label',''):>5} {s.get('Gpoa',0):>6.0f} "
              f"{s.get('Isc',0):>6.3f} {seq[k]:>6.2f}{unit} "
              f"{vx:>6.2f} {ix:>6.3f} {s['P_set']:>7.1f}{flag}")

    if args.dry_run:
        print("\n[dry-run] no hardware touched.")
        return 0

    # ── Connect ───────────────────────────────────────────────────────────────
    port = autodetect_port(args.port or DEFAULT_PORT)
    if port is None:
        print("ERROR: no EA serial port.")
        return 1
    ctrl = SCPIController(port=port, baud=DEFAULT_BAUD)
    try:
        print(f"\nEA-PS:   {ctrl.connect().strip()}")
    except Exception as exc:                              # noqa: BLE001
        print(f"ERROR connecting EA-PS: {exc}")
        return 1
    load = DL3000Load(resource=args.resource)
    try:
        print(f"DL3021:  {load.connect()}")
    except Exception as exc:                              # noqa: BLE001
        print(f"ERROR connecting DL3021: {exc}")
        ctrl.disconnect()
        return 1

    mode_fn, set_fn, _ = SETTERS[mode]
    samples: list[tuple] = []          # (t, step_idx, V, I, P)
    shared = {"i": -1, "running": True, "last_sp": seq[0]}

    # ALL DL access runs in the EA thread (progress_cb + current_fn), so there is
    # no cross-thread contention on the USB-TMC bus. The main thread only waits.
    def progress_cb(i, total, step):
        if seq[i] != shared["last_sp"]:        # per-hour setpoint change (cp/track)
            getattr(load, set_fn)(seq[i])
            shared["last_sp"] = seq[i]
        shared["i"] = i

    def current_fn():                          # close the mirror loop with the DL
        return load.measure()                  # accurate mA-resolution current

    def sample_cb(idx, v, i, p):
        samples.append((time.time(), idx, v, i, p))

    def ea_thread():
        try:
            ctrl.run_profile(profile, dt_ms=args.dt_ms, envelope="curve",
                             curve_drive="current", rapid_ms=args.rapid_ms,
                             curve_damp=args.curve_damp,
                             curve_i_offset=args.i_offset, progress_cb=progress_cb,
                             current_fn=current_fn, sample_cb=sample_cb)
        except Exception as exc:                          # noqa: BLE001
            print(f"[EA loop error] {type(exc).__name__}: {exc}")
        finally:
            shared["running"] = False

    t = threading.Thread(target=ea_thread, daemon=True)
    getattr(load, mode_fn)()
    if mode == "cr":
        load.set_resistance_range(15000)   # 150 V range: the peak op is ~19 V
    getattr(load, set_fn)(seq[0])
    load.input_on()
    t.start()
    try:
        while shared["running"]:
            time.sleep(0.2)
    except KeyboardInterrupt:
        print("\n[manual stop]")
    ctrl.stop()                    # stop the EA loop first...
    t.join(timeout=5.0)            # ...so the DL is no longer touched in-thread
    try:
        load.input_off()
    finally:
        load.close()
    ctrl.disconnect()
    print("DL input OFF, EA output OFF, both released.")

    # Per-step settled value = last sample of each step.
    last: dict[int, tuple] = {}
    for tstamp, idx, v, i, p in samples:
        last[idx] = (v, i, p)

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir = (args.out if args.out.suffix == "" else args.out.parent) \
        / f"replay_{mode}_{ts}"
    out_dir.mkdir(parents=True, exist_ok=True)

    # ── Per-hour report (V and I vs the model operating point) ────────────────
    print(f"\n{'h':>5} {'Gpoa':>6} {'Vexp':>6} {'Vmeas':>6} {'Iexp':>6} "
          f"{'Imeas':>6} {'errV%':>6} {'errI%':>6} {'Pmeas':>6}")
    print("-" * 66)
    rows, verr, ierr = [], [], []
    for idx, s in enumerate(profile):
        op = expected_op(s, mode, seq[idx])
        vx, ix = op if op else (None, None)
        meas = last.get(idx)
        vm, im, pm = meas if meas else (float("nan"),) * 3
        ev = ((vm - vx) / vx * 100) if (vx and vx > 0 and meas) else float("nan")
        ei = ((im - ix) / ix * 100) if (ix and ix > 0 and meas) else float("nan")
        if ev == ev:
            verr.append(ev)
        if ei == ei:
            ierr.append(ei)
        print(f"{s.get('label',''):>5} {s.get('Gpoa',0):>6.0f} "
              f"{(vx if vx is not None else float('nan')):>6.2f} {vm:>6.2f} "
              f"{(ix if ix is not None else float('nan')):>6.3f} {im:>6.3f} "
              f"{ev:>+6.1f} {ei:>+6.1f} {pm:>6.1f}")
        rows.append([s.get("label", ""), s.get("Gpoa", 0), s.get("Isc", 0),
                     seq[idx], vx if vx is not None else "",
                     ix if ix is not None else "",
                     f"{vm:.3f}", f"{im:.3f}", f"{pm:.3f}", s["P_set"]])

    csv_path = out_dir / "replay.csv"
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow([f"# day replay {label} | {load_desc} | dt={args.dt_ms} ms"])
        w.writerow(["hour", "Gpoa", "Isc", "set", "V_exp", "I_exp",
                    "V_meas", "I_meas", "P_meas", "P_set_model"])
        w.writerows(rows)

    # Raw time series (the transient: sub-second V,I,P around each step edge).
    raw_path = out_dir / "samples.csv"
    with open(raw_path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["t_s", "step_idx", "V", "I", "P"])
        t0 = samples[0][0] if samples else 0.0
        for tstamp, idx, v, i, p in samples:
            w.writerow([f"{tstamp - t0:.3f}", idx, f"{v:.4f}", f"{i:.4f}", f"{p:.4f}"])

    def _rms(xs):
        return (sum(x * x for x in xs) / len(xs)) ** 0.5 if xs else float("nan")

    print(f"\nReplay error vs model ({mode.upper()}, hours with a crossing):")
    if verr:
        print(f"  V: mean {statistics.mean(verr):+.1f}%  RMS {_rms(verr):.1f}%  "
              f"({len(verr)}/{len(profile)} h)")
    if ierr:
        print(f"  I: mean {statistics.mean(ierr):+.1f}%  RMS {_rms(ierr):.1f}%  "
              f"({len(ierr)}/{len(profile)} h)")
    print(f"CSV:     {csv_path}\nSamples: {raw_path} ({len(samples)} pts)")
    _plot(profile, last, seq, mode, load_desc, label, out_dir)
    return 0


def _plot(profile, last, seq, mode, load_desc, label, out_dir):
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        return
    hours = [s.get("hour", k) for k, s in enumerate(profile)]
    ops   = [expected_op(s, mode, seq[k]) for k, s in enumerate(profile)]
    vexp  = [o[0] if o else float("nan") for o in ops]
    iexp  = [o[1] if o else float("nan") for o in ops]
    vmeas = [last.get(k, (float("nan"),) * 3)[0] for k in range(len(profile))]
    imeas = [last.get(k, (float("nan"),) * 3)[1] for k in range(len(profile))]
    fig, (a1, a2) = plt.subplots(2, 1, figsize=(6.2, 5.4), sharex=True)
    a1.plot(hours, vexp, "-o", color="#1f77b4", ms=4, label="V model")
    a1.plot(hours, vmeas, "x", color="#d62728", ms=7, label="V measured")
    a1.set_ylabel("Voltage [V]"); a1.legend(fontsize=8); a1.grid(True, alpha=0.3)
    a1.set_title(f"Day replay ({load_desc})  |  {label}")
    a2.plot(hours, iexp, "-o", color="#1f77b4", ms=4, label="I model")
    a2.plot(hours, imeas, "x", color="#d62728", ms=7, label="I measured")
    a2.set_xlabel("Hour"); a2.set_ylabel("Current [A]")
    a2.legend(fontsize=8); a2.grid(True, alpha=0.3)
    fig.tight_layout()
    fig.savefig(out_dir / "replay_VI.png", dpi=150)
    plt.close(fig)
    print(f"Figure:  {out_dir / 'replay_VI.png'}")


if __name__ == "__main__":
    sys.exit(main())
