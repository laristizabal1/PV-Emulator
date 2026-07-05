"""
hmi/layout/tab_diagnostics.py
=============================
Layout of Tab 5 — Power device diagnostics.

SELF-CONTAINED visualization in Dash of the post-execution analysis figures
(setpoint vs DC measurement, MPPT efficiency / tracking fidelity, power error).
The operator runs a profile in the "SCPI / Control" tab and diagnoses the DUT
here without leaving the application — no script required.

Static image generation for the paper still lives elsewhere
(experiments/paper_figs.py); this tab is for interactive diagnostics only.

The outputs are filled by the callbacks in diagnostics_cb.py.
"""

from dash import dcc, html
from config.hardware import C
from hmi.layout.components import card
from hmi.i18n import t

# The figures use a light theme (transparent background over a white card).


def tab_diagnostics(lang: str = "en") -> html.Div:
    """Return the full layout of Tab 5 — Diagnostics."""
    return html.Div([
        _panel_controls(lang),
        _panel_figs(),
    ])


def _panel_controls(lang: str) -> html.Div:
    return card([
        html.Div(
            t("diag.intro", lang),
            style={"fontSize": 11, "color": C["dim"], "marginBottom": 10},
        ),
        html.Div([
            html.Div([
                html.Div(t("diag.session", lang),
                         style={"fontSize": 11, "color": C["dim"],
                                "marginBottom": 4}),
                dcc.Dropdown(
                    id="dd-diag-session",
                    options=[],          # filled by refresh_session_list
                    value="__live__",
                    clearable=False,
                    style={"fontSize": 12},
                ),
            ], style={"flex": 3}),
            html.Div([
                html.Div(" ", style={"fontSize": 11, "marginBottom": 4}),
                html.Button(
                    t("diag.refresh", lang),
                    id="btn-diag-refresh",
                    n_clicks=0,
                    style={
                        "width": "100%", "padding": "8px",
                        "borderRadius": 10, "border": "none",
                        "background": C["accent"], "color": "#fff",
                        "fontWeight": 800, "fontSize": 12, "cursor": "pointer",
                    },
                ),
            ], style={"flex": 1}),
        ], style={"display": "flex", "gap": 10, "alignItems": "flex-end",
                  "marginBottom": 10}),

        html.Div(id="diag-meta",
                 style={"fontSize": 11, "color": C["textMed"]}),
    ], title=t("diag.title", lang))


def _fig_card(graph_id: str) -> html.Div:
    return html.Div(
        dcc.Graph(id=graph_id, config={"displayModeBar": False},
                  style={"height": "100%"}),
        style={
            "background":    C["white"],
            "borderRadius":  13,
            "padding":       14,
            "marginBottom":  14,
            "border":        f"1px solid {C['border']}",
        },
    )


def _panel_figs() -> html.Div:
    return html.Div([
        _fig_card("diag-fig-pvi"),
        _fig_card("diag-fig-eff"),
        _fig_card("diag-fig-error"),
    ], style={"marginTop": 14})
