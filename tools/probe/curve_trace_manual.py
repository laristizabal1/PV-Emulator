"""
tools/probe/curve_trace_manual.py
=================================
MANUAL I-V curve fidelity validation of the emulator — no programmed
instrument, no new dependencies.

Idea: the emulator (EA-PS + I(V) loop, `curve` envelope) must present the model's
I(V) curve at its terminals. To TEST it in a device-agnostic way, the DL3000
electronic load is used as a reference instrument, in CV mode, sweeping the
voltage and reading (V, I) from the screen. This script:

  1. (--template)  Generates a CSV with the suggested voltages to set on the
                   DL3000 (~0 to Voc, denser at the knee) and the current the
                   model PREDICTS at each — your data-collection sheet.
  2. (--measured)  Reads the CSV you filled with the measurements (V_meas,
                   I_meas), compares it against the model curve and reports the
                   fidelity: current NRMSE + error in Isc / MPP / Voc / FF, plus
                   a figure (measured vs model curve).

It does NOT control the DL3000: the sweep and reading are done by hand
(procedure below). For the AUTOMATIC USB-TMC sweep see the future comm/dl3000.py.

Manual procedure with the DL3000
--------------------------------
  1. Connect the EA-PS DC output to the DL3000 input.
  2. Start the emulator in the `curve` envelope with a ONE-step profile at the
     chosen (G, T) condition (or leave the EA at that point of the curve).
  3. DL3000 in CV mode (CV key). For each V_set of the template:
       - set the CV voltage, wait ~2 s to settle,
       - record V_meas and I_meas from the DL3000 screen.
  4. Enter those pairs in the V_meas / I_meas columns of the CSV and run --measured.

Usage
-----
    # 1) generate the data-collection sheet (pvlib De Soto model, STC):
    python -m tools.probe.curve_trace_manual --template --model pvlib \\
        --G 1000 --T 25 --out tools/probe/curve_trace_results

    # 2) after measuring, analyze:
    python -m tools.probe.curve_trace_manual --measured measurements.csv \\
        --model pvlib --G 1000 --T 25
"""

from __future__ import annotations

import sys
import csv
import argparse
from pathlib import Path
from datetime import datetime

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import numpy as np                                       # noqa: E402

from config.modules_catalog import to_module_params, get_params, DEFAULT_MODULE_KEY  # noqa: E402
from models.simplified import SimplifiedModel           # noqa: E402
from models.single_diode import SingleDiodeModel        # noqa: E402
from models.two_diode import TwoDiodeModel              # noqa: E402

RESULTS_DIR = Path(__file__).resolve().parent / "curve_trace_results"

MODELS = {
    "simplified":   ("Simplified", SimplifiedModel),
    "single_diode": ("Single diode", SingleDiodeModel),
    "two_diode":    ("Two diodes", TwoDiodeModel),
}


# ─────────────────────────────────────────────────────────────────────────────
# MODEL REFERENCE CURVE
# ─────────────────────────────────────────────────────────────────────────────

def reference_curve(model_key: str, module_key: str, G: float, T: float,
                    Ns: int, Np: int):
    """Return (V_arr, I_arr, mpp) of the model I-V curve at (G, T)."""
    model_name, ModelClass = MODELS[model_key]
    params = to_module_params(module_key)
    model = ModelClass(params)
    model.fit()
    try:
        res = model.iv_curve(G, T, Ns_arr=Ns, Np_arr=Np, n_pts=200)
    except NotImplementedError:
        sys.exit(f"ERROR: model '{model_key}' does not implement iv_curve().")
    V = np.asarray(res.V_arr, dtype=float)
    I = np.asarray(res.I_arr, dtype=float)
    order = np.argsort(V)                # ensure ascending V for interp
    return V[order], I[order], res, model_name


def model_current_at(V_arr, I_arr, v: float) -> float:
    """Model current at an arbitrary voltage (linear interp, clip to [0, Voc])."""
    return float(np.interp(v, V_arr, I_arr))


# ─────────────────────────────────────────────────────────────────────────────
# TEMPLATE MODE — data-collection sheet
# ─────────────────────────────────────────────────────────────────────────────

def suggested_setpoints(V_arr, I_arr, mpp, n: int = 13) -> list[float]:
    """
    Voltages to set on the DL3000: cover [~0, Voc] with extra DENSITY at the
    knee (around Vmp), where the curve has the most structure and where the I(V)
    loop is most demanding.
    """
    Voc = float(V_arr[-1])
    Vmp = float(mpp.Vmp)
    # half uniform points across the range, half concentrated at the knee
    n_uni  = max(n // 2, 3)
    n_knee = n - n_uni
    uni  = np.linspace(0.05 * Voc, Voc, n_uni)
    knee = np.linspace(max(0.7 * Vmp, 0.05 * Voc), min(1.15 * Vmp, Voc), n_knee)
    vs = sorted(set(round(float(v), 2) for v in np.concatenate([uni, knee])))
    return vs


def write_template(path: Path, V_arr, I_arr, mpp, meta: dict):
    Voc = float(V_arr[-1]); Isc = float(I_arr[0])
    vs = suggested_setpoints(V_arr, I_arr, mpp)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow([f"# Reference curve: {meta['model_name']} | "
                    f"module {meta['module_label']} | G={meta['G']} W/m2 "
                    f"T={meta['T']} C | Voc={Voc:.2f} V Isc={Isc:.3f} A "
                    f"Vmp={mpp.Vmp:.2f} V Imp={mpp.Imp:.3f} A Pmp={mpp.Pmp:.1f} W"])
        w.writerow(["# Set each V_set on the DL3000 (CV mode) and record V_meas, I_meas."])
        w.writerow(["V_set", "I_model_expected", "V_meas", "I_meas"])
        for v in vs:
            w.writerow([f"{v:.2f}", f"{model_current_at(V_arr, I_arr, v):.4f}",
                        "", ""])
    print(f"Data-collection sheet written to: {path}")
    print(f"  {len(vs)} suggested V_set points between 0.05·Voc and Voc "
          f"(dense at the knee ~Vmp={mpp.Vmp:.1f} V).")
    print("  Fill V_meas / I_meas with the DL3000 readings and run --measured.")


# ─────────────────────────────────────────────────────────────────────────────
# CC SWEEP — suggested current setpoints (DL3021 in CC mode)
# ─────────────────────────────────────────────────────────────────────────────

def model_voltage_at(V_arr, I_arr, i: float) -> float:
    """Model voltage at an arbitrary current (interp on the descending branch)."""
    order = np.argsort(I_arr)
    return float(np.interp(i, np.asarray(I_arr)[order], np.asarray(V_arr)[order]))


def suggested_setpoints_current(V_arr, I_arr, mpp, n: int = 14,
                                i_low_max: float = 4.0) -> list[float]:
    """
    Currents to set on the DL3021 in CC mode. The CC sweep is the MIRROR of the
    CV one: here the whole voltage span 0..Voc is crammed into the current band
    Imp..Isc, so the DENSITY is concentrated near Isc (plateau + knee), where V
    moves fastest with current.

    i_low_max marks the DL3021 low CC range ceiling (0-4 A). Points above it
    require the high range (0-40 A) — that is where the MPP and plateau live.
    """
    Isc = float(I_arr[0])
    hi_lim  = 0.99 * Isc                       # avoid the V→0 ambiguity at Isc
    low_top = min(i_low_max, 0.95 * Isc)
    n_low   = max(n // 2, 4)                    # Voc rolloff (gentle, low range)
    n_high  = n - n_low                         # MPP + plateau (dense near Isc)
    low  = np.linspace(0.2, low_top, n_low)
    # cluster the high points toward Isc (exponent < 1 packs near the top)
    high = low_top + (hi_lim - low_top) * np.linspace(0.0, 1.0, n_high) ** 0.6
    return sorted(set(round(float(i), 3) for i in np.concatenate([low, high])
                      if 0.0 < i < Isc))


def write_template_current(path: Path, V_arr, I_arr, mpp, meta: dict,
                           i_low_max: float = 4.0):
    Voc = float(V_arr[-1]); Isc = float(I_arr[0])
    iset = suggested_setpoints_current(V_arr, I_arr, mpp, i_low_max=i_low_max)
    n_high = sum(1 for i in iset if i > i_low_max)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow([f"# Reference curve (CC sweep): {meta['model_name']} | "
                    f"module {meta['module_label']} | G={meta['G']} W/m2 "
                    f"T={meta['T']} C | Voc={Voc:.2f} V Isc={Isc:.3f} A "
                    f"Vmp={mpp.Vmp:.2f} V Imp={mpp.Imp:.3f} A Pmp={mpp.Pmp:.1f} W"])
        w.writerow(["# DL3021 in CC mode: set each I_set, wait ~2 s, record V_meas, I_meas."])
        w.writerow(["# range: LOW = 0-4 A range; HIGH = 0-40 A range "
                    "(disable the input before switching range)."])
        w.writerow(["I_set", "V_model_expected", "range", "V_meas", "I_meas"])
        for i in iset:
            rng = "LOW" if i <= i_low_max else "HIGH"
            w.writerow([f"{i:.3f}", f"{model_voltage_at(V_arr, I_arr, i):.3f}",
                        rng, "", ""])
    print(f"CC data-collection sheet written to: {path}")
    print(f"  {len(iset)} suggested I_set points 0.2 A → {hi_lim_str(Isc)} "
          f"(dense near Isc={Isc:.2f} A, where the plateau lives).")
    print(f"  {len(iset) - n_high} in LOW range (≤{i_low_max:.0f} A, Voc rolloff) | "
          f"{n_high} in HIGH range (>{i_low_max:.0f} A, MPP + plateau).")
    print("  Fill V_meas / I_meas and run --measured (same analysis as CV).")


def hi_lim_str(Isc: float) -> str:
    return f"{0.99 * Isc:.2f} A"


# ─────────────────────────────────────────────────────────────────────────────
# CR SWEEP — suggested resistance setpoints (DL3021 in CR mode)
# ─────────────────────────────────────────────────────────────────────────────

def suggested_setpoints_resistance(V_arr, I_arr, mpp, n: int = 80,
                                   r_high_min: float = 2.0) -> list[float]:
    """
    Resistances to set on the DL3021 in CR mode. n equispaced voltage points
    from 0.01*Voc to 0.99*Voc are converted to R=V/I_model(V). Points with
    R < r_high_min need the DL3021 low CR range (15 Ω) and are dropped; the
    rest fall in the high range (15 kΩ, covers 2 Ω–15 kΩ).
    """
    Voc = float(V_arr[-1])
    pts = []
    for v in np.linspace(0.01 * Voc, 0.99 * Voc, n):
        i = model_current_at(V_arr, I_arr, v)
        if i > 1e-3:
            R = v / i
            if R >= r_high_min:
                pts.append(round(R, 3))
    return sorted(set(pts))


def write_template_resistance(path: Path, V_arr, I_arr, mpp, meta: dict,
                              r_high_min: float = 2.0):
    Voc = float(V_arr[-1]); Isc = float(I_arr[0])
    rset = suggested_setpoints_resistance(V_arr, I_arr, mpp, r_high_min)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow([f"# Reference curve (CR sweep): {meta['model_name']} | "
                    f"module {meta['module_label']} | G={meta['G']} W/m2 "
                    f"T={meta['T']} C | Voc={Voc:.2f} V Isc={Isc:.3f} A "
                    f"Vmp={mpp.Vmp:.2f} V Imp={mpp.Imp:.3f} A Pmp={mpp.Pmp:.1f} W"])
        w.writerow(["# DL3021 in CR mode (HIGH range 2 ohm-15 kohm): set each "
                    "R_set, wait ~2 s, record V_meas, I_meas."])
        w.writerow(["R_set_ohm", "V_model_expected", "I_model_expected",
                    "V_meas", "I_meas"])
        for R in rset:
            i = Isc
            # operating point on the curve at this R (V where R(V)=R)
            v_sol = float(np.interp(
                R,
                *zip(*sorted((vv / ii, vv) for vv, ii in zip(V_arr, I_arr)
                             if ii > 1e-6))))
            i_sol = v_sol / R
            w.writerow([f"{R:.2f}", f"{v_sol:.3f}", f"{i_sol:.4f}", "", ""])
    print(f"CR data-collection sheet written to: {path}")
    print(f"  {len(rset)} suggested R_set points {rset[0]:.2f} → {rset[-1]:.1f} ohm "
          f"(HIGH range; covers V≈10 V → Voc, i.e. MPP + knee + Voc rolloff).")
    print("  The V < 10 V plateau (R < 2 ohm) is NOT covered — needs the low range.")
    print("  Fill V_meas / I_meas and run --measured (same analysis as CV/CC).")


# ─────────────────────────────────────────────────────────────────────────────
# MEASURED MODE — fidelity analysis
# ─────────────────────────────────────────────────────────────────────────────

def read_measured(path: Path) -> list[tuple[float, float]]:
    pts = []
    with open(path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(
                (line for line in f if not line.lstrip().startswith("#"))):
            try:
                v = float(row["V_meas"]); i = float(row["I_meas"])
            except (KeyError, ValueError, TypeError):
                continue
            pts.append((v, i))
    return pts


def analyze(measured: list[tuple[float, float]], V_arr, I_arr, mpp,
            meta: dict, out_dir: Path):
    if not measured:
        sys.exit("ERROR: no rows with numeric V_meas/I_meas in the CSV.")
    Vm = np.array([v for v, _ in measured])
    Im = np.array([i for _, i in measured])
    Iref = np.array([model_current_at(V_arr, I_arr, v) for v in Vm])

    Voc = float(V_arr[-1]); Isc = float(I_arr[0])

    # Drop points where the emulator hit the vertical wall (V collapsed).
    valid = Vm > Voc * 0.1
    n_excl = int((~valid).sum())
    if n_excl:
        print(f"  excluded {n_excl} collapsed point(s) (V < {Voc*0.1:.1f} V) from metrics.")
        Vm, Im, Iref = Vm[valid], Im[valid], Iref[valid]

    err   = Im - Iref
    rmse  = float(np.sqrt(np.mean(err ** 2)))
    nrmse = rmse / Isc * 100 if Isc > 0 else float("nan")
    mae   = float(np.mean(np.abs(err)))

    # Key points: measured vs model Pmp, FF.
    Pm     = Vm * Im
    pmp_m  = float(Pm.max())
    vmp_m  = float(Vm[int(Pm.argmax())])
    imp_m  = float(Im[int(Pm.argmax())])
    ff_m   = pmp_m / (Voc * Isc) * 100 if (Voc * Isc) > 0 else float("nan")
    ff_ref = mpp.Pmp / (Voc * Isc) * 100 if (Voc * Isc) > 0 else float("nan")

    print("\n" + "=" * 60)
    print(f"CURVE FIDELITY — {meta['model_name']}")
    print(f"  module {meta['module_label']} | G={meta['G']} W/m² T={meta['T']} °C")
    print("=" * 60)
    print(f"  measured points:    {len(measured)}")
    print(f"  current NRMSE:      {nrmse:.2f} %  (RMSE {rmse:.4f} A / Isc {Isc:.3f} A)")
    print(f"  current MAE:        {mae:.4f} A")
    print(f"  measured Pmp:       {pmp_m:.1f} W  @ V={vmp_m:.2f} I={imp_m:.3f}")
    print(f"  model Pmp:          {mpp.Pmp:.1f} W  @ V={mpp.Vmp:.2f} I={mpp.Imp:.3f}")
    print(f"  Pmp error:          {(pmp_m - mpp.Pmp) / mpp.Pmp * 100:+.1f} %")
    print(f"  FF measured / model:{ff_m:.1f} % / {ff_ref:.1f} %")
    print("=" * 60)

    _plot(Vm, Im, V_arr, I_arr, mpp, meta, out_dir)

    metrics = {
        **meta, "n_points": len(measured),
        "nrmse_pct": round(nrmse, 3), "rmse_a": round(rmse, 4),
        "mae_a": round(mae, 4),
        "pmp_meas_w": round(pmp_m, 2), "pmp_model_w": round(mpp.Pmp, 2),
        "pmp_err_pct": round((pmp_m - mpp.Pmp) / mpp.Pmp * 100, 2),
        "ff_meas_pct": round(ff_m, 2), "ff_model_pct": round(ff_ref, 2),
    }
    import json
    (out_dir / "curve_fidelity.json").write_text(
        json.dumps(metrics, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nMetrics:   {out_dir / 'curve_fidelity.json'}")


def _plot(Vm, Im, V_arr, I_arr, mpp, meta, out_dir):
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        print("  (matplotlib unavailable — skipping the figure)")
        return
    fig, ax = plt.subplots(figsize=(5.0, 3.6))
    ax.plot(V_arr, I_arr, "-", color="#1f77b4", lw=1.5,
            label=f"Model curve ({meta['model_name']})")
    ax.plot(Vm, Im, "o", color="#d62728", ms=5, label="Measured (DL3000, CV)")
    ax.plot(mpp.Vmp, mpp.Imp, "*", color="#2ca02c", ms=12, label="Model MPP")
    ax.set_xlabel("Voltage [V]"); ax.set_ylabel("Current [A]")
    ax.set_title(f"I-V curve fidelity  |  G={meta['G']} W/m²  T={meta['T']} °C")
    ax.grid(True, alpha=0.3); ax.legend(fontsize=8); ax.set_ylim(bottom=0)
    fig.tight_layout()
    for ext in ("png", "pdf"):
        fig.savefig(out_dir / f"curve_fidelity.{ext}", dpi=150)
    plt.close(fig)
    print(f"Figure:    {out_dir / 'curve_fidelity.png'}")


# ─────────────────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────────────────

def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    ap = argparse.ArgumentParser(
        description="Manual I-V curve fidelity validation (DL3000 in CV).")
    ap.add_argument("--template", action="store_true",
                    help="Generate the data-collection sheet (CSV) and exit.")
    ap.add_argument("--measured", type=Path, default=None,
                    help="CSV with V_meas,I_meas columns to analyze.")
    ap.add_argument("--model", default="single_diode", choices=list(MODELS),
                    help="Reference curve model.")
    ap.add_argument("--sweep", choices=["voltage", "current", "resistance"],
                    default="voltage",
                    help="DL3021 mode for the template: 'voltage' (CV, sweep V), "
                         "'current' (CC, sweep I), or 'resistance' (CR, sweep R). "
                         "--measured is the same for all three.")
    ap.add_argument("--module", default=DEFAULT_MODULE_KEY,
                    help="Module key in the catalog.")
    ap.add_argument("--G", type=float, default=1000.0, help="Irradiance [W/m²].")
    ap.add_argument("--T", type=float, default=25.0, help="Cell temperature [°C].")
    ap.add_argument("--Ns", type=int, default=1, help="Series modules.")
    ap.add_argument("--Np", type=int, default=1, help="Parallel modules.")
    ap.add_argument("--out", type=Path, default=RESULTS_DIR,
                    help="Output directory/file.")
    args = ap.parse_args()

    if not args.template and args.measured is None:
        ap.error("specify --template (generate sheet) or --measured <csv> (analyze).")

    V_arr, I_arr, mpp, model_name = reference_curve(
        args.model, args.module, args.G, args.T, args.Ns, args.Np)
    meta = {
        "model_key": args.model, "model_name": model_name,
        "module_key": args.module,
        "module_label": get_params(args.module)["label"],
        "G": args.G, "T": args.T, "Ns": args.Ns, "Np": args.Np,
    }

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")

    if args.template:
        out = args.out
        if out.suffix.lower() != ".csv":
            out.mkdir(parents=True, exist_ok=True)
            out = out / f"template_{args.sweep}_{args.model}_{ts}.csv"
        if args.sweep == "current":
            write_template_current(out, V_arr, I_arr, mpp, meta)
        elif args.sweep == "resistance":
            write_template_resistance(out, V_arr, I_arr, mpp, meta)
        else:
            write_template(out, V_arr, I_arr, mpp, meta)
        return 0

    out_dir = args.out if args.out.suffix == "" else args.out.parent
    out_dir = out_dir / f"trace_{args.model}_{ts}"
    out_dir.mkdir(parents=True, exist_ok=True)
    analyze(read_measured(args.measured), V_arr, I_arr, mpp, meta, out_dir)
    return 0


if __name__ == "__main__":
    sys.exit(main())
