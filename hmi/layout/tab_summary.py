"""
hmi/layout/tab_summary.py
=========================
Layout of Tab 4 — Summary.

Holds the grid of 4 stat_boxes, the full configuration table and the final
operating chart. The outputs are filled by summary_cb.py.
"""

from dash import dcc, html
from config.hardware import C
from hmi.layout.components import card
from hmi.i18n import t


def tab_summary(lang: str = "en") -> html.Div:
    """Return the full layout of Tab 4."""
    return html.Div([

        # ── 4 metric tiles ────────────────────────────────────────────────────
        html.Div(
            id="resumen-stats",
            style={
                "display":             "grid",
                "gridTemplateColumns": "repeat(4, 1fr)",
                "gap":                 10,
                "marginBottom":        12,
            },
        ),

        # ── Full configuration table ──────────────────────────────────────────
        card(
            [html.Div(id="resumen-config")],
            title=t("sum.config", lang),
        ),

        # ── Operating chart ───────────────────────────────────────────────────
        card(
            [dcc.Graph(
                id="chart-resumen",
                config={"displayModeBar": False},
                style={"height": 200},
            )],
            title=t("sum.operation", lang),
            style={"marginTop": 12},
        ),

    ], style={"display": "flex", "flexDirection": "column", "gap": 0})
