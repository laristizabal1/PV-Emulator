"""
tools/bench/smoke_transition.py
===============================
No-hardware smoke test of the anti-transient strategies:

    A — adaptive command order in set_output_fast()
    B — transition ramp transition_to_setpoint()
    C — graceful degradation of the drift corrector without EAMonitor

Replaces the serial port with a FakeSerial that captures the sent commands, so
the exact VOLT/CURR order can be verified without connecting the EA-PS source.

Usage (from the project root):
    python -m tools.bench.smoke_transition
"""

import sys
import time
import logging
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from comm.scpi import SCPIController          # noqa: E402
from tools.bench import transition_strategies  # noqa: E402


class FakeSerial:
    """Minimal serial.Serial replacement: captures writes, returns empty."""
    is_open = True
    timeout = 2.0

    def __init__(self):
        self.log: list[str] = []

    def write(self, data: bytes):
        self.log.append(data.decode("ascii").strip())

    def readline(self) -> bytes:
        return b""

    def close(self):
        self.is_open = False


def _cmds(ser: FakeSerial, prefix: str) -> list[str]:
    return [c for c in ser.log if c.startswith(prefix)]


def main():
    logging.basicConfig(level=logging.DEBUG,
                        format="%(asctime)s %(name)s %(message)s")

    ctrl = SCPIController(port="FAKE")
    ctrl._ser = FakeSerial()
    assert ctrl.connected

    # ── A. Adaptive order ────────────────────────────────────────────────────
    ctrl.set_output_fast(20.0, 5.0, on=False)   # first send (no history)
    assert ctrl._last_cmd_order == "VOLT->CURR", ctrl._last_cmd_order

    ctrl._ser.log.clear()
    ctrl.set_output_fast(21.0, 4.0, on=False)   # I drops → CURR first
    assert ctrl._last_cmd_order == "CURR->VOLT", ctrl._last_cmd_order
    assert ctrl._ser.log[0].startswith("CURR"), ctrl._ser.log

    ctrl._ser.log.clear()
    ctrl.set_output_fast(19.0, 6.0, on=False)   # I rises → VOLT first
    assert ctrl._last_cmd_order == "VOLT->CURR", ctrl._last_cmd_order
    assert ctrl._ser.log[0].startswith("VOLT"), ctrl._ser.log
    print("[OK] A — adaptive command order")

    # ── B. Transition ramp ───────────────────────────────────────────────────
    ctrl._ser.log.clear()
    n = transition_strategies.transition_to_setpoint(ctrl, 24.0, 3.0, steps=3, step_dt=0.2)
    assert n == 3, f"expected 3 ramp steps, got {n}"
    volts = _cmds(ctrl._ser, "VOLT")
    assert len(volts) == 3, volts
    # The last step must land exactly on the target
    assert volts[-1] == "VOLT 24.000", volts[-1]
    print(f"[OK] B — ramp: {n} steps, exact arrival ({volts})")

    # step_dt below DT_MIN must be clamped to 0.2 s (not crash)
    t0 = time.perf_counter()
    transition_strategies.transition_to_setpoint(ctrl, 20.0, 4.0, steps=2, step_dt=0.01)
    assert time.perf_counter() - t0 >= 0.19, "step_dt did not respect DT_MIN"
    print("[OK] B — step_dt clamped to DT_MIN (200 ms)")

    # ── C. run_profile ramp+drift WITHOUT monitor (graceful degradation) ────
    profile = [
        {"V_set": 22.0, "I_set": 4.5, "P_set": 99.0, "label": "10:00"},
        {"V_set": 21.5, "I_set": 4.0, "P_set": 86.0, "label": "11:00"},
    ]
    ctrl._ser.log.clear()
    t0 = time.perf_counter()
    transition_strategies.run_profile(ctrl, profile, 1000,
                     transition_mode="ramp+drift",
                     ramp_steps=2, ramp_step_dt=0.2,
                     monitor=None)               # no EAMonitor → no crash
    dt = time.perf_counter() - t0
    assert ctrl._ser.log[-1] == "OUTP OFF", ctrl._ser.log[-3:]
    print(f"[OK] C — run_profile ramp+drift without monitor "
          f"({dt:.1f} s, no exception)")

    print("\nSmoke test complete: 4/4 OK (no hardware)")


if __name__ == "__main__":
    main()
