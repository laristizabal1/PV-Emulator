"""
tools/bench/transition_bench.py
===============================
AUTOMATIC benchmark to DETERMINE the appropriate OPERATING MODE per DUT.

Given a device under test (DUT) connected to the source output, it runs the
matrix of the DUT's candidate MODES (its `candidate_modes` in config/devices.py)
over a mini-profile of hourly transitions of the emulated panel (pvlib I-V curve,
Renogy RNG-100D-SS by default), measuring V_dc/I_dc/P_dc with the EAMonitor. The
verdict reports the RECOMMENDED MODE for that DUT based on the measured fidelity.

(The historical "electrical models" axis is degenerate: the platform uses a
single curve source, pvlib. `--models` is kept for compatibility but in practice
there is only `pvlib`.) Available modes:

    instant     — adaptive order only (Strategy A, V/I rectangle)
    ramp        — A + transition ramp (Strategy B, V/I rectangle)
    ramp+drift  — A + B + drift corrector (Strategy C)
    slope       — emulated linear knee (rapid_ms=200, slope_pct=90)
    cp          — VOLT=Voc / CURR=Isc / POW=Pmp envelope (native CP loop)
    curve       — cp envelope + I(V) loop with the real model curve

Metrics per hourly transition:
    dV_pico   [V] — maximum excursion of V_dc from its settled value in the
                    window after the setpoint change.
    t_estab   [s] — time until V_dc stays within ±band of the final value for 3
                    consecutive samples.
    V_err     [V] — V_final − theoretical Vmp (operating-point fidelity).
    P_err     [%] — steady-state power error vs theoretical Pmp (energy
                    fidelity — the experiment's main metric).

Usage (source connected via USB and the DUT connected to its output):
    python -m tools.bench.transition_bench --dut mppt_inverter   # sweeps its modes
    python -m tools.bench.transition_bench --dut generic         # sweeps all modes
    python -m tools.bench.transition_bench --strategies cp,curve # explicit modes
    python -m tools.bench.transition_bench --dt 30               # longer steps
    python -m tools.bench.transition_bench --dut eload --mock    # no hardware (plumbing)

Results: console table + JSON in tools/bench/bench_results/.
Safety: sets panel-bounded OVP/OCP/OPP before the first run and turns the output
off between strategies.
"""

import sys
import json
import time
import argparse
import logging
import statistics
from pathlib import Path
from datetime import datetime

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from comm.scpi import SCPIController                    # noqa: E402
from comm.monitor import EAMonitor                      # noqa: E402
from tools.bench import transition_strategies           # noqa: E402
from config.modules_catalog import to_module_params, get_params  # noqa: E402
from config.devices import get_device, CATALOG as DUT_CATALOG  # noqa: E402
from models.simplified import SimplifiedModel           # noqa: E402
from models.single_diode import SingleDiodeModel        # noqa: E402
from models.two_diode import TwoDiodeModel              # noqa: E402
from pipeline.profile import _hourly_curve              # noqa: E402

RESULTS_DIR = Path(__file__).resolve().parent / "bench_results"

MODELS = {
    "simplified":   SimplifiedModel,
    "single_diode": SingleDiodeModel,
    "two_diode":    TwoDiodeModel,
}

# Sequence of conditions (G [W/m²], T_amb [°C]) covering morning rise, noon and
# afternoon fall — 5 representative transitions per strategy.
CONDITIONS = [(300, 22.0), (600, 25.0), (900, 28.0), (1000, 30.0),
              (700, 29.0), (400, 26.0)]

STRATEGY_KWARGS = {
    "instant":    dict(transition_mode="instant"),
    "ramp":       dict(transition_mode="ramp"),
    "ramp+drift": dict(transition_mode="ramp+drift", drift_correction=True),
    "slope":      dict(transition_mode="instant", rapid_ms=200, slope_pct=90),
    "cp":         dict(envelope="cp"),
    "curve":      dict(envelope="curve", rapid_ms=200),
}


# ── Test profile ──────────────────────────────────────────────────────────────

def build_profile(module_key: str, model_key: str) -> list[dict]:
    """
    Panel MPP + I-V curve for each condition, with the given curve source (pvlib
    De Soto) — the same flow as pipeline.profile.build with attach_curve=True.
    The I-V curve (via iv_curve()) enables the "cp" and "curve" envelopes.
    """
    params = to_module_params(module_key)
    model = MODELS[model_key](params)
    model.fit()
    cat = get_params(module_key)
    profile = []
    for k, (G, Tamb) in enumerate(CONDITIONS):
        Tcell = Tamb + (cat["noct"] - 20.0) / 800.0 * G   # NOCT model
        mpp = model.get_mpp(G, Tcell)
        step = {
            "V_set": round(mpp.Vmp, 3),
            "I_set": round(mpp.Imp, 3),
            "P_set": round(mpp.Pmp, 2),
            "label": f"h{k:02d}",
            "Gpoa":  G,
            "Tcell": round(Tcell, 1),
        }
        step.update(_hourly_curve(model, G, Tcell, Ns_arr=1, Np_arr=1,
                                  curve_pts=40, V_max=60.0, I_max=170.0))
        profile.append(step)
    return profile


# ── Metrics ───────────────────────────────────────────────────────────────────

def analyze_run(buffer: list[dict], profile: list[dict], t0: float,
                dt_s: float, band_v: float, window_s: float) -> list[dict]:
    """
    Metrics per transition. Step k of the profile starts at t0 + k·dt_s
    (run_profile compensates command overhead, error < 50 ms).
    """
    out = []
    for k in range(1, len(profile)):
        T_k   = t0 + k * dt_s
        V_set = profile[k]["V_set"]
        win   = [s for s in buffer if T_k <= s["timestamp"] < T_k + min(window_s, dt_s)]
        stead = [s for s in buffer if T_k + 0.6 * dt_s <= s["timestamp"] < T_k + dt_s]
        if len(win) < 2 or not stead:
            out.append({"paso": k, "V_set": V_set, "error": "no samples"})
            continue
        v_final = statistics.median(s["V_dc"] for s in stead)
        dv_pico = max(abs(s["V_dc"] - v_final) for s in win)

        t_estab = None
        consec = 0
        for s in win + stead:
            if abs(s["V_dc"] - v_final) <= band_v:
                consec += 1
                if consec >= 3:
                    t_estab = s["timestamp"] - T_k
                    break
            else:
                consec = 0
        i_final = statistics.median(s["I_dc"] for s in stead)
        p_final = statistics.median(s.get("P_dc", s["V_dc"] * s["I_dc"])
                                    for s in stead)
        P_set   = profile[k]["P_set"]
        out.append({
            "paso":      k,
            "V_set":     V_set,
            "I_set":     profile[k]["I_set"],
            "P_set":     P_set,
            "V_final":   round(v_final, 3),
            "I_final":   round(i_final, 3),
            "P_final":   round(p_final, 2),
            "modo":      SCPIController.infer_mode(v_final, i_final,
                                                   V_set, profile[k]["I_set"]),
            "dV_pico":   round(dv_pico, 3),
            "t_estab":   round(t_estab, 2) if t_estab is not None else None,
            "V_err":     round(v_final - V_set, 3),
            "P_err_pct": round((p_final - P_set) / P_set * 100, 2)
                         if P_set > 0 else None,
        })
    return out


def summarize(transitions: list[dict], dt_s: float) -> dict:
    ok = [t for t in transitions if "error" not in t]
    if not ok:
        return {"valido": False}
    t_estabs = [t["t_estab"] if t["t_estab"] is not None else dt_s for t in ok]
    p_errs   = [abs(t["P_err_pct"]) for t in ok if t.get("P_err_pct") is not None]
    return {
        "valido":          True,
        "n_transiciones":  len(ok),
        "dV_pico_medio":   round(statistics.mean(t["dV_pico"] for t in ok), 3),
        "dV_pico_max":     round(max(t["dV_pico"] for t in ok), 3),
        "t_estab_medio":   round(statistics.mean(t_estabs), 2),
        "no_estabilizo":   sum(1 for t in ok if t["t_estab"] is None),
        "V_err_medio":     round(statistics.mean(abs(t["V_err"]) for t in ok), 3),
        "P_err_medio_pct": round(statistics.mean(p_errs), 2) if p_errs else None,
    }


# ── Mock (no hardware) ────────────────────────────────────────────────────────

class FakeSerial:
    """Simulated serial: answers MEAS with a plausible V based on the last VOLT."""
    is_open = True
    timeout = 2.0

    def __init__(self):
        self.last_v = 0.0
        self._next = b""

    def write(self, data: bytes):
        cmd = data.decode("ascii").strip()
        if cmd.startswith("VOLT ") and not cmd.startswith("VOLT:"):
            try:
                self.last_v = float(cmd.split()[1])
            except ValueError:
                pass
        elif cmd.endswith("?"):
            self._next = f"{self.last_v * 0.97:.3f} V\n".encode("ascii")

    def readline(self) -> bytes:
        nxt, self._next = self._next, b""
        return nxt

    def close(self):
        self.is_open = False


def install_mock(ctrl: SCPIController, monitor: EAMonitor):
    """Synthetic V_dc: first order toward 0.97·V_set + light noise."""
    import random
    ctrl._ser = FakeSerial()
    state = {"v": 0.0}

    def _mock_read():
        target = (ctrl._last_v_set or 0.0) * 0.97
        state["v"] += (target - state["v"]) * 0.35      # tau ~ 0.7 s at 300 ms
        v = state["v"] + random.gauss(0, 0.03)
        i = (ctrl._last_i_set or 0.0) * 0.95
        return {"timestamp": round(time.time(), 3),
                "V_dc": round(v, 4), "I_dc": round(i, 4),
                "P_dc": round(v * i, 4)}

    monitor._read_once = _mock_read


# ── Main ──────────────────────────────────────────────────────────────────────

def parse_args():
    p = argparse.ArgumentParser(description="CV->CC transition benchmark")
    p.add_argument("--port", default=None, help="COM port (default: auto-detect)")
    p.add_argument("--module", default="renogy_rng_100d_ss", help="Catalog key")
    p.add_argument("--dut", default=None, choices=list(DUT_CATALOG),
                   help="DUT under test (config/devices.py). If given and "
                        "--strategies is not passed, the modes to sweep are the "
                        "DUT's candidate_modes.")
    p.add_argument("--models", default="all",
                   help=f"(Compat) models to test: {','.join(MODELS)} or 'all'. "
                        f"The platform uses a single curve source (pvlib).")
    p.add_argument("--dt", type=float, default=20.0,
                   help="Seconds per hourly step (default 20)")
    p.add_argument("--cooldown", type=float, default=10.0,
                   help="Pause with output OFF between strategies (default 10 s)")
    p.add_argument("--band", type=float, default=0.5,
                   help="Settling band ±V (default 0.5)")
    p.add_argument("--window", type=float, default=8.0,
                   help="Peak search window after the transition (default 8 s)")
    p.add_argument("--strategies", default=None,
                   help=f"Modes to sweep, comma-separated. Available: "
                        f"{','.join(STRATEGY_KWARGS)}. If omitted: the --dut "
                        f"candidate_modes, or instant,ramp,slope,cp,curve.")
    p.add_argument("--mock", action="store_true",
                   help="No hardware: simulated serial and measurements")
    p.add_argument("--debug", action="store_true", help="DEBUG logging of comm.scpi")
    return p.parse_args()


def main():
    args = parse_args()
    logging.basicConfig(
        level=logging.DEBUG if args.debug else logging.INFO,
        format="%(asctime)s %(name)s %(message)s")

    # Resolve modes to sweep: explicit --strategies > the DUT's candidate_modes
    # > default list. Determining a DUT's appropriate mode = sweep its
    # candidate_modes and rank by fidelity.
    device = get_device(args.dut) if args.dut else None
    if args.strategies:
        strat_src = args.strategies
    elif device is not None:
        strat_src = ",".join(device.candidate_modes)
    else:
        strat_src = "instant,ramp,slope,cp,curve"
    strategies = [s.strip() for s in strat_src.split(",") if s.strip()]
    unknown = [s for s in strategies if s not in STRATEGY_KWARGS]
    if unknown:
        sys.exit(f"Unknown modes: {unknown}. "
                 f"Valid: {list(STRATEGY_KWARGS)}")

    model_keys = (list(MODELS) if args.models.strip().lower() == "all"
                  else [m.strip() for m in args.models.split(",") if m.strip()])
    unknown_m = [m for m in model_keys if m not in MODELS]
    if unknown_m:
        sys.exit(f"Unknown models: {unknown_m}. "
                 f"Valid: {list(MODELS)} or 'all'")

    cat = get_params(args.module)
    dt_ms = int(args.dt * 1000)
    n_runs = len(model_keys) * len(strategies)
    est_min = n_runs * (len(CONDITIONS) * args.dt + args.cooldown) / 60.0

    print(f"\nEmulated panel: {cat['label']}")
    if device is not None:
        print(f"DUT: {device.label}  "
              f"(MPPT={'yes' if device.has_mppt else 'no'}, "
              f"recommended envelope={device.envelope})")
        print(f"Modes to sweep (candidate_modes): {strategies}")
    else:
        print(f"DUT: unspecified (--dut) — sweeping modes: {strategies}")
    print(f"Runs: {len(model_keys)} x {len(strategies)} "
          f"= {n_runs} (~{est_min:.0f} min)")

    ctrl = SCPIController(port=args.port or None) if args.port else SCPIController()
    monitor = EAMonitor(ctrl)

    if args.mock:
        print("\n[MOCK] No hardware — simulated serial and measurements. "
              "Validates the flow and metrics, NOT the transient physics.")
        install_mock(ctrl, monitor)
    else:
        idn = ctrl.connect()
        print(f"\nConnected: {idn.strip()[:70]}")
        # Panel-bounded protections BEFORE any output
        ctrl.set_protections(ovp=cat["Voc"] * 1.10,
                             ocp=cat["Isc"] * 1.15,
                             opp=cat["Voc"] * cat["Isc"])
        print(f"Protections: OVP={cat['Voc']*1.10:.1f} V  "
              f"OCP={cat['Isc']*1.15:.2f} A  OPP={cat['Voc']*cat['Isc']:.0f} W")

    # Warm up the deferred import of EAMonitor._get_step(): the first call
    # imports hmi.callbacks.scpi_cb (dash chain, 1-10 s cold) INSIDE the polling
    # thread, stealing the first samples of the first run. Importing it here
    # makes it instantaneous during the bench.
    try:
        import hmi.callbacks.scpi_cb  # noqa: F401
    except Exception:
        pass

    results:  dict = {}
    profiles: dict = {}
    run_idx = 0
    for model_key in model_keys:
        profile = build_profile(args.module, model_key)
        profiles[model_key] = profile
        results[model_key]  = {}

        print(f"\n{'#'*64}\nMODEL: {model_key}\n{'#'*64}")
        print(f"Profile ({len(profile)} steps, {len(profile)-1} transitions, "
              f"{args.dt:.0f} s/step):")
        for s in profile:
            curva = "yes" if s.get("curve_v") else "NO"
            print(f"  {s['label']}  V_set={s['V_set']:6.2f} V  "
                  f"I_set={s['I_set']:5.3f} A  P={s['P_set']:6.1f} W  "
                  f"Voc={s.get('Voc', '--')}  Isc={s.get('Isc', '--')}  "
                  f"curve={curva}")

        for strat in strategies:
            run_idx += 1
            print(f"\n{'='*64}\n[{run_idx}/{n_runs}] {model_key} / {strat}"
                  f"  ({STRATEGY_KWARGS[strat]})\n{'='*64}")
            monitor.start(meta={"bench": "transition", "estrategia": strat,
                                "modelo": model_key,
                                "modulo": args.module, "dt_ms": dt_ms})  # data keys
            time.sleep(0.7)                      # first monitor cycle
            t0 = time.time()
            try:
                transition_strategies.run_profile(ctrl, profile, dt_ms,
                                 monitor=monitor, **STRATEGY_KWARGS[strat])
            except Exception as exc:
                monitor.stop()
                print(f"[ERROR] {model_key}/{strat}: {exc}")
                results[model_key][strat] = {"error": str(exc)}
                continue
            monitor.stop()
            buffer = monitor.get_buffer()

            if buffer and not args.mock:
                i_max = max(s["I_dc"] for s in buffer)
                if i_max < 0.05:
                    print("[WARN] I_dc ~ 0 A for the whole run: the DUT is not "
                          "drawing. Is the DUT connected and (if applicable) the "
                          "grid present?")

            transitions = analyze_run(buffer, profile, t0, args.dt,
                                      args.band, args.window)
            summary = summarize(transitions, args.dt)
            results[model_key][strat] = {
                "resumen": summary, "transiciones": transitions,
                "n_muestras": len(buffer)}

            for t in transitions:
                if "error" in t:
                    print(f"  step {t['paso']}: {t['error']}")
                else:
                    te = (f"{t['t_estab']:5.2f} s"
                          if t["t_estab"] is not None else "  NO  ")
                    pe = (f"{t['P_err_pct']:+6.1f}%"
                          if t.get("P_err_pct") is not None else "   --  ")
                    print(f"  step {t['paso']}: V_set={t['V_set']:6.2f}  "
                          f"V_final={t['V_final']:6.2f} ({t['modo']:>5})  "
                          f"dV_pico={t['dV_pico']:5.2f} V  t_estab={te}  "
                          f"P_err={pe}")
            if summary.get("valido"):
                print(f"  -> dV_pico mean={summary['dV_pico_medio']} V  "
                      f"V_err mean={summary['V_err_medio']} V  "
                      f"P_err mean={summary['P_err_medio_pct']} %  "
                      f"t_estab mean={summary['t_estab_medio']} s")

            if run_idx < n_runs:
                if not args.mock:
                    ctrl.output_off()
                print(f"  ... cooldown {args.cooldown:.0f} s (output OFF)")
                time.sleep(args.cooldown)

    if not args.mock:
        ctrl.output_off()

    # ── Verdict ───────────────────────────────────────────────────────────────
    # Fidelity first (P_err, then V_err), transient as tie-breaker.
    def _rank_key(kv):
        s = kv[1]
        return (s.get("P_err_medio_pct") if s.get("P_err_medio_pct")
                is not None else 999.0,
                s["V_err_medio"], s["dV_pico_medio"])

    print(f"\n{'='*64}\nVEREDICTO\n{'='*64}")
    print("Ranking by fidelity: 1st power error, 2nd voltage error vs Vmp, "
          "3rd transient.\n")

    global_best = None   # (ranking_key, model, strategy)
    for model_key in model_keys:
        valid = {k: v["resumen"] for k, v in results[model_key].items()
                 if v.get("resumen", {}).get("valido")}
        print(f"--- {model_key} ---")
        if not valid:
            print("  no valid metrics (check connection/load or errors above)\n")
            continue
        ranked = sorted(valid.items(), key=_rank_key)
        print(f"  {'strategy':<12} {'P_err mean':>10} {'V_err mean':>10} "
              f"{'dV_pico mean':>12} {'t_estab mean':>12} {'no settle':>10}")
        for name, s in ranked:
            pe = (f"{s['P_err_medio_pct']:>8.1f} %"
                  if s.get("P_err_medio_pct") is not None else "      -- ")
            print(f"  {name:<12} {pe} {s['V_err_medio']:>8.2f} V "
                  f"{s['dV_pico_medio']:>10.2f} V "
                  f"{s['t_estab_medio']:>10.2f} s {s['no_estabilizo']:>10}")
        best_name, best_sum = ranked[0]
        print(f"  -> best for {model_key}: {best_name}\n")
        key = _rank_key((best_name, best_sum))
        if global_best is None or key < global_best[0]:
            global_best = (key, model_key, best_name)

    if global_best is not None:
        _, g_model, g_strat = global_best
        dut_txt = f"the DUT '{device.label}'" if device is not None else "this DUT"
        print(f"Recommended operating mode for {dut_txt}: {g_strat}")
        if device is not None and g_strat != device.envelope:
            print(f"  (note: differs from the recommended starting envelope "
                  f"'{device.envelope}' — the evidence suggests '{g_strat}')")
        if g_strat in ("cp", "curve"):
            print(f"Configure: run_profile(..., envelope=\"{g_strat}\") and "
                  f"build the profile with build(..., attach_curve=True).")
        elif g_strat == "slope":
            print("Configure: run_profile(..., rapid_ms=200, slope_pct=90) "
                  "(the HMI does not expose it yet — pass via code or CLI).")
        else:
            print(f"Configure: $env:PV_TRANSITION_MODE = \"{g_strat}\" "
                  f"before launching app.py")
    else:
        print("No combination produced valid metrics — check connection/load.")

    RESULTS_DIR.mkdir(exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out = RESULTS_DIR / f"transition_bench_{ts}.json"
    payload = {
        "fecha": datetime.now().isoformat(timespec="seconds"),  # data keys kept
        "modulo": args.module, "modelos": model_keys,
        "dut": device.key if device is not None else None,
        "modo_recomendado": global_best[2] if global_best is not None else None,
        "dt_s": args.dt, "band_v": args.band,
        "window_s": args.window, "mock": args.mock,
        "perfiles": profiles, "resultados": results,
    }
    out.write_text(json.dumps(payload, indent=2, ensure_ascii=False),
                   encoding="utf-8")
    print(f"\nResults saved to {out}")

    if not args.mock:
        ctrl.disconnect()


if __name__ == "__main__":
    main()
