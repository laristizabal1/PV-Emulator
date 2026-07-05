"""
hmi/layout/tab_location.py
==========================
Layout of Tab 0 — Location & Data.

Holds the city selection, the date range, the NASA POWER download button and the
chart containers (filled dynamically by nasa_cb.update_irradiance_charts).
"""

from dash import dcc, html
from config.locations import LOCATIONS
from config.hardware  import C
from hmi.layout.components import card
from hmi.i18n import t


def tab_location(lang: str = "en") -> html.Div:
    """Return the full layout of Tab 0."""
    # ── City buttons (2-column grid) ──────────────────────────────────────────
    loc_buttons = [
        html.Button(
            loc["name"],
            id={"type": "btn-loc", "index": i},
            n_clicks=0,
            style={
                "padding":      "6px 10px",
                "borderRadius": 8,
                "fontSize":     11,
                "border":       f"1px solid {C['border']}",
                "background":   C["white"],
                "color":        C["textMed"],
                "cursor":       "pointer",
                "width":        "100%",
                "textAlign":    "left",
            },
        )
        for i, loc in enumerate(LOCATIONS)
    ]

    panel_izquierdo = html.Div([

        card([
            html.Div(
                loc_buttons,
                style={
                    "display":             "grid",
                    "gridTemplateColumns": "1fr 1fr",
                    "gap":                 5,
                    "marginBottom":        12,
                },
            ),
            # Custom-coordinate inputs (hidden by default)
            html.Div(
                id="custom-coords-container",
                style={"display": "none"},
                children=[
                    html.Div(t("loc.lat", lang), style={"fontSize": 11, "color": C["dim"],
                                                        "marginBottom": 3}),
                    dcc.Input(
                        id="inp-lat", type="number", value=4.71, step=0.01,
                        style=_input_style(),
                    ),
                    html.Div(t("loc.lon", lang), style={"fontSize": 11, "color": C["dim"],
                                                        "marginTop": 8, "marginBottom": 3}),
                    dcc.Input(
                        id="inp-lon", type="number", value=-74.07, step=0.01,
                        style=_input_style(),
                    ),
                ],
            ),
        ], title=t("loc.card_location", lang)),

        card([
            html.Div(t("loc.start", lang),
                     style={"fontSize": 11, "color": C["dim"], "marginBottom": 3}),
            dcc.Input(
                id="inp-start", type="text", value="20240315",
                style={**_input_style(), "marginBottom": 10},
            ),
            html.Div(t("loc.end", lang),
                     style={"fontSize": 11, "color": C["dim"], "marginBottom": 3}),
            dcc.Input(
                id="inp-end", type="text", value="20240317",
                style={**_input_style(), "marginBottom": 12},
            ),
            html.Button(
                t("loc.download", lang),
                id="btn-fetch-nasa",
                n_clicks=0,
                style={
                    "width":        "100%",
                    "padding":      "10px",
                    "borderRadius": 10,
                    "border":       "none",
                    "background":   C["accent"],
                    "color":        "#fff",
                    "fontWeight":   800,
                    "fontSize":     12,
                    "cursor":       "pointer",
                },
            ),
            # Download status (OK / error) — updated by nasa_cb
            html.Div(id="nasa-status", style={"marginTop": 8}),
            html.Div([
                "Parameters: GHI · DNI · DHI · T2M · WS2M", html.Br(),
                "Source: NASA POWER (CERES + MERRA-2)",      html.Br(),
                "Hourly data available since 2001",
            ], style={"marginTop": 10, "fontSize": 9,
                      "color": C["dim"], "lineHeight": 1.6}),
        ], title=t("loc.card_dates", lang)),

    ], style={
        "width":         290,
        "flexShrink":    0,
        "display":       "flex",
        "flexDirection": "column",
        "gap":           12,
    })

    # ── Right panel: charts (filled by callback) ──────────────────────────────
    panel_derecho = html.Div(
        id="irradiance-chart-container",
        children=[
            html.Div(
                t("loc.no_data", lang),
                style={
                    "textAlign":  "center",
                    "color":      C["dim"],
                    "padding":    "80px 0",
                    "fontSize":   13,
                },
            )
        ],
        style={"flex": 1, "display": "flex", "flexDirection": "column", "gap": 12},
    )

    return html.Div([
        panel_izquierdo,
        panel_derecho,
    ], style={"display": "flex", "gap": 14})


def _input_style() -> dict:
    return {
        "width":        "100%",
        "padding":      "6px 10px",
        "borderRadius": 8,
        "border":       f"1px solid {C['border']}",
        "fontSize":     12,
        "fontFamily":   "monospace",
        "color":        C["text"],
        "background":   C["white"],
        "boxSizing":    "border-box",
    }
