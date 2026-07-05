# Diagnostic tools

[← Back to README](../README.md)

The full `tools/` inventory (bench + probe) is documented in
[modules.md](modules.md#tools). This page collects the common command lines.

## Modbus TCP console monitor

Verify the bridge from outside the app:

```bash
python tools/probe/modbus_monitor.py                       # localhost:PV_MODBUS_PORT
python tools/probe/modbus_monitor.py --host 192.168.1.10   # remote host
python tools/probe/modbus_monitor.py 192.168.1.10 502      # positional (compat)
```

Shows measured V/I/P, setpoints, output state and latency live. Useful to verify
the integration with SCADA, PLC or a DC microgrid.

## SCPI latency benchmark

```bash
python tools/bench/response_time_bench.py                # auto-detected port
python tools/bench/response_time_bench.py --port COM4    # explicit port
python tools/bench/response_time_bench.py --reps 200     # more repetitions
```

Measures the RTT of 7 command types with statistics (mean, std, P95, max). Saves
results in `tools/bench/bench_results/` as CSV and an interactive HTML figure.

## Operating-mode calibration per DUT

```bash
python tools/bench/transition_bench.py --dut mppt_inverter   # with hardware
python tools/bench/transition_bench.py --dut eload --mock    # no hardware
```

Sweeps the DUT's candidate modes measuring fidelity and recommends the
best-fidelity operating mode.

## No-hardware smoke tests

```bash
pytest tools/test_smoke.py           # pipeline + mocked run_profile
python tools/test_smoke.py           # same, standalone runner (exit 0 = OK)
```
