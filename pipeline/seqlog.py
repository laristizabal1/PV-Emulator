"""
pipeline/seqlog.py
==================
Export of the setpoint profile to the SeqLog CSV format of EA Power Control
(EA Elektro-Automatik's vendor software).

SeqLog format (semicolon separator):
    Time [ms] ; Voltage [V] ; Current [A] ; Power [W] ; DC Output

Reference: EA Power Control manual (Doc ID: PCES), section 8.6.1

Usage:
    from pipeline.seqlog import to_csv_string, save

    # For the Dash download callback:
    csv_str = to_csv_string(profile, dt_ms=1000)

    # To save to disk from the CLI:
    path = save(profile, dt_ms=200, path="outputs/profile.csv")
"""

from pathlib import Path
import pandas as pd

from config.hardware import DT_MIN


def to_dataframe(profile: list[dict], dt_ms: int) -> pd.DataFrame:
    """
    Convert the profile into a DataFrame with the exact format that EA Power
    Control's SeqLog expects.

    Applies the minimum limit of DT_MIN = 200 ms per step.

    Columns:
        Time [ms]   — step duration in milliseconds
        Voltage [V] — voltage setpoint
        Current [A] — current setpoint
        Power [W]   — computed power (visual reference in SeqLog)
        DC Output   — 1 = output active, 0 = output off (night)
    """
    dt_safe = max(int(dt_ms), DT_MIN)

    rows = [
        {
            "Time [ms]":   dt_safe,
            "Voltage [V]": step["V_set"],
            "Current [A]": step["I_set"],
            "Power [W]":   step["P_set"],
            "DC Output":   1 if step["P_set"] > 0 else 0,
        }
        for step in profile
    ]
    return pd.DataFrame(rows)


def to_csv_string(profile: list[dict], dt_ms: int) -> str:
    """
    Return the CSV as a string for the Dash dcc.Download component.

    The separator is a semicolon (;) because SeqLog requires it that way per
    section 8.6.1 of the EA Power Control manual.
    """
    return to_dataframe(profile, dt_ms).to_csv(index=False, sep=";")


def save(profile: list[dict], dt_ms: int,
         path: str | Path) -> Path:
    """
    Save the CSV to disk.
    Creates intermediate directories if they do not exist.
    Returns the Path of the saved file.

    Usage from the CLI orchestrator (run.py):
        saved = seqlog.save(profile, dt_ms=200, path="outputs/bogota_day.csv")
        print(f"Saved to {saved}")
    """
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    to_dataframe(profile, dt_ms).to_csv(out, index=False, sep=";")
    return out


def preview(profile: list[dict], dt_ms: int, n: int = 5) -> str:
    """
    Return the first `n` rows as a string for debugging.
    Useful in notebooks and the REPL to verify the format.
    """
    df = to_dataframe(profile, dt_ms)
    return df.head(n).to_string(index=False)
