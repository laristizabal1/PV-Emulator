"""
hmi/callbacks/summary_cb.py
===========================
Callbacks of Tab 4 (Summary) and the HMI top header.

Registered callbacks (2):
    - update_resumen  → final stats, configuration table and operating chart
    - update_header   → top bar with location, NASA status and array config
"""

import plotly.graph_objects as go
from dash import Input, Output, State, html

from config.hardware  import C, DT_MIN
from config.locations import LOCATIONS
from config.devices   import get_device
from hmi.layout.components import stat_box, icon
from hmi.layout.figtheme   import style_fig
from hmi.i18n             import t


def register(app):
    """
    Register the 2 summary callbacks on the Dash `app` instance.
    Called from app.py: summary_cb.register(app)
    """

    # ─── 1. Summary tab ───────────────────────────────────────────────────────
    @app.callback(
        Output("resumen-stats",  "children"),
        Output("resumen-config", "children"),
        Output("chart-resumen",  "figure"),
        Input("store-profile",  "data"),
        Input("lang",           "data"),
        State("store-loc-idx",  "data"),
        State("sl-Ns",          "value"),
        State("sl-Np",          "value"),
        State("sl-Voc",         "value"),
        State("sl-Isc",         "value"),
        State("sl-noct",        "value"),
        State("sl-tilt",        "value"),
        State("dd-model",       "value"),
        State("dd-strategy",    "value"),
        State("inp-dt",         "value"),
        State("inp-start",      "value"),
        State("inp-end",        "value"),
        State("dd-dut",         "value"),
    )
    def update_resumen(profile, lang, loc_idx, Ns, Np, Voc, Isc,
                       noct, tilt, model, strategy, dt, start, end, dut):
        """
        Fires whenever the profile changes (any slider or a new download).

        Returns:
          - resumen-stats : 4 tiles (peak power, energy, modules, duration)
          - resumen-config: full configuration table in columns
          - chart-resumen : overlaid P_set and V_set with a dual Y axis
        """
        lang = lang or "en"
        if not profile:
            return (
                [],
                html.Div(t("sum.no_data", lang),
                         style={"color": C["dim"], "fontSize": 13, "padding": 20}),
                go.Figure(),
            )

        # ── Metrics ───────────────────────────────────────────────────────────
        Ns      = Ns  or 1
        Np      = Np  or 1
        dt_ms   = max(dt or 1000, DT_MIN)
        peak_P  = max(d["P_set"] for d in profile)
        total_E = sum(d["P_set"] for d in profile) / 1000.0
        lab_s   = len(profile) * dt_ms / 1000.0
        loc     = LOCATIONS[loc_idx or 0]
        _dut    = get_device(dut)   # DUT selected in Tab 3 (or default)
        _yes    = {"en": "yes", "es": "sí"}.get(lang, "yes")
        _no     = {"en": "no",  "es": "no"}.get(lang, "no")

        stats = [
            stat_box(t("prof.peak_p", lang), f"{peak_P:.0f}",  "W",   C["accent"]),
            stat_box(t("prof.energy", lang), f"{total_E:.2f}", "kWh", C["accentDark"]),
            stat_box(t("sum.modules", lang), f"{Ns * Np}",
                                      f"{Ns}s × {Np}p",        C["blue"]),
            stat_box(t("prof.lab_dur", lang), f"{lab_s:.0f}",  "s",   C["purple"]),
        ]

        # ── Configuration table ───────────────────────────────────────────────
        s_fmt = (f"{start[:4]}-{start[4:6]}-{start[6:]}"
                 if start and len(start) == 8 else start or "—")
        e_fmt = (f"{end[:4]}-{end[4:6]}-{end[6:]}"
                 if end and len(end) == 8 else end or "—")

        model_label = t(f"model.{model or 'single_diode'}", lang)

        config_sections = [
            (t("sum.location", lang), [
                loc["name"],
                f"{loc['lat']}°, {loc['lon']}°",
                f"{s_fmt} → {e_fmt}",
                "NASA POWER",
            ]),
            (t("sum.array_pv", lang), [
                f"{Ns}s × {Np}p  ({Ns * Np} {t('sum.modules', lang).lower()})",
                f"Voc: {Voc} V · Isc: {Isc} A",
                f"NOCT: {noct} °C · Tilt: {tilt}°",
                f"{t('arr.model', lang)}: {model_label}",
            ]),
            (t("sum.emulation", lang), [
                f"{t('sum.strategy', lang)}: {strategy or 'day'}",
                f"{dt_ms} ms/step",
                f"{len(profile)} {t('sum.steps', lang)}",
                f"Δt min: {DT_MIN} ms",
            ]),
            (t("sum.dut", lang), [
                _dut.label,
                f"MPPT: {_yes if _dut.has_mppt else _no}",
                f"{t('sum.envelope', lang)}: {_dut.envelope}",
                f"P min: {_dut.p_min_w:.0f} W",
            ]),
            (t("sum.source", lang), [
                "EA-PS 10060-170",
                "60 V / 170 A / 5 kW",
                "SCPI (ASCII) · USB/COM",
                "OVP / OCP / OPP / OT",
            ]),
        ]

        cfg_div = html.Div([
            html.Div([
                html.Div(
                    section_title,
                    style={
                        "fontWeight":      800,
                        "color":           C["accentDark"],
                        "fontSize":        10,
                        "textTransform":   "uppercase",
                        "letterSpacing":   1.2,
                        "marginBottom":    8,
                    },
                ),
                html.Div([
                    html.Div(
                        line,
                        style={"color": C["textMed"], "lineHeight": 1.8, "fontSize": 11},
                    )
                    for line in lines
                ]),
            ])
            for section_title, lines in config_sections
        ], style={
            "display":             "grid",
            "gridTemplateColumns": "repeat(5, 1fr)",
            "gap":                 16,
        })

        # ── Operating chart ───────────────────────────────────────────────────
        labels  = [d["label"] for d in profile]
        n_ticks = max(1, len(profile) // 20)

        fig = go.Figure()
        fig.add_trace(go.Scatter(
            x=labels,
            y=[d["P_set"] for d in profile],
            fill="tozeroy",
            name="P (W)",
            line=dict(color=C["accent"], width=2),
            fillcolor="rgba(22,163,74,0.10)",
        ))
        fig.add_trace(go.Scatter(
            x=labels,
            y=[d["V_set"] for d in profile],
            name="V (V)",
            line=dict(color=C["red"], width=1.5, shape="hv"),
            yaxis="y2",
        ))
        style_fig(fig, height=200)
        fig.update_xaxes(dtick=n_ticks)
        fig.update_layout(
            yaxis=dict(rangemode="tozero"),
            yaxis2=dict(overlaying="y", side="right", rangemode="tozero",
                        showgrid=False,
                        tickfont=dict(size=9, color=C["label"])))

        return stats, cfg_div, fig

    # ─── 2. Top header status ─────────────────────────────────────────────────
    @app.callback(
        Output("header-live", "children"),
        Input("store-nasa",    "data"),
        Input("store-loc-idx", "data"),
        Input("lang",          "data"),
        State("sl-Ns", "value"),
        State("sl-Np", "value"),
    )
    def update_header(nasa_data, loc_idx, lang, Ns, Np):
        """
        Updates the header status bar whenever:
          - the selected location changes
          - Se descargan nuevos datos de NASA POWER

        Shows: location (with icon) · Ns/Np configuration · NASA badge (N h)
        """
        loc = LOCATIONS[loc_idx or 0]
        Ns  = Ns or 1
        Np  = Np or 1

        loc_badge = html.Div([
            icon("location", color=C["onDarkDim"], size=13),
            html.Span(f"{loc['name']} · {Ns}s{Np}p",
                      style={"fontSize": 11, "color": C["onDarkDim"]}),
        ], style={"display": "flex", "alignItems": "center", "gap": 6})

        nasa_badge = html.Div()   # empty if there is no data
        if nasa_data:
            nasa_badge = html.Div([
                icon("check", color=C["greenDim"], size=12),
                html.Span(f"NASA · {len(nasa_data)} h",
                          style={"fontSize": 10.5, "fontWeight": 600,
                                 "color": C["greenDim"]}),
            ], style={
                "display": "flex", "alignItems": "center", "gap": 6,
                "padding": "4px 10px", "borderRadius": 7,
                "background": "rgba(22,163,74,0.15)",
                "border": "1px solid rgba(22,163,74,0.4)",
            })

        return [loc_badge, nasa_badge]
