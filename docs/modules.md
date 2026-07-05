# Code modules

[← Back to README](../README.md)

## `config/`

| File | Contents |
|---------|-----------|
| `hardware.py` | Electrical limits (V_MAX=60V, I_MAX=170A, P_MAX=5kW), serial port, Modbus host and port — all overridable via environment variables |
| `locations.py` | 9 preconfigured Colombian cities with lat/lon (Bogotá, Medellín, Barranquilla, Cali, Bucaramanga, Leticia, Riohacha, Villa de Leyva) plus a "Custom" entry |
| `modules_catalog.py` | Catalog of PV modules with datasheet parameters. Accepts temperature coefficients in relative (%/°C) or absolute (A/°C, V/°C) convention — auto-detected |
| `devices.py` | DUT catalog (envelope, MPPT, p_min, candidate modes) |

## `models/`

They inherit from `PVModel` (ABC) and implement `fit()`, `get_mpp(G_poa, T_cell, Ns_arr, Np_arr)` and `iv_curve(...)`.

| File | Model | Description |
|---------|--------|-------------|
| `base.py` | `PVModel`, `ModuleParams`, `MPPResult` | Base class and interfaces |
| `simplified.py` | `SimplifiedModel` | Explicit closed-form MPP from temperature coefficients (no diode network to solve) |
| `single_diode.py` | `SingleDiodeModel` | Single-diode model with Rs/Rsh — **default** curve source |
| `two_diode.py` | `TwoDiodeModel` | Two-diode model — higher accuracy at low irradiance |
| `thermal.py` | `cell_temperature` | Cell temperature via the NOCT and Faiman (pvlib) models (orthogonal to the electrical models above) |
| `panel_factory.py` | `panel_from_datasheet()` | Builds `ModuleParams` from the catalog, auto-detecting the coefficient convention and half-cell modules |

## `pipeline/`

| File | Main function |
|---------|------------------|
| `nasa_power.py` | `fetch(lat, lon, start, end)` — downloads hourly NASA POWER data (GHI, DNI, DHI, T2M, WS). Local cache in `data/nasa_cache/` anchored to the project root |
| `profile.py` | `build(nasa_data, model, Ns, Np, tilt)` — computes Gpoa (Hay-Davies POA transposition via pvlib with closure DNI; geometric fallback without pvlib), Tcell and V_set/I_set/P_set setpoints per hour. `attach_curve=True` attaches the hourly I-V curve for the cp/curve envelopes. `apply_strategy()` filters the profile: `"average"` (average day), `"day"` (6–18h window), `"full"` (no filter) |
| `seqlog.py` | `to_csv_string()`, `save()` — exports the profile to the EA Power Control SeqLog CSV format (separator `;`) |
| `post_exec_plots.py` | `build_post_exec_figs(buffer, meta)` — generates 3 post-execution Plotly figures: setpoint vs measurement P/V/I, MPPT efficiency η = P_dc/P_set × 100 % (labeled **tracking fidelity** if the DUT has no MPPT, per `meta["dut_has_mppt"]`), and absolute error \|ΔP\| with MAE and RMSE |

## `comm/`

| File | Class / Function | Description |
|---------|----------------|-------------|
| `scpi.py` | `SCPIController` | SCPI controller over USB/COM. Thread-safe (RLock). Includes `autodetect_port()` — falls back to the first available port if the configured one does not exist. Commands: `connect`, `run_profile`, `set_output_fast`, `output_off`, `disconnect` |
| `scpi.py` | `list_ports()` | Enumerates COM/USB ports for the HMI dropdown |
| `bridge.py` | `ScpiModbusBridge` | SCPI ↔ Modbus TCP bridge. Two daemon threads: `_poll_loop` (SCPI→Modbus at 20 ms) and `_run_modbus_server` (asyncio). `start_shared(ser)` reuses the SCPIController serial without opening it twice. The `_owns_serial` flag ensures `stop()` does not close a borrowed serial |
| `bridge.py` | `EAPowerSupply` | Low-latency SCPI driver (20 ms/cycle). Strategy: `MEAS:ALL?` single query, locally cached setpoints, `write_batch()` for fast initialization |
| `bridge.py` | `BridgeConfig` | Bridge config (port, baudrate, Modbus host, port, poll interval) — all with defaults from `config/hardware.py` |
| `monitor.py` | `EAMonitor` | In-process monitor (daemon thread, poll 500 ms). Accumulates a buffer of V/I/P measurements with timestamp. When the bridge is active, it reads from `bridge.last_readings` (without touching the serial). On stop, it persists JSON in `data/sessions/` |
| `dl3000.py` | `DL3000Load` | Minimal driver for the Rigol DL3000 (DL3021) electronic load over USB-TMC (pyvisa). Automates the curve-trace sweep (CC/CV/CR modes) used as the reference load for I-V curve fidelity validation |

**Modbus register map (Holding Registers):**

| HR | Type | Contents |
|----|------|-----------|
| 0–1 | Float32 BE | Measured voltage [V] |
| 2–3 | Float32 BE | Measured current [A] |
| 4–5 | Float32 BE | Measured power [W] |
| 6–7 | Float32 BE | Voltage setpoint [V] |
| 8–9 | Float32 BE | Current setpoint [A] |
| 10 | UInt16 | Output state (0=OFF, 1=ON) |
| 11 | UInt16 | Remote active (always 1) |
| 12 | UInt16 | SCPI error code |

Coil 0 (client write): `1` = Output ON, `0` = Output OFF.

## `hmi/`

The interface has 6 tabs. Each tab has its layout in `hmi/layout/` and its
callbacks in `hmi/callbacks/`. The callbacks are registered in `app.py` via
`cb.register_all(app)`. The UI is bilingual (EN/ES) via `hmi/i18n.py`, with a
language selector at the bottom-left of the sidebar.

| Tab | Layout | Callback | Function |
|-----|--------|----------|---------|
| 0 — Location | `tab_location.py` | — | City selection or manual coordinates |
| 1 — Array | `tab_array.py` | `array_cb.py` | PV module configuration (catalog or custom), number of modules Ns/Np, tilt |
| 2 — Profiles | `tab_profiles.py` | `profile_cb.py` | Download NASA POWER, compute the profile (diode-model curve), preview setpoints, export SeqLog CSV |
| 3 — SCPI Control | `tab_scpi.py` | `scpi_cb.py` | Connect the source, select the DUT, run the profile, manual V/I/Output control, Modbus TCP bridge |
| 4 — Summary | `tab_summary.py` | `summary_cb.py` | Session configuration (incl. DUT) and operating chart |
| 5 — Diagnostics | `tab_diagnostics.py` | `diagnostics_cb.py` | **Self-contained visualization** of the post-execution figures (setpoint vs measurement, η MPPT / fidelity, error \|ΔP\|) from the live or a saved session — DUT diagnostics without running scripts |

## `experiments/`

Repeated-experiment module for robust statistical analysis (see [experiments.md](experiments.md) for how to run it).

| File | Description |
|---------|-------------|
| `run_experiment.py` | CLI orchestrator. Runs N curve-fidelity repetitions with the default curve source and the original solar profile (Cali, statistical average day). Accepts `--dut` (derives the DUT envelope). Computes MAE, RMSE and η/tracking per run and aggregates statistics (mean ± standard deviation) over the N repetitions. Saves JSONs and a log in `experiments/results/` |
| `paper_figs.py` | Post-analysis for the paper. Recomputes metrics separating transient from steady state (discarding the first N s per hour, the DUT's `--pmin` minimum threshold) and generates IEEE-format figures (single column, panels (a)/(b)/(c), English) + markdown tables. Detects sessions in `results/<ts>/` |

## `tools/`

Grouped into `bench/` (performance benchmarks) and `probe/` (hardware diagnostics). See [tools.md](tools.md) for command examples.

| File | Description |
|---------|-------------|
| `bench/transition_bench.py` | **Operating-mode calibration per DUT.** With the DUT connected, sweeps its candidate modes (`--dut <key>`) measuring fidelity (P_err, V_err, dV_pico, t_estab) and recommends the best-fidelity mode. `--mock` mode without hardware. Results in `tools/bench/bench_results/` |
| `bench/response_time_bench.py` | SCPI latency benchmark. Measures the RTT of each command type (`*IDN?`, `MEAS:ALL?`, `MEAS:VOLT?`, `MEAS:CURR?`, `VOLT`/`CURR` write, full step) with 100 repetitions and full statistics (mean, std, P95, max). Generates raw CSV and an HTML figure with distributions |
| `bench/smoke_transition.py` | No-hardware smoke test of the anti-transient strategies (A/B/C) using a `FakeSerial` that captures the sent commands |
| `bench/transition_strategies.py` | Inter-setpoint transition strategies for the `direct` envelope (instant / ramp / ramp+drift / slope), extracted from `comm/scpi.py`. Research comparison exercised only by the bench tools |
| `probe/curve_trace_manual.py` | Manual I-V curve fidelity validation using an electronic load (DL3000) in CV as a reference instrument. Generates the data-collection sheet and analyzes the measurements (NRMSE, error in Isc/MPP/Voc/FF) |
| `probe/curve_trace_auto.py` | Automated version of `curve_trace_manual.py`: the DL3021 sweeps the operating point while the EA-PS holds the model curve (`curve` envelope), and the script computes the fidelity metrics without manual data entry |
| `probe/curve_hold.py` | Holds a single I-V curve point on the EA-PS (`curve` envelope) so the DL3021 can be swept by hand to validate curve fidelity manually |
| `probe/profile_replay_auto.py` | Full-day profile reproduction with the DL3021 as a deterministic (no-MPPT) load: the source replays the hourly staircase in the `curve` envelope |
| `probe/dl3021_check.py` | Preflight USB-TMC connectivity check for the Rigol DL3021, to run before the full curve-trace experiment |
| `probe/modbus_monitor.py` | Standalone Modbus TCP client for diagnostics. Shows measured V/I/P, setpoints and output state in real time on the console. Useful to verify the SCADA or PLC integration |
| `probe/meas_probe.py` | SCPI measurement probe: captures the raw format/latency of each measurement command to diagnose zero readings |
| `probe/quick_profile_test.py` | Quick console sanity run of a short PV profile (CV/CC alternation, setpoint tracking error) |
| `test_smoke.py` | No-hardware pytest suite (pipeline + mocked `run_profile`). Run with `pytest tools/test_smoke.py` |

---

## File structure

```
pv-emulator/
│
├── app.py                      # HMI entry point (Dash)
├── requirements.txt            # Python dependencies (general minimums)
├── requirements-lock.txt       # Exact versions used for the paper results
├── .python-version             # Python version (3.13)
│
├── config/
│   ├── hardware.py             # Electrical limits and network config (env vars)
│   ├── locations.py            # Preconfigured Colombian cities
│   ├── modules_catalog.py      # PV module catalog
│   └── devices.py              # DUT catalog (envelope, MPPT, p_min, modes)
│
├── models/
│   ├── base.py                 # PVModel (ABC), ModuleParams, MPPResult
│   ├── simplified.py           # SimplifiedModel — explicit closed-form MPP
│   ├── single_diode.py         # SingleDiodeModel — 1-diode Rs/Rsh (default)
│   ├── two_diode.py            # TwoDiodeModel — 2-diode (low-G accuracy)
│   ├── thermal.py              # Cell-temperature models (NOCT, Faiman via pvlib)
│   └── panel_factory.py        # Builder from datasheet
│
├── pipeline/
│   ├── nasa_power.py           # NASA POWER API client + cache
│   ├── profile.py              # V/I/P profile generation
│   ├── seqlog.py               # SeqLog CSV export
│   └── post_exec_plots.py      # PVI / MPPT / error post-execution figures
│
├── comm/
│   ├── scpi.py                 # SCPIController (USB/COM) + auto-detection
│   ├── bridge.py               # ScpiModbusBridge + EAPowerSupply (20 ms)
│   ├── monitor.py              # EAMonitor (polling + JSON persistence)
│   └── dl3000.py               # DL3000Load — Rigol DL3021 e-load (USB-TMC)
│
├── hmi/
│   ├── i18n.py                 # EN/ES translations + t()
│   ├── layout/
│   │   ├── tab_location.py     # Tab 0: city selection
│   │   ├── tab_array.py        # Tab 1: PV array configuration
│   │   ├── tab_profiles.py     # Tab 2: NASA download + profile computation
│   │   ├── tab_scpi.py         # Tab 3: SCPI control + DUT selection
│   │   ├── tab_summary.py      # Tab 4: session configuration
│   │   ├── tab_diagnostics.py  # Tab 5: diagnostic figures inside Dash
│   │   ├── figtheme.py         # Shared Plotly theme
│   │   └── components.py       # Reusable UI components
│   └── callbacks/
│       ├── array_cb.py         # Array and module callbacks
│       ├── nasa_cb.py          # NASA POWER download callbacks
│       ├── profile_cb.py       # Profile generation callbacks
│       ├── scpi_cb.py          # SCPI control + bridge callbacks
│       ├── summary_cb.py       # Summary and chart callbacks
│       └── diagnostics_cb.py   # Diagnostics callbacks (live/saved figures)
│
├── experiments/
│   ├── run_experiment.py       # CLI: N curve-fidelity repetitions (--dut)
│   └── paper_figs.py           # Post-analysis: steady-state metrics + IEEE figures
│
├── tools/
│   ├── bench/                  # Performance benchmarks
│   │   ├── transition_bench.py       # Operating-mode calibration per DUT
│   │   ├── transition_strategies.py  # Direct-envelope transition strategies
│   │   ├── response_time_bench.py    # SCPI latency benchmark
│   │   └── smoke_transition.py       # No-hardware anti-transient smoke test
│   ├── probe/                  # Hardware diagnostics / probes
│   │   ├── meas_probe.py           # SCPI measurement format/latency probe
│   │   ├── modbus_monitor.py       # Modbus TCP console monitor
│   │   ├── curve_trace_manual.py   # Manual I-V curve fidelity validation (DL3000)
│   │   ├── curve_trace_auto.py     # Automated I-V curve fidelity sweep (DL3021)
│   │   ├── curve_hold.py           # Hold one curve point for manual CV sweep
│   │   ├── profile_replay_auto.py  # Full-day replay with DL3021 as fixed load
│   │   ├── dl3021_check.py         # DL3021 USB-TMC preflight connectivity check
│   │   └── quick_profile_test.py   # Quick profile sanity run
│   └── test_smoke.py           # No-hardware pytest suite (pipeline + mocked run)
│
├── assets/
│   └── styles.css              # HMI polish (hover, dropdowns, scrollbar)
│
└── data/                       # Generated at runtime — not versioned
    ├── nasa_cache/             # NASA POWER response JSONs (input cache)
    └── sessions/               # EAMonitor output sessions
        └── sesion_*.json       # measurement sessions per run
```
