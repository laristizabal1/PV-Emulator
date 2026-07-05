"""
hmi/callbacks/nasa_cb.py
========================
Callbacks related to location selection and downloading environmental data
from NASA POWER.

Registered callbacks (4):
    - set_location        → store the selected city index
    - toggle_custom       → show / hide custom lat/lon
    - fetch_nasa_cb       → download and parse NASA POWER data
    - update_irradiance_charts → draw GHI/DNI/DHI and Tamb charts
"""

import pandas as pd
import plotly.graph_objects as go
from dash import Input, Output, State, html, dcc, ctx, no_update

# Internal imports — resolved correctly because app.py is at the project root
# and Python finds it on sys.path
from config.locations  import LOCATIONS
from config.hardware   import C
from pipeline.nasa_power import fetch as nasa_fetch, DEFAULT_CACHE_DIR
from hmi.layout.components import card, status_badge
from hmi.layout.figtheme   import style_fig
from hmi.i18n              import t


def register(app):
    """
    Register this module's 4 callbacks on the Dash `app` instance.
    Called from app.py: nasa_cb.register(app)
    """

    # ─── 1. Store location index ──────────────────────────────────────────────
    @app.callback(
        Output("store-loc-idx", "data"),
        [Input({"type": "btn-loc", "index": i}, "n_clicks") for i in range(9)],
        prevent_initial_call=True,
    )
    def set_location(*_args):
        """
        Each city button has id {"type":"btn-loc","index":i}.
        ctx.triggered_id returns the pressed button's dict, from which we
        extract the index.
        """
        triggered = ctx.triggered_id
        if triggered and "index" in triggered:
            return triggered["index"]
        return 0

    # ─── 2. Show / hide custom coordinates ────────────────────────────────────
    @app.callback(
        Output("custom-coords-container", "style"),
        Input("store-loc-idx", "data"),
    )
    def toggle_custom(idx):
        """
        Only shows the lat/lon inputs when the user selects
        "Custom" (index 8 in LOCATIONS).
        """
        return {"display": "block"} if idx == 8 else {"display": "none"}

    # ─── 3. Download NASA POWER data ──────────────────────────────────────────
    @app.callback(
        Output("store-nasa",  "data"),
        Output("nasa-status", "children"),
        Input("btn-fetch-nasa", "n_clicks"),
        State("store-loc-idx", "data"),
        State("inp-lat",   "value"),
        State("inp-lon",   "value"),
        State("inp-start", "value"),
        State("inp-end",   "value"),
        prevent_initial_call=True,
        running=[(Output("btn-fetch-nasa", "disabled"), True, False)],
    )
    def fetch_nasa_cb(_n, loc_idx, lat, lon, start, end):
        """
        Calls pipeline/nasa_power.fetch() with the selected coordinates and
        dates. Stores the result in store-nasa.

        running=[(btn, True, False)] disables the button while it loads and
        re-enables it when done — no extra state.
        """
        loc     = LOCATIONS[loc_idx or 0]
        use_lat = lat if loc_idx == 8 else loc["lat"]
        use_lon = lon if loc_idx == 8 else loc["lon"]

        try:
            data  = nasa_fetch(use_lat, use_lon, start, end,
                               cache_dir=DEFAULT_CACHE_DIR)
            label = (f"{len(data)} registros horarios  "
                     f"({start[:4]}-{start[4:6]}-{start[6:]} → "
                     f"{end[:4]}-{end[4:6]}-{end[6:]})")
            return data, status_badge(label, "ok")

        except Exception as exc:
            return no_update, status_badge(str(exc), "error")

    # ─── 4. Irradiance and temperature charts ─────────────────────────────────
    @app.callback(
        Output("irradiance-chart-container", "children"),
        Input("store-nasa", "data"),
        Input("lang", "data"),
        State("store-loc-idx", "data"),
    )
    def update_irradiance_charts(data, lang, loc_idx):
        """
        Draws two stacked charts:
          - Area: GHI, DNI, DHI in W/m²
          - Lines: T2M (°C) and wind speed (m/s) on the right Y axis
        Fires automatically whenever store-nasa changes.
        """
        lang = lang or "en"
        if not data:
            return html.Div(
                t("loc.no_data", lang),
                style={"textAlign": "center", "color": C["dim"],
                       "padding": "80px 0", "fontSize": 13},
            )

        df       = pd.DataFrame(data)
        loc_name = LOCATIONS[loc_idx or 0]["name"]
        n_ticks  = max(1, len(df) // 20)

        # ── Irradiance chart ────────────────────────────────────────────────
        fig_irr = go.Figure()
        fig_irr.add_trace(go.Scatter(
            x=df["label"], y=df["ghi"],
            fill="tozeroy", name="GHI (W/m²)",
            line=dict(color=C["accent"], width=2),
        ))
        fig_irr.add_trace(go.Scatter(
            x=df["label"], y=df["dni"],
            fill="tozeroy", name="DNI",
            line=dict(color=C["orange"], width=1.5),
            fillcolor="rgba(234,88,12,0.08)",
        ))
        fig_irr.add_trace(go.Scatter(
            x=df["label"], y=df["dhi"],
            fill="tozeroy", name="DHI",
            line=dict(color=C["blue"], width=1),
            fillcolor="rgba(37,99,235,0.06)",
        ))
        style_fig(fig_irr, height=210)
        fig_irr.update_xaxes(dtick=n_ticks)
        fig_irr.update_yaxes(rangemode="tozero")

        # ── Temperature and wind chart ──────────────────────────────────────
        fig_t = go.Figure()
        fig_t.add_trace(go.Scatter(
            x=df["label"], y=df["T2M"],
            name="T2M (°C)",
            line=dict(color=C["cyan"], width=1.5),
        ))
        fig_t.add_trace(go.Scatter(
            x=df["label"], y=df["WS"],
            name="Wind (m/s)",
            line=dict(color=C["purple"], width=1),
            yaxis="y2",
        ))
        style_fig(fig_t, height=160)
        fig_t.update_xaxes(dtick=n_ticks)
        fig_t.update_layout(
            yaxis2=dict(overlaying="y", side="right", showgrid=False,
                        tickfont=dict(size=9, color=C["label"])))

        return [
            card([dcc.Graph(figure=fig_irr, config={"displayModeBar": False})],
                 title=f'{t("loc.irradiance", lang)} — {loc_name}'),
            card([dcc.Graph(figure=fig_t, config={"displayModeBar": False})],
                 title=t("loc.temp_wind", lang),
                 style={"marginTop": 12}),
        ]
