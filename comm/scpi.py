"""
comm/scpi.py
============
Controller for the EA-PS 10060-170 source via SCPI ASCII over USB/COM.

Usage:
    from comm.scpi import SCPIController, list_ports

    ports = list_ports()
    ctrl  = SCPIController(port="COM3")
    idn   = ctrl.connect()
    ctrl.set_output_fast(40.0, 10.0)
    ctrl.run_profile(profile, dt_ms=200)
    ctrl.disconnect()

FIXES APPLIED:
    1. SYST:REM:TRAN ON -> SYST:LOCK ON (correct command for EA-PS 10060-170)
    2. threading.Lock() -> threading.RLock() to avoid deadlock between threads
    3. SYST:LOCK ON sent in connect() to enable remote from the start
    4. SYST:LOCK OFF sent in disconnect() to release correctly
    5. SYST:LOCK ON removed from the run_profile() loop (already active since connect)
"""

import time
import bisect
import logging
import threading
from datetime import datetime

from config.hardware import (V_MAX, I_MAX, P_MAX, DT_MIN, DEFAULT_PORT,
                             DEFAULT_BAUD, DEFAULT_TIMEOUT)

logger = logging.getLogger(__name__)

try:
    import serial
    import serial.tools.list_ports
    SERIAL_AVAILABLE = True
except ImportError:
    SERIAL_AVAILABLE = False


def list_ports() -> list[dict]:
    """List available COM ports. Returns a list for dcc.Dropdown."""
    if not SERIAL_AVAILABLE:
        return [{"label": "pyserial unavailable — pip install pyserial",
                 "value": "NONE"}]
    ports = list(serial.tools.list_ports.comports())
    if not ports:
        return [{"label": "No COM ports detected", "value": "NONE"}]
    return [
        {"label": f"{p.device}  —  {p.description[:40]}", "value": p.device}
        for p in sorted(ports, key=lambda p: p.device)
    ]


def available_port_devices() -> list[str]:
    """List of present serial devices (COM3, /dev/ttyUSB0, ...)."""
    if not SERIAL_AVAILABLE:
        return []
    return sorted(p.device for p in serial.tools.list_ports.comports())


def autodetect_port(preferred: str | None = None) -> str | None:
    """
    Resolve the serial port to use, portable across machines/OS.

    1. If `preferred` exists among the detected ports, it is used.
    2. Otherwise the first available port is returned (e.g. on another PC the
       port will be COM4/COM5 or /dev/ttyUSB0 instead of the configured one).
    3. If there is no port, returns None.
    """
    devices = available_port_devices()
    if not devices:
        return None
    if preferred and preferred in devices:
        return preferred
    return devices[0]


class SCPIController:
    """
    Controller for the EA-PS 10060-170 source over USB/COM using SCPI ASCII.
    Thread-safe via an internal RLock (reentrant — avoids deadlock).
    """

    def __init__(self, port: str = DEFAULT_PORT, baud: int = DEFAULT_BAUD,
                 timeout: float = DEFAULT_TIMEOUT):
        self.port    = port
        self.baud    = baud
        self.timeout = timeout
        self._ser      = None
        self._running  = False
        # FIX #2: RLock (reentrant) instead of Lock to avoid deadlock when
        # output_off() and set_output_fast() are called from the same thread
        self._lock     = threading.RLock()
        # Last setpoint sent — used by the adaptive command order of
        # set_output_fast() and as the ramp origin in transition_to_setpoint().
        # None until the first send.
        self._last_v_set: float | None = None
        self._last_i_set: float | None = None
        self._last_cmd_order: str = "-"

    # ── Connection ──────────────────────────────────────────────────────────────

    def connect(self) -> str:
        if not SERIAL_AVAILABLE:
            raise RuntimeError("pyserial not installed: pip install pyserial")
        # Portable auto-detection: if the configured port does not exist on this
        # machine, fall back to the first available one (another PC/OS uses another name).
        resolved = autodetect_port(self.port)
        if resolved is None:
            raise RuntimeError(
                "No serial port detected. Connect the EA source "
                "or set PV_SERIAL_PORT.")
        if resolved != self.port:
            print(f"[scpi] Port '{self.port}' unavailable; "
                  f"using '{resolved}'.")
            self.port = resolved
        self._ser = serial.Serial(self.port, self.baud, timeout=self.timeout)
        time.sleep(0.3)
        idn = self.query("*IDN?").strip()
        time.sleep(0.1)
        # FIX #1 and #4: SYST:LOCK ON is the correct command to enable digital
        # remote control on the EA-PS 10060-170 (not SYST:REM:TRAN ON).
        # Sending it here keeps it active for the whole session.
        # The source display should show "Remote: USB" when it receives it.
        self.send("SYST:LOCK ON")
        return idn

    def disconnect(self):
        if self.connected:
            # FIX: release remote control before closing so the source returns
            # to local mode and the physical knobs regain control. try/finally
            # (Fase 1.1): if OUTP OFF / SYST:LOCK OFF raise (equipment yanked
            # mid-session, serial write error), the port must still be closed.
            try:
                self.send("OUTP OFF")
                self.send("SYST:LOCK OFF")
            finally:
                self._ser.close()

    @property
    def connected(self) -> bool:
        return bool(self._ser and self._ser.is_open)

    # ── Communication ───────────────────────────────────────────────────────────

    def send(self, cmd: str):
        with self._lock:
            if not self.connected:
                raise RuntimeError(
                    f"Port {self.port} is not open. "
                    "Connect first with the '*IDN?' button."
                )
            self._ser.write((cmd + "\n").encode("ascii"))
            time.sleep(0.05)

    def query(self, cmd: str) -> str:
        with self._lock:
            if self.connected:
                self._ser.write((cmd + "\n").encode("ascii"))
                time.sleep(0.05)
                return self._ser.readline().decode("ascii", errors="replace")
        return ""

    def query_fast(self, cmd: str, timeout: float = 0.15,
                   settle: float = 0.05) -> str:
        """
        Short-timeout query — for MEAS reads of the monitor and the I(V) loop.

        The normal query() uses self.timeout = 2.0 s. If the source does not
        respond (e.g. MEAS:ALL? unsupported), readline() blocks for the full 2 s,
        stalling the monitor thread and, if called from a Dash callback, blocking
        the Flask worker.

        query_fast() reduces the timeout to 150 ms inside the RLock:
          - Normal response (~50 ms)  → readline returns immediately.
          - No response               → readline returns "" in 150 ms.
        The original timeout is always restored (finally block).

        settle: write→readline wait. readline() already blocks until the newline
            or the timeout, so this wait is NOT needed for correctness — it is
            just a cushion. The monitor keeps the historical value (0.05 s); the
            I(V) loop, which needs maximum bandwidth, lowers it to ~2 ms
            (settle=0.002) and lets readline govern the source's real latency.
            It is the loop's bandwidth lever.
        """
        with self._lock:
            if not self.connected:
                return ""
            old_timeout      = self._ser.timeout
            self._ser.timeout = timeout
            try:
                self._ser.write((cmd + "\n").encode("ascii"))
                if settle > 0:
                    time.sleep(settle)
                return self._ser.readline().decode("ascii", errors="replace")
            finally:
                self._ser.timeout = old_timeout

    # ── Output control ──────────────────────────────────────────────────────────

    def set_output_fast(self, V: float, I: float, on: bool = True):
        """
        Optimized set_output for real-time profiles.
        Sends VOLT and CURR with minimal delay (2ms) instead of 50ms.
        Requires SYST:LOCK ON to have been sent first (connect() does it).

        ADAPTIVE command order (anti CV→CC transient):
          - If the new current DROPS (I <= last I sent): CURR first, VOLT after.
            The restrictive limit is applied before moving the voltage, avoiding
            the intermediate state (V_new, I_old_high) in which the source can
            jump to CV and produce a voltage spike on the MPPT.
          - If the current RISES: VOLT first, CURR after (the intermediate state
            (V_new, I_old_low) is the most restrictive possible).
        The order used is stored in self._last_cmd_order for diagnostic logging.
        The (V, I, on) signature does not change — existing callers are untouched.
        """
        V_safe = min(max(float(V), 0.0), V_MAX)
        I_safe = min(max(float(I), 0.0), I_MAX)

        with self._lock:
            if self.connected:
                curr_first = (self._last_i_set is not None
                              and I_safe <= self._last_i_set)
                if curr_first:
                    self._ser.write(f"CURR {I_safe:.3f}\n".encode("ascii"))
                    time.sleep(0.002)
                    self._ser.write(f"VOLT {V_safe:.3f}\n".encode("ascii"))
                    time.sleep(0.002)
                else:
                    self._ser.write(f"VOLT {V_safe:.3f}\n".encode("ascii"))
                    time.sleep(0.002)
                    self._ser.write(f"CURR {I_safe:.3f}\n".encode("ascii"))
                    time.sleep(0.002)
                self._last_cmd_order = "CURR->VOLT" if curr_first else "VOLT->CURR"
                self._last_v_set = V_safe
                self._last_i_set = I_safe
                if on:
                    self._ser.write(b"OUTP ON\n")
                    time.sleep(0.002)

    def set_current_fast(self, I: float):
        """
        Update ONLY the current setpoint with minimal delay.
        Used by the I(V) curve emulation loop: the voltage stays fixed at Voc
        (ceiling) and only CURR moves each cycle — one command per cycle instead
        of two, half the latency of set_output_fast.
        """
        I_safe = min(max(float(I), 0.0), I_MAX)
        with self._lock:
            if self.connected:
                self._ser.write(f"CURR {I_safe:.3f}\n".encode("ascii"))
                time.sleep(0.002)
                self._last_i_set = I_safe

    def set_voltage_fast(self, V: float):
        """
        Update ONLY the voltage setpoint with minimal delay.
        Mirror of set_current_fast(): used by the I->V curve loop (curve_drive=
        "current"), where the source acts as a VOLTAGE source governed by the
        curve — the current ceiling stays at Isc and only VOLT moves each cycle.
        """
        V_safe = min(max(float(V), 0.0), V_MAX)
        with self._lock:
            if self.connected:
                self._ser.write(f"VOLT {V_safe:.3f}\n".encode("ascii"))
                time.sleep(0.002)
                self._last_v_set = V_safe

    def set_power_fast(self, P: float):
        """
        Set the power setpoint (native CP loop of the PS 10000) with minimal
        delay. With VOLT=Voc and CURR=Isc as ceilings, the POW=Pmp limit creates
        the CC → hyperbola V·I=Pmp → CV envelope that approximates the PV curve
        and guarantees the hour's power in hardware.
        """
        P_safe = min(max(float(P), 0.0), P_MAX)
        with self._lock:
            if self.connected:
                self._ser.write(f"POW {P_safe:.1f}\n".encode("ascii"))
                time.sleep(0.002)

    @staticmethod
    def _interp_current(v: float, curve_v: list, curve_i: list) -> float:
        """
        Linear I(V) interpolation over the step's curve table (stdlib only — the
        comm layer does not depend on numpy). curve_v must be ascending, as
        produced by iv_curve().
        """
        if not curve_v or len(curve_v) != len(curve_i):
            return 0.0
        if v <= curve_v[0]:
            return curve_i[0]
        if v >= curve_v[-1]:
            return curve_i[-1]
        lo = bisect.bisect_right(curve_v, v) - 1
        v0, v1 = curve_v[lo], curve_v[lo + 1]
        if v1 <= v0:
            return curve_i[lo]
        frac = (v - v0) / (v1 - v0)
        return curve_i[lo] + (curve_i[lo + 1] - curve_i[lo]) * frac

    @staticmethod
    def _interp_voltage(i: float, curve_v: list, curve_i: list) -> float:
        """
        Linear V(I) interpolation over the step's curve table — the MIRROR of
        _interp_current(). Used by the I->V loop (curve_drive="current"): given a
        measured current it returns the model voltage on the curve.

        curve_v is ascending and curve_i is monotonically descending (as produced
        by iv_curve), so the (I, V) pairs are sorted by ascending current before
        interpolating. stdlib only — the comm layer does not depend on numpy.
        """
        if not curve_i or len(curve_v) != len(curve_i):
            return 0.0
        pairs = sorted(zip(curve_i, curve_v))   # ascending in current
        cs = [p[0] for p in pairs]
        vs = [p[1] for p in pairs]
        if i <= cs[0]:
            return vs[0]
        if i >= cs[-1]:
            return vs[-1]
        lo = bisect.bisect_right(cs, i) - 1
        i0, i1 = cs[lo], cs[lo + 1]
        if i1 <= i0:
            return vs[lo]
        frac = (i - i0) / (i1 - i0)
        return vs[lo] + (vs[lo + 1] - vs[lo]) * frac

    def output_off(self):
        self.send("OUTP OFF")

    def set_protections(self, ovp: float = None, ocp: float = None,
                        opp: float = None):
        if ovp is not None:
            self.send(f"VOLT:PROT {min(ovp, V_MAX * 1.1):.2f}")
        if ocp is not None:
            self.send(f"CURR:PROT {min(ocp, I_MAX * 1.1):.2f}")
        if opp is not None:
            self.send(f"POW:PROT {opp:.1f}")

    # ── Profile execution ───────────────────────────────────────────────────────

    def read_oper_cond(self) -> tuple[int, str]:
        """
        Read STAT:OPER:COND? and return (raw_int, label_hex).

        The EA-PS 10060-170 (firmware KE V3.06, PS series) returns 0x0240:
            bit 6 (0x0040): output active
            bit 9 (0x0200): remote control active (SYST:LOCK ON)
        Bits 0 (CC) and 1 (CV) are NOT set in this firmware.
        Use infer_mode() to determine CV/CC from measurements.
        """
        raw = self.query_fast("STAT:OPER:COND?").strip()
        try:
            val = int(raw)
        except (ValueError, TypeError):
            return 0, "---"
        if val == 0:
            label = "OFF"
        elif val & 0x0040:
            label = "ON"   # output+remote active; CV/CC not available in reg.
        else:
            label = f"0x{val:04X}"
        return val, label

    @staticmethod
    def infer_mode(v_meas: float, i_meas: float,
                   v_set: float, i_set: float) -> str:
        """
        Infer the regulation mode by comparing measurements against setpoints.

        - "CV"  if V_meas ≈ V_set (the source regulates voltage, I < I_set)
        - "CC"  if I_meas ≈ I_set (the source regulates current, V < V_set)
        - "CV+CC" in the transition zone (both within the threshold)
        - "???" if neither matches (possible transient or off)
        """
        if v_set <= 0 or i_set <= 0:
            return "---"
        near_v = abs(v_meas - v_set) / v_set < 0.08   # ±8 %
        near_i = abs(i_meas - i_set) / i_set < 0.08
        if near_v and near_i:
            return "CV+CC"
        if near_v:
            return "CV"
        if near_i:
            return "CC"
        return "???"

    def run_profile(self,
                    profile:     list[dict],
                    dt_ms:       int,
                    progress_cb: callable = None,
                    verbose:     bool = False,
                    rapid_ms:    int = 0,
                    curve_seed_hold_ms: int | None = None,
                    envelope:          str = "direct",
                    curve_drive:       str = "voltage",
                    cp_curr_ceiling:   "float | str | None" = None,
                    curve_damp:        float = 1.0,
                    curve_i_offset:    float = 0.0,
                    current_fn:        callable = None,
                    sample_cb:         callable = None):
        """
        Run the profile, compensating for the real time of each SCPI command.

        verbose: if True, prints CV/CC mode and V/I measurements at end of step.

        rapid_ms: I(V) loop period [ms] for envelope="curve" (default 200 ms).
            Ignored for "direct"/"cp".

        The inter-setpoint transition strategies for the "direct" envelope
        (instant / ramp / ramp+drift / slope) live in
        tools/bench/transition_strategies.py — a research comparison used only by
        the benches. The product paths use the cp/curve envelopes below, which
        manage their own dynamics.

        envelope: shape the source presents to the load at each step.
            "direct" — VOLT=Vmp, CURR=Imp (rectangle): one send per step + wait.
            "cp"     — VOLT=Voc, CURR=Isc, POW=Pmp. The native envelope
                       CC → hyperbola V·I=Pmp → CV guarantees the hour's power in
                       hardware. Requires "Voc" and "Isc" keys in each step
                       (added by pipeline.profile.build with attach_curve=True);
                       if missing it degrades to "direct".
            "curve"  — "cp" envelope + I(V) loop: measures V with MEAS:VOLT? and
                       sends CURR interpolated from the step's curve_v/curve_i
                       table (the real diode-model curve). The MPPT sees the true
                       knee. Uses rapid_ms (minimum 200) as the loop period. Steps
                       without a table behave as "cp".

        curve_drive: direction of the I(V) loop in envelope="curve".
            "voltage" (default) — V->I loop: the source is a CURRENT source,
                measures V (MEAS:VOLT?) and commands CURR = I_model(V). Natural
                match for a CV or CR load, which sets the operating voltage.
            "current" — I->V loop (MIRROR): the source is a VOLTAGE source,
                measures I (MEAS:CURR?) and commands VOLT = V_model(I). Natural
                match for a CC load, which sets the operating current. Use this to
                trace the curve against a constant-current electronic load (e.g.
                DL3021 in CC): a CC load fights the "voltage" loop (two current
                controllers) and latches; the mirror loop keeps source-voltage vs
                load-current orthogonal and stable. Ignored unless envelope=
                "curve".
        """
        if not profile:
            return

        # ── Envelope (direct / cp / curve) ────────────────────────────────────
        if envelope not in ("direct", "cp", "curve"):
            logger.warning("envelope '%s' unknown — using 'direct'", envelope)
            envelope = "direct"
        if curve_drive not in ("voltage", "current"):
            logger.warning("curve_drive '%s' unknown — using 'voltage'",
                           curve_drive)
            curve_drive = "voltage"
        if envelope in ("cp", "curve"):
            # The envelope needs Voc/Isc per step (added by build()).
            has_env = all(s.get("Voc") and s.get("Isc")
                          for s in profile if s["P_set"] > 0)
            if not has_env:
                logger.warning("envelope '%s' requires Voc/Isc keys in the "
                               "profile steps — degrading to 'direct'. "
                               "Build the profile with attach_curve=True.",
                               envelope)
                envelope = "direct"

        self._running = True
        dt_s     = max(int(dt_ms), DT_MIN) / 1000.0
        rapid_s  = max(rapid_ms, 0) / 1000.0
        total    = len(profile)

        if envelope == "curve" and rapid_s == 0:
            rapid_s = 0.2          # default I(V) loop period

        logger.debug("run_profile: %d steps, Dt=%.2f s, envelope=%s",
                     total, dt_s, envelope)

        if verbose:
            print(f"{'Step':>5} {'Hour':>6} {'V_set':>7} {'I_set':>7} "
                  f"{'P_set':>7} {'Mode':>6} {'V_meas':>7} {'I_meas':>7} {'Err_V':>7}")
            print("-" * 69)

        # Turn the output ON ONCE with the first point.
        out_on = profile[0]["P_set"] > 0
        self.set_output_fast(profile[0]["V_set"], profile[0]["I_set"], on=out_on)
        if not out_on:
            self.output_off()

        for i, step in enumerate(profile):
            if not self._running:
                break

            t_step_start = time.perf_counter()
            V_s = step["V_set"]
            I_s = step["I_set"]
            want_on = step["P_set"] > 0

            # Update setpoints WITHOUT re-asserting OUTP (on=False skips OUTP ON).
            if envelope in ("cp", "curve") and want_on:
                # PV envelope: VOLT=Voc + POW=Pmp + a CURR ceiling. The ceiling
                # choice (cp only) sets WHERE the CP corner sits and decides the
                # DUT's operating point:
                #   "imp"  → CURR=Imp: the corner falls at Vmp, so an MPPT DUT
                #            cannot slide below Vmp into the sub-power region — it
                #            is pinned at the MPP (Vmp, Imp, Pmp). Best for MPPT
                #            inverters, especially at high sun where CURR=Isc lets
                #            them drift to the low-voltage end of their MPPT window.
                #   <float>→ a fixed high ceiling (e.g. Isc peak): headroom for a
                #            constant-current load so it doesn't latch at the corner.
                #   None   → CURR=Isc(hour): the real short-circuit current.
                if envelope == "cp" and cp_curr_ceiling == "imp":
                    ceiling_i = step.get("I_set") or step["Isc"]
                elif (envelope == "cp" and cp_curr_ceiling
                        and isinstance(cp_curr_ceiling, (int, float))):
                    ceiling_i = cp_curr_ceiling
                else:
                    ceiling_i = step["Isc"]
                if envelope == "curve" and curve_drive == "current":
                    # The CURR ceiling limits on the source's OWN current sense,
                    # which reads +curve_i_offset high. A ceiling at Isc therefore
                    # trips at actual I≈Isc-offset — and at low sun Isc barely
                    # exceeds Imp, so it clamps BELOW the operating point, forcing
                    # CC and collapsing V (the 7h/16h failure). Lift it clear of
                    # the offset (the mirror voltage loop sets the point anyway).
                    ceiling_i = step["Isc"] + curve_i_offset + 0.2
                self.set_output_fast(step["Voc"], ceiling_i, on=False)
                if envelope == "curve" and curve_drive == "current":
                    # Mirror mode commands VOLT directly and the CURR=Isc ceiling
                    # already bounds the point; a POW=Pmp clamp would fight the
                    # loop AT the MPP (V·I≈Pmp) and, with the source's +offset
                    # current sense, trip low — biasing V down, worst at low sun
                    # (small Pmp). Give headroom (the curve's Voc·Isc box) so the
                    # curve alone sets the operating point.
                    self.set_power_fast(step["Voc"] * step["Isc"])
                else:
                    self.set_power_fast(step["P_set"])
            else:
                self.set_output_fast(V_s, I_s, on=False)

            logger.debug("step %d/%d %s V_set=%.3f I_set=%.3f order=%s",
                         i + 1, total,
                         datetime.now().isoformat(timespec="milliseconds"),
                         V_s, I_s, self._last_cmd_order)

            # Toggle the output only when the day/night state changes.
            if want_on != out_on:
                if want_on:
                    self.send("OUTP ON")
                else:
                    self.output_off()
                out_on = want_on

            # Notify the step at the START (setpoint already sent): the monitor
            # attributes samples to the correct emulated hour. It used to notify
            # at the end of the step, shifting the attribution by one step.
            if progress_cb:
                try:
                    progress_cb(i, total, step)
                except Exception:
                    logger.exception("progress_cb failed at step %d", i)

            # ── I(V) curve emulation loop (envelope="curve") ────────────────
            cv = step.get("curve_v")
            ci = step.get("curve_i")
            if envelope == "curve" and out_on and cv and ci and curve_drive == "current":
                # ── Mirror loop I->V (for constant-current loads, e.g. DL3021) ─
                # The source acts as a VOLTAGE source governed by the curve: it
                # measures I (MEAS:CURR?) and commands VOLT = V_model(I). A CC load
                # fixes the current, the source fixes the matching curve voltage —
                # source-voltage vs load-current are orthogonal, so there is no
                # current-controller fight and no latch (unlike the "voltage" loop,
                # which a CC load would deadlock).
                #
                # Downward-transition anti-latch (seed window): at a step where
                # the curve SHRINKS, the load still pulls the previous hour's
                # current at entry. If that current exceeds the new hour's Isc,
                # V_model(I) falls off the curve and returns ~0 (the short-circuit
                # end) → V slams to the floor and the loop oscillates low instead
                # of settling at the operating point. During a seed_hold window we
                # command Vmp (V_set) outright, letting the load's current fall
                # into the new curve's range; then the mirror loop follows.
                seed_hold_s = (max(curve_seed_hold_ms, 0) / 1000.0
                               if curve_seed_hold_ms is not None
                               else max(rapid_s * 2.0, 0.25))
                t_seed_end = t_step_start + seed_hold_s
                # Under-relaxation (damping): the mirror loop is a fixed-point
                # iteration V←V_model(I(V)); its gain ≈ |dV/dI|_curve / R exceeds 1
                # at high load resistance (low sun, steep curve near Voc), so it
                # limit-cycles below the operating point. Blending each command
                # toward the target (curve_damp<1) scales the gain down and it
                # converges. curve_damp=1.0 = undamped (previous behaviour).
                a = curve_damp if 0.0 < curve_damp <= 1.0 else 1.0
                v_last = V_s
                curve_cycles = 0
                t_end = t_step_start + dt_s - 0.010
                while time.perf_counter() < t_end:
                    if not self._running:
                        break
                    tc = time.perf_counter()
                    if tc < t_seed_end:
                        self.set_voltage_fast(V_s)          # seed at Vmp
                        v_last = V_s
                        curve_cycles += 1
                    else:
                        # Current feedback for the curve lookup. Prefer an
                        # external meter (current_fn -> (V,I,P)): the source's own
                        # MEAS:CURR? is quantized (~0.1 A on this 170 A EA-PS),
                        # which is fine at midday but wrecks low-sun hours where
                        # Isc<1 A — the coarse current lands on the steep knee and
                        # V collapses. The load (DL3021) resolves current in mA.
                        meas = None
                        if current_fn is not None:
                            try:
                                meas = current_fn()
                                i_now = meas[1]
                            except Exception:                # noqa: BLE001
                                i_now = None
                        else:
                            try:
                                i_now = float(self.query_fast(
                                    "MEAS:CURR?", settle=0.002).strip().split()[0]
                                    ) - curve_i_offset       # EA current-sense offset
                            except (ValueError, TypeError, IndexError):
                                i_now = None
                        if i_now is not None:
                            v_target = self._interp_voltage(i_now, cv, ci)
                            v_last = v_last + a * (v_target - v_last)
                            self.set_voltage_fast(v_last)
                            curve_cycles += 1
                            if sample_cb is not None and meas is not None:
                                try:
                                    sample_cb(i, meas[0], meas[1], meas[2])
                                except Exception:            # noqa: BLE001
                                    logger.exception(
                                        "sample_cb failed at step %d", i)
                    wait_cycle = rapid_s - (time.perf_counter() - tc)
                    if wait_cycle > 0:
                        time.sleep(wait_cycle)
                logger.debug("step %d/%d I->V loop: %d updates (period %.0f ms)",
                             i + 1, total, curve_cycles, rapid_s * 1000)

            elif envelope == "curve" and out_on and cv and ci:
                # ── Device-agnostic I(V) loop ────────────────────────────────
                # The source acts as a CURRENT SOURCE governed by the model curve:
                # it measures V (the operating point is set by the load — MPPT
                # inverter, DC/DC, electronic load or resistor) and adjusts the
                # current limit to I_model(V). Any device sees the correct I(V);
                # there is no tuning tied to a specific load.
                #
                # Start-up anti-latch: at Voc the curve gives I≈0. If 0 were
                # commanded right at entry (the load has not yet pulled V to the
                # knee), the load would have no current to lower V and the loop
                # would latch at open circuit — the transition_bench failure. So
                # during a `seed_hold` window the limit does NOT drop below Isc:
                # full short-circuit capacity is presented so ANY load (fast or
                # slow) enters the active region; past the window, V is already at
                # the knee and the curve is followed.
                #
                # The measurement uses settle≈2 ms (not the monitor's 50 ms): the
                # real latency is governed by readline and the loop runs as fast
                # as `rapid_s` allows. Sweeping rapid_s/seed_hold against the
                # DL3000 characterizes this source's SCPI bandwidth limit.
                seed_hold_s = (max(curve_seed_hold_ms, 0) / 1000.0
                               if curve_seed_hold_ms is not None
                               else max(rapid_s * 2.0, 0.25))
                t_seed_end = t_step_start + seed_hold_s
                Isc_step   = step["Isc"]
                curve_cycles = 0
                t_end = t_step_start + dt_s - 0.010
                while time.perf_counter() < t_end:
                    if not self._running:
                        break
                    tc = time.perf_counter()
                    try:
                        v_now = float(self.query_fast(
                            "MEAS:VOLT?", settle=0.002).strip().split()[0])
                    except (ValueError, TypeError, IndexError):
                        v_now = None
                    if v_now is not None:
                        i_cmd = self._interp_current(v_now, cv, ci)
                        if tc < t_seed_end:
                            i_cmd = max(i_cmd, Isc_step)   # seed floor
                        self.set_current_fast(i_cmd)
                        curve_cycles += 1
                    wait_cycle = rapid_s - (time.perf_counter() - tc)
                    if wait_cycle > 0:
                        time.sleep(wait_cycle)
                logger.debug("step %d/%d I(V) loop: %d updates "
                             "(period %.0f ms, seed %.0f ms)",
                             i + 1, total, curve_cycles, rapid_s * 1000,
                             seed_hold_s * 1000)

            else:
                elapsed = time.perf_counter() - t_step_start
                wait = dt_s - elapsed
                if wait > 0:
                    time.sleep(wait)

            if verbose:
                v_raw = self.query_fast("MEAS:VOLT?").strip().split()[0]
                i_raw = self.query_fast("MEAS:CURR?").strip().split()[0]
                try:
                    vm = float(v_raw)
                    im = float(i_raw)
                except ValueError:
                    vm = im = float("nan")
                mode_label = self.infer_mode(vm, im, V_s, I_s)
                hora = step.get("label", "")
                err_v = (vm - V_s) / V_s * 100 if V_s > 0 else 0.0
                print(f"{i+1:>5} {hora:>6} "
                      f"{V_s:>7.2f} {I_s:>7.3f} "
                      f"{step['P_set']:>7.1f} {mode_label:>6} "
                      f"{vm:>7.2f} {im:>7.3f} {err_v:>+6.1f}%")

        self.output_off()
        if envelope in ("cp", "curve"):
            # Restore the power ceiling: without this, the last Pmp of the
            # profile would keep limiting any later use of the source.
            self.send("POW MAX")
        self._running = False

    def stop(self):
        self._running = False
        self.output_off()