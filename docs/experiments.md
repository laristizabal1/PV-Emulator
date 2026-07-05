# Repeated experiments

[← Back to README](../README.md)

To characterize the repeatability of the emulation in a statistically robust way
(N repetitions with the reference curve and the original experiments' solar
profile):

```bash
# See the plan without connecting the source:
python experiments/run_experiment.py --dry-run --reps 5

# Run the experiment (reference curve × repetitions):
python experiments/run_experiment.py --reps 5

# Fix the envelope from the connected DUT:
python experiments/run_experiment.py --dut mppt_inverter --reps 5

# Fewer repetitions for a quick test:
python experiments/run_experiment.py --reps 3

# Post-analysis of the latest run (IEEE figures + tables):
python -m experiments.paper_figs

# Specific session:
python -m experiments.paper_figs --ts 20260610_125202
```

**Fixed configuration of the reference experiment** (exact replica of the
original April 2026 experiments):

| Parameter | Value |
|-----------|-------|
| City | Cali (lat=3.45, lon=−76.53) |
| Strategy | Statistical average day (`average`) |
| Module | Custom: Voc=24.3V, Isc=5.2A, Vmp=20.4V, Imp=4.9A, Ns=36, NOCT=47°C |
| Array | 1×1, tilt=10° |
| Steps | 15 (5h → 19h), dt=7000 ms/step |
| NASA | 2024-03-15 → 2025-03-17 (1 year) |

**Outputs per run** (in `experiments/results/`):

- `{ts}_{model}.json` — full buffer + metrics per repetition + per-hour aggregated statistics
- `{ts}_summary.json` — summary table of the repetitions
- `{ts}.log` — full session log
- `{ts}_tabla.csv` — table for LaTeX/Excel
- `{ts}_potencia.html`, `{ts}_eficiencia.html`, `{ts}_error.html` — figures with ± std uncertainty bands

> **Reproducibility.** Install the pinned environment with
> `pip install -r requirements-lock.txt` (Python 3.13) to reproduce the exact
> numerical results reported in the paper.
