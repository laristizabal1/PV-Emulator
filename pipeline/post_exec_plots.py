"""
pipeline/post_exec_plots.py
============================
Builds the Plotly post-execution analysis figures from the EAMonitor buffer.
Called automatically when run_profile() finishes.

Figures produced
----------------
fig_pvi   : 3 subplots (P, V, I) — setpoint vs DC measurement
fig_mppt  : MPPT efficiency η = P_dc / P_set × 100 %
fig_error : Absolute power error |ΔP| with shaded area

Usage from scpi_cb.py
---------------------
    from pipeline.post_exec_plots import build_post_exec_figs
    figs = build_post_exec_figs(buffer, meta)
    # figs = {"pvi": Figure, "mppt": Figure, "error": Figure}
    # or {} if the buffer is empty or has no setpoints
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

from hmi.i18n import t

# ── Colors consistent with the HMI (light theme: figures over a white card) ──
_CLR_SET  = "#94a3b8"   # setpoint — neutral gray
_CLR_MEAS = "#16a34a"   # measurement — accent green
_CLR_EFF  = "#16a34a"   # efficiency / tracking — green
_CLR_ERR  = "#ea580c"   # error — orange
_TXT      = "#3b4654"   # axis/title text
_TICK     = "#9aa4b1"   # ticks
_BG       = "rgba(0,0,0,0)"
_GRID     = "#eef1f5"


def build_post_exec_figs(
    buffer: list[dict],
    meta:   dict | None = None,
    lang:   str = "en",
) -> dict[str, go.Figure]:
    """
    Build the 3 analysis figures from the measurement buffer.

    Parameters
    ----------
    buffer : list of EAMonitor dicts (each with timestamp, V_dc, I_dc, P_dc and
             optionally V_set, I_set, P_set, hora_emulada)
    meta   : store-profile-meta dict (location, model, strategy, etc.)

    Returns
    -------
    dict with keys "pvi", "mppt", "error" → go.Figure objects
    Returns {} if the buffer is empty or has no valid setpoints.
    """
    if not buffer:
        return {}

    df = pd.DataFrame(buffer)

    # Keep only rows with a recorded setpoint
    has_set = "P_set" in df.columns and df["P_set"].notna().any()
    if not has_set:
        return {}

    df = df[df["P_set"].notna()].copy()
    if df.empty:
        return {}

    # ── Group by emulated hour ────────────────────────────────────────────────
    if "hora_emulada" in df.columns and df["hora_emulada"].notna().any():
        grp = (
            df.groupby("hora_emulada", sort=False)
            .median(numeric_only=True)
            .reset_index()
        )
        # Sort hours correctly (5h, 6h, … 19h)
        grp["_hora_num"] = (
            grp["hora_emulada"]
            .str.replace("h", "", regex=False)
            .astype(float)
        )
        grp = grp.sort_values("_hora_num").reset_index(drop=True)
        x_vals  = grp["hora_emulada"].tolist()
        x_title = t("fig.hour", lang)
    else:
        # Fallback: step index
        grp     = df.groupby(df.index).median(numeric_only=True).reset_index()
        x_vals  = list(range(len(grp)))
        x_title = t("fig.step", lang)

    p_set  = grp["P_set"].tolist()
    v_set  = grp["V_set"].tolist() if "V_set" in grp else [None] * len(grp)
    i_set  = grp["I_set"].tolist() if "I_set" in grp else [None] * len(grp)
    p_meas = grp["P_dc"].tolist()
    v_meas = grp["V_dc"].tolist()
    i_meas = grp["I_dc"].tolist()

    # ── Metadata for titles ───────────────────────────────────────────────────
    meta    = meta or {}
    ciudad  = meta.get("ciudad", "")
    modelo  = meta.get("modelo", "")
    estrat  = meta.get("estrategia", "")
    dut_lbl = meta.get("dut_label", "")
    # Historical sessions (without DUT) are assumed MPPT — keeps the prior label.
    has_mppt = bool(meta.get("dut_has_mppt", True))
    _parts    = [p for p in (ciudad, dut_lbl, estrat) if p]
    subtitulo = " · ".join(_parts)

    # Legend at the BOTTOM (was at y=1.08, overlapping the 2-line title).
    layout_common = dict(
        paper_bgcolor=_BG,
        plot_bgcolor=_BG,
        font=dict(family="DM Sans, sans-serif", size=11, color=_TXT),
        margin=dict(l=52, r=22, t=58, b=56),
        legend=dict(
            orientation="h", x=0, y=-0.2, yanchor="top",
            font=dict(size=10), bgcolor="rgba(0,0,0,0)",
        ),
    )

    def _title(line1: str, line2: str = "") -> dict:
        """2-line title: name (13px) + readable metric/subtitle (12px)."""
        txt = line1
        if line2:
            txt += f"<br><span style='font-size:12px;color:{_TICK}'>{line2}</span>"
        return dict(text=txt, font=dict(size=13, color=_TXT), x=0.5,
                    xanchor="center", y=0.97, yanchor="top")

    def _axis(_title=None):
        # Common axis style. The title is passed separately (title=/title_text=)
        # by the caller — not included here to avoid colliding with that kwarg.
        return dict(
            gridcolor=_GRID,
            zerolinecolor=_GRID,
            tickfont=dict(size=10),
        )

    # ════════════════════════════════════════════════════════════════════════
    # FIG 1 — P / V / I : setpoint vs measurement
    # ════════════════════════════════════════════════════════════════════════
    fig_pvi = make_subplots(
        rows=3, cols=1, shared_xaxes=True,
        vertical_spacing=0.06,
        subplot_titles=[t("fig.a_power", lang), t("fig.b_voltage", lang),
                        t("fig.c_current", lang)],
    )

    def _trace_set(y, name, row, dash="dash"):
        fig_pvi.add_trace(go.Scatter(
            x=x_vals, y=y, name=f"{name} — {t('fig.setpoint', lang)}",
            mode="lines", line=dict(color=_CLR_SET, dash=dash, width=1.5),
            showlegend=(row == 1),
        ), row=row, col=1)

    def _trace_meas(y, name, row, color=_CLR_MEAS):
        fig_pvi.add_trace(go.Scatter(
            x=x_vals, y=y, name=f"{name} — {t('fig.dc_meas', lang)}",
            mode="lines+markers",
            line=dict(color=color, width=2),
            marker=dict(size=5),
        ), row=row, col=1)

    _trace_set(p_set,  "P_set", row=1)
    _trace_meas(p_meas, "P_dc",  row=1, color="#6366f1")

    _trace_set(v_set,  "V_set", row=2)
    _trace_meas(v_meas, "V_dc",  row=2, color="#f43f5e")

    _trace_set(i_set,  "I_set", row=3)
    _trace_meas(i_meas, "I_dc",  row=3, color="#0ea5e9")

    fig_pvi.update_layout(
        **{**layout_common, "margin": dict(l=52, r=22, t=58, b=70)},
        title=_title(t("fig.pvi_title", lang), subtitulo),
        height=540,
    )
    fig_pvi.update_yaxes(title_text="P [W]", row=1, col=1, **_axis(""))
    fig_pvi.update_yaxes(title_text="V [V]", row=2, col=1, **_axis(""))
    fig_pvi.update_yaxes(title_text="I [A]", row=3, col=1, **_axis(""))
    fig_pvi.update_xaxes(title_text=x_title, row=3, col=1, **_axis(""))
    for r in [1, 2, 3]:
        fig_pvi.update_xaxes(showgrid=True, gridcolor=_GRID, row=r, col=1)

    # ════════════════════════════════════════════════════════════════════════
    # FIG 2 — MPPT efficiency (DUT with MPPT) / Power fidelity (no MPPT)
    # ════════════════════════════════════════════════════════════════════════
    # Same P_dc/P_set×100 ratio; the label depends on whether the DUT seeks MPP.
    if has_mppt:
        _eff_trace = "η MPPT"
        _eff_name  = f"{t('fig.mppt_eff', lang)}  ·  η = P_dc / P_set × 100 %"
        _eff_ybar  = "η̄"
    else:
        _eff_trace = t("fig.tracking", lang)
        _eff_name  = f"{t('fig.power_fidelity', lang)}  ·  P_dc / P_set × 100 %"
        _eff_ybar  = t("fig.mean", lang)
    with np.errstate(invalid="ignore", divide="ignore"):
        eta = [
            round(m / s * 100, 1) if s and s > 0.5 else None
            for m, s in zip(p_meas, p_set)
        ]

    # Annotate the maximum
    eta_valid  = [(i, v) for i, v in enumerate(eta) if v is not None]
    eta_max_i, eta_max_v = max(eta_valid, key=lambda t: t[1]) if eta_valid else (0, None)

    fig_mppt = go.Figure()
    fig_mppt.add_hline(
        y=100, line_dash="dot", line_color="#475569",
        annotation_text=t("fig.ref100", lang),
        annotation_font=dict(color="#94a3b8", size=10),
    )
    fig_mppt.add_trace(go.Scatter(
        x=x_vals, y=eta,
        mode="lines+markers+text",
        line=dict(color=_CLR_EFF, width=2),
        marker=dict(size=6),
        name=_eff_trace,
    ))
    if eta_max_v is not None:
        fig_mppt.add_annotation(
            x=x_vals[eta_max_i], y=eta_max_v,
            text=f"<b>{eta_max_v}%</b>",
            showarrow=True, arrowhead=2,
            font=dict(color=_CLR_EFF, size=11),
            arrowcolor=_CLR_EFF,
        )

    avg_eta = round(np.nanmean([v for v in eta if v is not None]), 1)
    fig_mppt.update_layout(
        **layout_common,
        title=_title(_eff_name, f"{_eff_ybar} = {avg_eta} %  ·  {subtitulo}"),
        xaxis=dict(title=x_title, **_axis(x_title)),
        yaxis=dict(title="[%]", range=[0, 120], **_axis("[%]")),
        height=350,
    )

    # ════════════════════════════════════════════════════════════════════════
    # FIG 3 — Absolute power error |ΔP|
    # ════════════════════════════════════════════════════════════════════════
    delta_p = [abs(s - m) if s is not None and m is not None else None
               for s, m in zip(p_set, p_meas)]

    mae = round(np.nanmean([v for v in delta_p if v is not None]), 2)
    rmse = round(
        np.sqrt(np.nanmean([(s - m) ** 2
                            for s, m in zip(p_set, p_meas)
                            if s is not None and m is not None])),
        2,
    )

    # Shaded area under the curve
    x_fill = x_vals + x_vals[::-1]
    y_fill = delta_p + [0] * len(delta_p)

    fig_err = go.Figure()
    fig_err.add_trace(go.Scatter(
        x=x_fill, y=y_fill,
        fill="toself",
        fillcolor="rgba(245,158,11,0.15)",
        line=dict(color="rgba(0,0,0,0)"),
        showlegend=False,
        hoverinfo="skip",
    ))
    fig_err.add_trace(go.Scatter(
        x=x_vals, y=delta_p,
        mode="lines+markers",
        line=dict(color=_CLR_ERR, width=2),
        marker=dict(size=5),
        name="|ΔP|",
    ))
    fig_err.update_layout(
        **layout_common,
        title=_title(f"{t('fig.power_err', lang)}  |ΔP| = |P_set − P_dc|",
                     f"MAE = {mae} W  ·  RMSE = {rmse} W  ·  {subtitulo}"),
        xaxis=dict(title=x_title, **_axis(x_title)),
        yaxis=dict(title="|ΔP| [W]", **_axis("|ΔP| [W]")),
        height=320,
    )

    return {"pvi": fig_pvi, "mppt": fig_mppt, "error": fig_err}
