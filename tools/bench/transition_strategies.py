"""
tools/bench/transition_strategies.py
=====================================
Inter-setpoint TRANSITION STRATEGIES for the "direct" envelope, extracted from
comm/scpi.py. These are a RESEARCH comparison exercised only by the bench tools
(transition_bench, smoke_transition, quick_profile_test) — the product paths use
the cp/curve envelopes, which manage their own dynamics and never needed them.

`run_profile(ctrl, ...)` is the single entry point the benches call:
  - envelope="cp"/"curve"  → delegates to ctrl.run_profile (comm/scpi.py).
  - envelope="direct"      → runs the strategy step-loop here.

Strategies (direct envelope):
  instant     — one set_output_fast per step, then wait.
  ramp        — linear ramp between setpoints (transition_to_setpoint).
  ramp+drift  — ramp + a 200 ms corrector that resends if V drifts past the
                threshold (needs an EAMonitor with get_latest(); degrades to a
                passive wait without one).
  slope       — emulate the PV knee: within [slope_pct%·V_set, V_set] the sent
                current decays linearly to 0 so an MPPT converges (needs
                rapid_ms > 0). Selected via transition_mode="instant" + slope_pct.

It drives the controller through its public methods only; the parameters that
used to live in config/hardware.py are defaults here now.
"""

from __future__ import annotations

import time
import logging
from datetime import datetime

from config.hardware import DT_MIN

logger = logging.getLogger(__name__)

# Defaults (formerly config/hardware.py — only the bench reads them now).
RAMP_STEPS        = 10
RAMP_STEP_DT      = 0.2
DRIFT_THRESHOLD_V = 1.0


def transition_to_setpoint(ctrl, v_new: float, i_new: float,
                           steps: int = RAMP_STEPS,
                           step_dt: float = RAMP_STEP_DT) -> int:
    """
    Linear ramp from ctrl's last setpoint to (v_new, i_new) over `steps` steps,
    `step_dt` s apart (clamped to DT_MIN), so an MPPT follows without the abrupt
    CV→CC jump. Sends the target directly if there is no prior setpoint. Returns
    the number of steps sent.
    """
    step_dt = max(float(step_dt), DT_MIN / 1000.0)
    steps   = max(int(steps), 1)
    v0, i0  = ctrl._last_v_set, ctrl._last_i_set

    if v0 is None or i0 is None:
        ctrl.set_output_fast(v_new, i_new, on=False)
        return 1

    was_running = ctrl._running
    sent = 0
    for k in range(1, steps + 1):
        if was_running and not ctrl._running:
            break
        t0   = time.perf_counter()
        frac = k / steps
        ctrl.set_output_fast(v0 + (v_new - v0) * frac,
                             i0 + (i_new - i0) * frac, on=False)
        sent += 1
        if k < steps:
            wait = step_dt - (time.perf_counter() - t0)
            if wait > 0:
                time.sleep(wait)
    return sent


def run_profile(ctrl, profile: list[dict], dt_ms: int, *,
                transition_mode: str = "instant",
                ramp_steps: int = RAMP_STEPS,
                ramp_step_dt: float = RAMP_STEP_DT,
                drift_correction: bool = False,
                drift_threshold_v: float = DRIFT_THRESHOLD_V,
                slope_pct: int = 0,
                rapid_ms: int = 0,
                envelope: str = "direct",
                curve_seed_hold_ms: int | None = None,
                curve_drive: str = "voltage",
                monitor=None,
                progress_cb=None,
                verbose: bool = False):
    """
    Bench entry point. cp/curve delegate to ctrl.run_profile; the direct envelope
    runs the strategy step-loop (instant / ramp / ramp+drift / slope). See module
    docstring. Returns None.
    """
    if envelope in ("cp", "curve"):
        return ctrl.run_profile(profile, dt_ms, progress_cb=progress_cb,
                                verbose=verbose, rapid_ms=rapid_ms,
                                curve_seed_hold_ms=curve_seed_hold_ms,
                                envelope=envelope, curve_drive=curve_drive)
    if not profile:
        return

    mode = (transition_mode or "instant").strip().lower()
    if mode not in ("instant", "ramp", "ramp+drift"):
        logger.warning("transition_mode '%s' unknown — using 'instant'", mode)
        mode = "instant"
    n_ramp   = int(ramp_steps)
    dt_ramp  = max(float(ramp_step_dt), DT_MIN / 1000.0)
    do_drift = bool(drift_correction) and mode == "ramp+drift"
    v_thresh = float(drift_threshold_v)

    ctrl._running = True
    dt_s    = max(int(dt_ms), DT_MIN) / 1000.0
    rapid_s = max(rapid_ms, 0) / 1000.0
    total   = len(profile)

    # ramp and the fast (slope) sub-loop share the intra-step slot: rapid wins.
    use_ramp = mode in ("ramp", "ramp+drift") and rapid_s == 0
    if use_ramp and n_ramp * dt_ramp > dt_s * 0.8:
        use_ramp = False                       # ramp would eat the step
    if do_drift and rapid_s > 0:
        do_drift = False

    if verbose:
        print(f"{'Step':>5} {'Hour':>6} {'V_set':>7} {'I_set':>7} "
              f"{'P_set':>7} {'Mode':>6} {'V_meas':>7} {'I_meas':>7} {'Err_V':>7}")
        print("-" * 69)

    out_on = profile[0]["P_set"] > 0
    ctrl.set_output_fast(profile[0]["V_set"], profile[0]["I_set"], on=out_on)
    if not out_on:
        ctrl.output_off()

    for i, step in enumerate(profile):
        if not ctrl._running:
            break
        t_step_start = time.perf_counter()
        V_s = step["V_set"]
        I_s = step["I_set"]
        want_on = step["P_set"] > 0

        # ramp only between active setpoints (day→day); not on OFF↔ON edges.
        if use_ramp and out_on and want_on:
            transition_to_setpoint(ctrl, V_s, I_s, steps=n_ramp, step_dt=dt_ramp)
        else:
            ctrl.set_output_fast(V_s, I_s, on=False)

        if want_on != out_on:
            if want_on:
                ctrl.send("OUTP ON")
            else:
                ctrl.output_off()
            out_on = want_on

        if progress_cb:
            try:
                progress_cb(i, total, step)
            except Exception:
                pass

        if rapid_s > 0 and out_on:
            # ── adaptive slope: emulate the PV knee so the MPPT converges ──
            v_knee = V_s * slope_pct / 100.0 if (slope_pct > 0 and I_s > 0) else 0.0
            t_end  = t_step_start + dt_s - 0.010
            while time.perf_counter() < t_end:
                if not ctrl._running:
                    break
                tc = time.perf_counter()
                if v_knee > 0:
                    try:
                        v_now = float(
                            ctrl.query_fast("MEAS:VOLT?").strip().split()[0])
                    except (ValueError, TypeError, IndexError):
                        v_now = 0.0          # lost read must not kill the thread
                    if v_now >= v_knee:
                        I_eff = I_s * max(0.0, (V_s - v_now) / (V_s - v_knee))
                    else:
                        I_eff = I_s
                else:
                    I_eff = I_s
                ctrl.set_output_fast(V_s, I_eff, on=False)
                wait_cycle = rapid_s - (time.perf_counter() - tc)
                if wait_cycle > 0:
                    time.sleep(wait_cycle)
        elif do_drift and out_on:
            # ── drift corrector: resend if V_dc drifts past the threshold ──
            # Reads the EAMonitor cache (no serial). No monitor → passive wait.
            t_end = t_step_start + dt_s - 0.010
            while time.perf_counter() < t_end:
                if not ctrl._running:
                    break
                tc = time.perf_counter()
                v_meas = None
                try:
                    if monitor is not None and getattr(monitor, "active", False):
                        m = monitor.get_latest()
                        if m:
                            v_meas = m.get("V_dc")
                except Exception:
                    v_meas = None
                if v_meas is not None and abs(v_meas - V_s) > v_thresh:
                    ctrl.set_output_fast(V_s, I_s, on=False)
                wait_cycle = 0.2 - (time.perf_counter() - tc)
                if wait_cycle > 0:
                    time.sleep(wait_cycle)
        else:
            wait = dt_s - (time.perf_counter() - t_step_start)
            if wait > 0:
                time.sleep(wait)

        if verbose:
            v_raw = ctrl.query_fast("MEAS:VOLT?").strip().split()[0]
            i_raw = ctrl.query_fast("MEAS:CURR?").strip().split()[0]
            try:
                vm = float(v_raw); im = float(i_raw)
            except ValueError:
                vm = im = float("nan")
            mode_label = ctrl.infer_mode(vm, im, V_s, I_s)
            hora = step.get("label", "")
            err_v = (vm - V_s) / V_s * 100 if V_s > 0 else 0.0
            print(f"{i+1:>5} {hora:>6} {V_s:>7.2f} {I_s:>7.3f} "
                  f"{step['P_set']:>7.1f} {mode_label:>6} "
                  f"{vm:>7.2f} {im:>7.3f} {err_v:>+6.1f}%")

    ctrl.output_off()
    ctrl._running = False
