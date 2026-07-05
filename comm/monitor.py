"""
comm/monitor.py
================
Real-time measurement monitor for the EA-PS source.

WHY a background thread and NOT reading from the Dash callback
---------------------------------------------------------------
Calling query() from a Dash callback blocks the Flask worker:
  - normal query() has timeout=2.0 s → if the source does not respond,
    the callback is blocked for 2 s, Dash marks it as failed and no output
    updates (live-readout and live-measured empty).

Solution: a dedicated daemon thread using query_fast() (timeout=150 ms).
The callback only reads _latest from memory — it never touches the serial port.

Approximate synchronization
---------------------------
  POLL_MS = 300 ms  ==  Dash interval-exec
  → monitor updates _latest every ~300 ms
  → callback reads _latest every ~300 ms
  → max skew: one cycle (~300 ms), imperceptible in the HMI

FIX — serial collision with the Modbus Bridge
---------------------------------------------
When the Modbus Bridge is active, its poll_loop (every 20 ms) and this monitor
share the same serial.Serial object but with different locks
(EAPowerSupply._lock vs SCPIController._lock) → race condition → SCPI responses
arrive mixed → garbage values in the HMI.

Solution: if the bridge is active (_bridge is not None and running),
_read_once() reads directly from bridge.last_readings (in-memory cache) instead
of sending SCPI queries. Only the bridge talks to the source.

Usage:
    monitor.set_bridge(bridge_instance)   # when starting the bridge
    monitor.set_bridge(None)              # when stopping the bridge

Persistence
-----------
  start() → clears buffer, records t_inicio
  stop()  → saves data/sessions/sesion_YYYYMMDD_HHMMSS.json

  (JSON keys kept in Spanish for backward compatibility with saved sessions:)
  {
    "sesion": {"inicio": "...", "fin": "...", "n_muestras": N, "intervalo_ms": 300},
    "mediciones": [{"timestamp": …, "V_dc": …, "I_dc": …, "P_dc": …}, …]
  }
"""

import json
import time
import threading
import collections
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from comm.scpi import SCPIController
    from comm.bridge import ScpiModbusBridge

# Intervalo del thread de polling — debe coincidir con interval-exec de Dash
POLL_MS = 300

_SAVE_DIR = (
    Path(__file__).resolve().parent.parent / "data" / "sessions"
)


class EAMonitor:
    """
    Reads real V, I, P in a daemon thread using query_fast() (150 ms timeout).
    The Dash callback only reads _latest from memory — non-blocking.

    When the Modbus Bridge is active, it reads from bridge.last_readings instead
    of issuing SCPI queries, avoiding the collision on the shared serial port.
    """

    def __init__(self, controller: "SCPIController"):
        self._ctrl     = controller
        self._running  = False
        self._thread: threading.Thread | None = None
        self._t_inicio = ""
        self._meta: dict = {}   # active profile metadata

        # Circular buffer: ~30 min at 300 ms
        self._buf: collections.deque[dict] = collections.deque(maxlen=6000)
        self._latest: dict = {}
        self._rlock = threading.Lock()   # protects _buf and _latest

        # Reference to the bridge — None when the bridge is not active.
        # When set and running, _read_once() reads from its cache instead of
        # sending SCPI queries (avoids collision on the serial).
        self._bridge: Optional["ScpiModbusBridge"] = None
        self._bridge_lock = threading.Lock()   # protects _bridge

        # Step explicitly injected (CLI / bench). When set, _get_step() uses
        # it instead of reading the HMI progress — in HMI-less contexts
        # (run_experiment) that progress is never updated and samples ended up
        # without P_set/V_set (empty metrics).
        self._ext_step: dict | None = None

    # ── Bridge control ───────────────────────────────────────────────────

    def set_bridge(self, bridge: Optional["ScpiModbusBridge"]) -> None:
        """
        Register (or clear) the reference to the active bridge.

        Call with the instance when starting the bridge:
            monitor.set_bridge(bridge_instance)

        Call with None when stopping the bridge:
            monitor.set_bridge(None)

        Thread-safe.
        """
        with self._bridge_lock:
            self._bridge = bridge
        if bridge is not None:
            print("[EAMonitor] Bridge mode active — reading from Modbus cache (no SCPI queries)")
        else:
            print("[EAMonitor] Bridge mode off — reading directly from SCPI")

    # ── Lifecycle ───────────────────────────────────────────────────────────

    def start(self, meta: dict | None = None):
        """Start the polling thread. Idempotent.

        meta: dict with profile metadata (location, model, strategy, etc.)
              Comes from store-profile-meta and is embedded in the final JSON.
        """
        if self._running:
            return
        self._running  = True
        self._t_inicio = datetime.now().isoformat(timespec="seconds")
        self._meta     = meta or {}
        with self._rlock:
            self._buf.clear()
            self._latest = {}
        self._thread = threading.Thread(
            target=self._loop, daemon=True, name="EAMonitor"
        )
        self._thread.start()
        print(f"[EAMonitor] Started — poll {POLL_MS} ms, query timeout 150 ms")

    def stop(self):
        """Stop the thread and save the JSON."""
        if not self._running:
            return
        self._running = False
        self._save_json()
        print("[EAMonitor] Stopped")

    @property
    def active(self) -> bool:
        return self._running

    # ── Data access (from the callback — non-blocking) ───────────────────────

    def get_latest(self) -> dict:
        """
        Latest available reading. Keys: timestamp, V_dc, I_dc, P_dc.
        Returns {} if there are no readings yet.
        """
        with self._rlock:
            return dict(self._latest)

    def get_buffer(self) -> list[dict]:
        """Full buffer history."""
        with self._rlock:
            return list(self._buf)

    def get_meta(self) -> dict:
        """Active session metadata (location, dut, envelope, …)."""
        return dict(self._meta)

    # ── Thread loop ───────────────────────────────────────────────────────

    def _loop(self):
        while self._running:
            t0 = time.perf_counter()

            if self._ctrl.connected:
                try:
                    reading = self._read_once()

                    # Attach the theoretical setpoint of the current step
                    step = self._get_step()
                    if step:
                        reading["V_set"]        = step.get("V_set", None)
                        reading["I_set"]        = step.get("I_set", None)
                        reading["P_set"]        = step.get("P_set", None)
                        reading["hora_emulada"] = step.get("label", "")
                        reading["Tcell"]        = step.get("Tcell", None)
                        reading["Gpoa"]         = step.get("Gpoa",  None)

                    with self._rlock:
                        self._latest = reading
                        self._buf.append(reading)
                except Exception as exc:
                    print(f"[EAMonitor] Read error: {exc}")

            elapsed = time.perf_counter() - t0
            wait = POLL_MS / 1000.0 - elapsed
            if wait > 0:
                time.sleep(wait)

    def set_step(self, step: dict | None):
        """
        Inject the running profile step (current setpoint) so samples carry
        V_set/I_set/P_set/hora_emulada. Use from run_profile's progress_cb in
        HMI-less contexts (CLI, bench). Thread-safe. Pass None to read the HMI
        progress again.
        """
        with self._rlock:
            self._ext_step = dict(step) if step else None

    def _get_step(self) -> dict:
        """
        Current profile step: first the one injected via set_step() (CLI),
        otherwise the HMI _exec_progress (deferred import to avoid a circular
        dependency). Returns {} if there is none.
        """
        with self._rlock:
            if self._ext_step is not None:
                return dict(self._ext_step)
        try:
            import hmi.callbacks.scpi_cb as _scpi
            return dict(_scpi._exec_progress.get("step") or {})
        except Exception:
            return {}

    def _read_once(self) -> dict:
        """
        Read V, I, P in the most appropriate way for the system state:

        CASE A — Bridge active:
            Reads from bridge.last_readings (in-memory dict, no serial access).
            This avoids the lock collision between EAMonitor and ScpiModbusBridge
            that caused garbage values when both read the same COM port.

        CASE B — No bridge:
            Uses query_fast() with a 150 ms timeout, as before.
            Tries MEAS:ALL? first (one query) and falls back to
            MEAS:VOLT? + MEAS:CURR? if unsupported.
        """
        # ── CASE A: bridge active → read from its cache ────────────────────
        with self._bridge_lock:
            bridge = self._bridge

        if bridge is not None and bridge.running:
            r = bridge.get_readings()
            return {
                "timestamp": round(time.time(), 3),
                "V_dc":      round(r.get("V", 0.0), 4),
                "I_dc":      round(r.get("I", 0.0), 4),
                "P_dc":      round(r.get("P", 0.0), 4),
            }

        # ── CASE B: no bridge → direct SCPI query ──────────────────────────
        # NOTE: the EA-PS 10060-170 does NOT support MEAS:ALL? (it hangs until
        # the timeout and returns empty, wasting ~150 ms per cycle). Validated
        # with tools/probe/meas_probe.py. The scalar measurements are read separately,
        # each responding in ~55 ms. Power is read directly with MEAS:POW? (the
        # instrument provides it) instead of computing V*I.
        v = _parse_float(self._ctrl.query_fast("MEAS:VOLT?")) or 0.0
        i = _parse_float(self._ctrl.query_fast("MEAS:CURR?")) or 0.0
        p = _parse_float(self._ctrl.query_fast("MEAS:POW?"))
        return {
            "timestamp": round(time.time(), 3),
            "V_dc":      round(v, 4),
            "I_dc":      round(i, 4),
            "P_dc":      round(p if p is not None else v * i, 4),
        }

    # ── Persistence ─────────────────────────────────────────────────────────

    def _save_json(self):
        with self._rlock:
            mediciones = list(self._buf)

        if not mediciones:
            print("[EAMonitor] Empty buffer — no JSON generated.")
            return

        payload = {
            "sesion": {
                "inicio":       self._t_inicio,
                "fin":          datetime.now().isoformat(timespec="seconds"),
                "n_muestras":   len(mediciones),
                "intervalo_ms": POLL_MS,
            },
            "perfil": self._meta,
            "mediciones": mediciones,
        }

        _SAVE_DIR.mkdir(parents=True, exist_ok=True)
        ts      = datetime.now().strftime("%Y%m%d_%H%M%S")
        archivo = _SAVE_DIR / f"sesion_{ts}.json"

        try:
            with open(archivo, "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2, ensure_ascii=False)
            # "->" ASCII: the U+2192 arrow breaks on Windows cp1252 consoles
            print(f"[EAMonitor] JSON -> {archivo.name} ({len(mediciones)} samples)")
        except OSError as exc:
            print(f"[EAMonitor] Error saving JSON: {exc}")


# ── Helper ────────────────────────────────────────────────────────────────────

def _parse_float(raw: str) -> float | None:
    """'10.00 V' o '2.5' → float. None si falla."""
    try:
        return float(raw.strip().split()[0])
    except (ValueError, IndexError, AttributeError):
        return None