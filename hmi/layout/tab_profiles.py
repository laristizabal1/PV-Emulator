"""
hmi/layout/tab_profiles.py
==========================
Layout of Tab 2 — Setpoint profiles.

Holds the control bar (strategy, ms/step, export CSV), the metric stat_boxes
and the two Plotly charts. The outputs are filled by profile_cb.update_profile
and profile_cb.export_csv.
"""

from dash import dcc, html
from config.hardware import C
from hmi.layout.components import card
from hmi.i18n import t


def tab_profiles(lang: str = "en") -> html.Div:
    """Return the full layout of Tab 2."""
    return html.Div([

        # Warning when there is no data (filled by profile_cb)
        html.Div(id="perfiles-warning"),

        # ── Control bar ───────────────────────────────────────────────────────
        html.Div([
            # Strategy selector
            html.Div([
                html.Span(f'{t("prof.strategy", lang)}:',
                          style={"fontSize": 11, "color": C["dim"]}),
                dcc.RadioItems(
                    id="dd-strategy",
                    options=[
                        {"label": f'  {t("prof.full", lang)}',    "value": "full"},
                        {"label": f'  {t("prof.day", lang)}',     "value": "day"},
                        {"label": f'  {t("prof.average", lang)}', "value": "average"},
                    ],
                    value="day",
                    inline=True,
                    labelStyle={
                        "marginLeft": 10,
                        "fontSize":   11,
                        "color":      C["textMed"],
                        "cursor":     "pointer",
                    },
                ),
            ], style={"display": "flex", "alignItems": "center", "gap": 8}),

            # ms/step + pts/hour (profile smoothing) + export button
            html.Div([
                html.Span(f'{t("prof.ms_step", lang)}:',
                          style={"fontSize": 11, "color": C["dim"]}),
                dcc.Input(
                    id="inp-dt-perfiles",
                    type="number", value=1000, min=200, step=100,
                    style={
                        "width":        80,
                        "padding":      "5px 8px",
                        "borderRadius": 8,
                        "border":       f"1px solid {C['border']}",
                        "fontSize":     12,
                        "fontFamily":   "monospace",
                    },
                ),
                # Profile smoothing: sub-points per hour (1 = hourly staircase).
                # Interpolates G/T and recomputes the MPP per sub-step so the DUT's
                # MPPT follows a slowly-moving point (less transient).
                html.Span(f'{t("prof.pts_hour", lang)}:',
                          style={"fontSize": 11, "color": C["dim"]}),
                dcc.Input(
                    id="inp-pph",
                    type="number", value=1, min=1, max=60, step=1,
                    style={
                        "width":        64,
                        "padding":      "5px 8px",
                        "borderRadius": 8,
                        "border":       f"1px solid {C['border']}",
                        "fontSize":     12,
                        "fontFamily":   "monospace",
                    },
                ),
                html.Button(
                    t("prof.export", lang),
                    id="btn-export-csv",
                    n_clicks=0,
                    style={
                        "padding":      "7px 16px",
                        "borderRadius": 8,
                        "border":       f"1px solid {C['accent']}",
                        "background":   C["accentLight"],
                        "color":        C["accentDark"],
                        "fontWeight":   700,
                        "fontSize":     11,
                        "cursor":       "pointer",
                    },
                ),
                dcc.Download(id="download-csv"),
            ], style={"display": "flex", "alignItems": "center", "gap": 10}),
        ], style={
            "display":        "flex",
            "justifyContent": "space-between",
            "alignItems":     "center",
            "marginBottom":   12,
        }),

        # ── Metric tiles (filled by profile_cb) ──────────────────────────────
        html.Div(
            id="profile-stats",
            style={
                "display":             "grid",
                "gridTemplateColumns": "repeat(5, 1fr)",
                "gap":                 10,
                "marginBottom":        12,
            },
        ),

        # ── V_set · I_set chart ──────────────────────────────────────────────
        card(
            [dcc.Graph(
                id="chart-vi",
                config={"displayModeBar": False},
                style={"height": 200},
            )],
            title=t("prof.setpoints", lang),
        ),

        # ── P_set · Tcell chart ──────────────────────────────────────────────
        card(
            [dcc.Graph(
                id="chart-pt",
                config={"displayModeBar": False},
                style={"height": 180},
            )],
            title=t("prof.power_temp", lang),
            style={"marginTop": 12},
        ),

    ], style={"display": "flex", "flexDirection": "column", "gap": 0})
