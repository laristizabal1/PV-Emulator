"""
tools/bench/response_time_bench.py
==================================
SCPI latency benchmark for the EA-PS 10060-170 source.

Measures the round-trip time (RTT) of each SCPI command type using
time.perf_counter() with sub-millisecond resolution. The source must be
connected and in remote mode; the DC output stays off during the whole test
(safe to connect without a load).

Usage:
    python tools/bench/response_time_bench.py              # auto-detected port
    python tools/bench/response_time_bench.py --port COM4  # explicit port
    python tools/bench/response_time_bench.py --reps 200   # more repetitions
    python tools/bench/response_time_bench.py --out results.csv

Outputs:
    - Summary table in the console (mean, std, p95, max per command)
    - CSV with all raw times for external analysis
    - HTML figure with latency distributions (if plotly is available)
"""

from __future__ import annotations

import argparse
import csv
import sys
import time
from pathlib import Path

# Allows running from the pv-emulator root or from tools/bench/
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from comm.scpi import SCPIController, autodetect_port
from config.hardware import DEFAULT_PORT, DEFAULT_BAUD

# ── Benchmark configuration ───────────────────────────────────────────────────

# Each benchmark is (readable_name, function that receives ctrl and runs the
# command). The measured time is exclusively the command RTT, without external
# sleeps.

def _bench_idn(ctrl: SCPIController) -> float:
    t0 = time.perf_counter()
    ctrl.query("*IDN?")
    return time.perf_counter() - t0


def _bench_meas_all(ctrl: SCPIController) -> float:
    t0 = time.perf_counter()
    ctrl.query("MEAS:ALL?")
    return time.perf_counter() - t0


def _bench_meas_volt(ctrl: SCPIController) -> float:
    t0 = time.perf_counter()
    ctrl.query("MEAS:VOLT?")
    return time.perf_counter() - t0


def _bench_meas_curr(ctrl: SCPIController) -> float:
    t0 = time.perf_counter()
    ctrl.query("MEAS:CURR?")
    return time.perf_counter() - t0


def _bench_set_volt(ctrl: SCPIController) -> float:
    """VOLT is write-only — measures only the send (no readline)."""
    with ctrl._lock:
        t0 = time.perf_counter()
        ctrl._ser.write(b"VOLT 0.000\n")
        ctrl._ser.flush()
        return time.perf_counter() - t0


def _bench_set_curr(ctrl: SCPIController) -> float:
    with ctrl._lock:
        t0 = time.perf_counter()
        ctrl._ser.write(b"CURR 0.000\n")
        ctrl._ser.flush()
        return time.perf_counter() - t0


def _bench_full_step(ctrl: SCPIController) -> float:
    """A full profile step: VOLT + CURR + MEAS:ALL? (no compensation sleep)."""
    with ctrl._lock:
        t0 = time.perf_counter()
        ctrl._ser.write(b"VOLT 0.000\n")
        ctrl._ser.write(b"CURR 0.000\n")
        ctrl._ser.write(b"MEAS:ALL?\n")
        ctrl._ser.readline()
        return time.perf_counter() - t0


BENCHES: list[tuple[str, callable]] = [
    ("*IDN?",          _bench_idn),
    ("MEAS:ALL?",      _bench_meas_all),
    ("MEAS:VOLT?",     _bench_meas_volt),
    ("MEAS:CURR?",     _bench_meas_curr),
    ("VOLT (write)",   _bench_set_volt),
    ("CURR (write)",   _bench_set_curr),
    ("full step",      _bench_full_step),
]

# ── Statistics ────────────────────────────────────────────────────────────────

def _stats(samples: list[float]) -> dict:
    import statistics
    s = sorted(samples)
    n = len(s)
    return {
        "n":    n,
        "mean": statistics.mean(s)       * 1000,   # → ms
        "std":  statistics.stdev(s)      * 1000 if n > 1 else 0.0,
        "p50":  s[int(n * 0.50)]         * 1000,
        "p95":  s[int(n * 0.95)]         * 1000,
        "max":  s[-1]                    * 1000,
        "min":  s[0]                     * 1000,
    }

# ── Runner ────────────────────────────────────────────────────────────────────

def run_bench(ctrl: SCPIController, reps: int) -> dict[str, list[float]]:
    results: dict[str, list[float]] = {}

    for name, fn in BENCHES:
        print(f"  Measuring '{name}' × {reps}... ", end="", flush=True)
        samples = []
        # 5 warmup (not recorded)
        for _ in range(5):
            try:
                fn(ctrl)
            except Exception:
                pass
            time.sleep(0.02)

        for _ in range(reps):
            try:
                t = fn(ctrl)
                samples.append(t)
            except Exception as e:
                print(f"\n    [WARN] sample error: {e}")
            time.sleep(0.02)   # minimal pause between samples so the buffer is not saturated

        results[name] = samples
        st = _stats(samples)
        print(f"mean={st['mean']:.1f} ms  p95={st['p95']:.1f} ms  max={st['max']:.1f} ms")

    return results

# ── Outputs ───────────────────────────────────────────────────────────────────

def print_table(results: dict[str, list[float]]):
    header = f"{'Command':<20} {'N':>5} {'Mean':>8} {'Std':>7} {'Min':>7} {'P50':>7} {'P95':>7} {'Max':>7}"
    print("\n" + "─" * len(header))
    print(header)
    print("─" * len(header))
    for name, samples in results.items():
        st = _stats(samples)
        print(
            f"{name:<20} {st['n']:>5} "
            f"{st['mean']:>7.2f}ms {st['std']:>6.2f}ms "
            f"{st['min']:>6.2f}ms {st['p50']:>6.2f}ms "
            f"{st['p95']:>6.2f}ms {st['max']:>6.2f}ms"
        )
    print("─" * len(header))


def save_csv(results: dict[str, list[float]], path: Path):
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["comando", "rep", "rtt_ms"])
        for name, samples in results.items():
            for i, t in enumerate(samples):
                w.writerow([name, i + 1, f"{t * 1000:.4f}"])
    print(f"\nCSV saved to: {path}")


def save_html(results: dict[str, list[float]], path: Path):
    try:
        import plotly.graph_objects as go
        from plotly.subplots import make_subplots
    except ImportError:
        print("plotly unavailable — skipping HTML figure")
        return

    n = len(results)
    fig = make_subplots(
        rows=n, cols=1,
        subplot_titles=list(results.keys()),
        shared_xaxes=False,
    )
    for row, (name, samples) in enumerate(results.items(), start=1):
        ms = [t * 1000 for t in samples]
        fig.add_trace(
            go.Histogram(x=ms, nbinsx=40, name=name, showlegend=False),
            row=row, col=1,
        )
        fig.update_xaxes(title_text="RTT [ms]", row=row, col=1)

    fig.update_layout(
        title="SCPI latency distribution — EA-PS 10060-170",
        height=280 * n,
        template="plotly_white",
    )
    fig.write_html(str(path))
    print(f"HTML figure saved to: {path}")

# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="SCPI latency benchmark")
    parser.add_argument("--port", default=None,  help="Serial port (e.g. COM4)")
    parser.add_argument("--baud", default=DEFAULT_BAUD, type=int)
    parser.add_argument("--reps", default=100,   type=int, help="Repetitions per command")
    parser.add_argument("--out",  default=None,  help="Base path of the output CSV")
    args = parser.parse_args()

    port = args.port or autodetect_port(DEFAULT_PORT)
    if port is None:
        print("ERROR: no serial port detected. Connect the source.")
        sys.exit(1)

    print(f"\n=== SCPI latency benchmark ===")
    print(f"Port: {port}  Baud: {args.baud}  Repetitions: {args.reps}\n")

    ctrl = SCPIController(port=port, baud=args.baud)
    try:
        idn = ctrl.connect()
        print(f"Source identified: {idn.strip()}\n")
        ctrl.output_off()   # safety: output off during the whole test

        results = run_bench(ctrl, reps=args.reps)

    finally:
        ctrl.disconnect()

    print_table(results)

    # Output paths
    ts   = time.strftime("%Y%m%d_%H%M%S")
    base = Path(args.out).stem if args.out else f"bench_{ts}"
    out_dir = Path(__file__).parent / "bench_results"
    out_dir.mkdir(exist_ok=True)

    save_csv(results, out_dir / f"{base}.csv")
    save_html(results, out_dir / f"{base}.html")


if __name__ == "__main__":
    main()
