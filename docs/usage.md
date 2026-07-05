# Full usage flow

[← Back to README](../README.md)

```mermaid
flowchart LR
    C[Connect EA-PS<br/>over USB] --> T0[Tab 0<br/>Location]
    T0 --> T1[Tab 1<br/>Array]
    T1 --> T2[Tab 2<br/>Profiles]
    T2 --> T3[Tab 3<br/>SCPI Control]
    T3 --> T4[Tab 4<br/>Summary]
    T4 --> T5[Tab 5<br/>Diagnostics]
```

1. **Connect** the EA-PS 10060-170 source over USB.

2. **Launch the HMI:**
   ```bash
   python app.py
   ```

3. **Tab 0 — Location.** Select a city (e.g. Cali) or enter manual coordinates.

4. **Tab 1 — Array.** Select a module from the catalog or enter custom
   parameters (Voc, Isc, Vmp, Imp, betaVoc, alphaIsc, Ns, NOCT). Configure the
   number of modules (Ns_arr × Np_arr) and tilt.

5. **Tab 2 — Profiles.** Select a strategy (Average day / Daytime window /
   Full). Download NASA POWER data → Compute profile → Preview setpoints. Export
   the SeqLog CSV to run with EA Power Control if desired.

6. **Tab 3 — SCPI Control.** Select the COM port → Connect the source. Select the
   device under test (DUT) → derives the envelope and is recorded in the session.
   (Optional) Start the Modbus TCP bridge to expose measurements to SCADA. Run the
   profile → the source follows the V/I setpoints in real time.

7. **Tab 4 — Summary.** Session configuration (incl. DUT) and P/V operating chart.

8. **Tab 5 — Diagnostics.** View, inside Dash, the post-execution analysis
   figures (adapted to the DUT) of the live session or a saved session:
   - Setpoint vs DC Measurement (P, V, I per hour)
   - MPPT efficiency η = P_dc / P_set × 100 % (or tracking fidelity if the DUT
     has no MPPT)
   - Absolute error \|ΔP\| with MAE and RMSE

   Image generation for the paper remains in `experiments/paper_figs.py` (see
   [experiments.md](experiments.md)).
