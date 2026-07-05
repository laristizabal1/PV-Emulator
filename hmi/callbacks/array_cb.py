"""
hmi/callbacks/array_cb.py
=========================
Callbacks of Tab 1 — PV Array.

Registered callbacks (4):
    - slider labels         -> one callback per slider
    - load_module           -> load catalog params when a module is selected
    - toggle_custom_sliders -> show/hide sliders in custom mode
    - update_arreglo        -> diagram + params table + limits
"""

from dash import Input, Output, State, html, ctx, no_update
from config.hardware        import C, MONO, V_MAX, I_MAX, P_MAX
from config.modules_catalog import CATALOG, CUSTOM_MODULE_KEY, get_params
from hmi.layout.components   import pv_panel, status_badge, model_diagram
from hmi.i18n                import t

_SLIDERS_MODULO = [
    ("sl-Voc",       " V"),
    ("sl-Isc",       " A"),
    ("sl-Vmp",       " V"),
    ("sl-Imp",       " A"),
    ("sl-betaVoc",   " V/C"),
    ("sl-alphaIsc",  " A/C"),
    ("sl-Ns-cells",  ""),
    ("sl-noct",      " C"),
]
_SLIDERS_ARREGLO = [
    ("sl-Ns",   ""),
    ("sl-Np",   ""),
    ("sl-tilt", ""),
]
_ALL_SLIDERS = _SLIDERS_MODULO + _SLIDERS_ARREGLO


def register(app):

    # 1. Slider labels
    for sl_id, unit in _ALL_SLIDERS:
        @app.callback(
            Output(f"val-{sl_id}", "children"),
            Input(sl_id, "value"),
            prevent_initial_call=False,
        )
        def _label(v, _unit=unit):
            if v is None:
                return f"--{_unit}"
            if isinstance(v, float) and v != 0 and abs(v) < 0.01:
                return f"{v:.4f}{_unit}"
            return f"{v}{_unit}"

    # 2. Load module from the catalog
    @app.callback(
        Output("sl-Voc",       "value"),
        Output("sl-Isc",       "value"),
        Output("sl-Vmp",       "value"),
        Output("sl-Imp",       "value"),
        Output("sl-betaVoc",   "value"),
        Output("sl-alphaIsc",  "value"),
        Output("sl-Ns-cells",  "value"),
        Output("sl-noct",      "value"),
        Output("module-pmax-badge", "children"),
        Input("dd-module", "value"),
        Input("lang",      "data"),
        prevent_initial_call=False,
    )
    def load_module(module_key, lang):
        lang = lang or "en"
        if not module_key or module_key == CUSTOM_MODULE_KEY:
            return [no_update] * 8 + [
                html.Span(t("arr.enter_manual", lang),
                          style={"color": C["dim"]})
            ]
        d = get_params(module_key)
        # Compute Pmax using the new catalog keys (Vmp, Imp)
        Pmax = round(d["Vmp"] * d["Imp"], 1)
        badge = html.Span(
            f"Pmax STC = {Pmax} W  |  Voc {d['Voc']} V  |  Isc {d['Isc']} A",
            style={"color": C["accentDark"], "fontWeight": 600},
        )
        return (d["Voc"], d["Isc"], d["Vmp"], d["Imp"],
                d["KV"], d["KI"], d["Ns"], d["noct"], badge)

    # 2b. Equivalent-circuit diagram of the selected electrical model
    @app.callback(
        Output("model-diagram", "children"),
        Input("dd-model", "value"),
        prevent_initial_call=False,
    )
    def update_model_diagram(model_key):
        return model_diagram(model_key or "single_diode", 230)

    # 3. Show/hide custom sliders
    @app.callback(
        Output("custom-module-sliders", "style"),
        Input("dd-module", "value"),
    )
    def toggle_custom_sliders(module_key):
        if module_key == CUSTOM_MODULE_KEY:
            return {"display": "block"}
        return {"display": "none"}

    # 4. Diagrama + tabla + limites
    @app.callback(
        Output("arreglo-diagram",     "children"),
        Output("limites-check",       "children"),
        Output("module-params-table", "children"),
        Input("dd-module",    "value"),
        Input("sl-Ns",        "value"),
        Input("sl-Np",        "value"),
        Input("sl-Voc",       "value"),
        Input("sl-Isc",       "value"),
        Input("sl-Vmp",       "value"),
        Input("sl-Imp",       "value"),
        Input("sl-betaVoc",   "value"),
        Input("sl-alphaIsc",  "value"),
        Input("sl-Ns-cells",  "value"),
        Input("sl-noct",      "value"),
        Input("lang",         "data"),
    )
    def update_arreglo(module_key, Ns, Np, Voc, Isc, Vmp, Imp,
                       betaVoc, alphaIsc, Ns_cells, noct, lang):
        lang = lang or "en"
        _arr = t("arr.array_word", lang)
        Ns  = Ns  or 1
        Np  = Np  or 1
        Voc = Voc or 47.0
        Isc = Isc or 13.5
        Vmp = Vmp or 39.0
        Imp = Imp or 13.0

        # Diagrama
        # Mockup-style diagram: SVG module + array values beside it.
        def _diag_row(lbl, val, color=None):
            return html.Div([
                html.Span(lbl, style={"color": C["dim"]}),
                html.Span(val, style={"fontFamily": MONO, "fontWeight": 700,
                                      "color": color or C["text"]}),
            ], style={"display": "flex", "justifyContent": "space-between",
                      "gap": 14, "fontSize": 12.5, "lineHeight": 1.9})

        # Module tile that grows with the configuration: Ns columns (series)
        # x Np rows (parallel). The module keeps its real shape (pv_panel) and
        # is scaled so the set fits without overflowing.
        pw = max(24, min(104, 210 // max(Ns, 1) - 4))
        panel_grid = html.Div(
            [pv_panel(pw) for _ in range(Ns * Np)],
            style={"display": "grid",
                   "gridTemplateColumns": f"repeat({Ns}, max-content)",
                   "gap": 6, "justifyContent": "center",
                   "marginBottom": 12},
        )
        diagram = html.Div([
            panel_grid,
            html.Div(f"{Ns}S × {Np}P · {Ns*Np} {t('arr.modules_word', lang)}",
                     style={"fontSize": 11, "color": C["label"],
                            "textAlign": "center", "marginBottom": 8}),
            html.Div([
                _diag_row(f"Vmp {_arr}:", f"{(Vmp or 0)*Ns:.1f} V"),
                _diag_row(f"Imp {_arr}:", f"{(Imp or 0)*Np:.2f} A"),
                _diag_row(f"Pmp {_arr}:", f"{(Vmp or 0)*(Imp or 0)*Ns*Np:.0f} W",
                          C["accent"]),
            ], style={"maxWidth": 240, "margin": "0 auto"}),
        ], style={"padding": "6px 0"})

        # Parameters table
        if module_key and module_key != CUSTOM_MODULE_KEY:
            d = get_params(module_key)
            # Compute absolute KI/KV via panel_from_datasheet for display
            from models.panel_factory import panel_from_datasheet
            mp = panel_from_datasheet(
                Isc=d["Isc"], Voc=d["Voc"], Imp=d["Imp"], Vmp=d["Vmp"],
                KI=d["KI"], KV=d["KV"], Ns=d["Ns"], noct=d["noct"]
            )
            d_show = {"Voc": d["Voc"], "Isc": d["Isc"],
                      "Vmp": d["Vmp"], "Imp": d["Imp"],
                      "KV": round(mp.KV, 5), "KI": round(mp.KI, 6),
                      "Ns": d["Ns"], "noct": d["noct"]}
        else:
            d_show = {"Voc": Voc or 47.0, "Isc": Isc or 13.5,
                      "Vmp": Vmp or 39.0, "Imp": Imp or 13.0,
                      "KV": betaVoc or -0.13, "KI": alphaIsc or 0.0005,
                      "Ns": Ns_cells or 144, "noct": noct or 45}
            d = d_show

        Pmax_mod = round(d_show["Vmp"] * d_show["Imp"], 1)
        FF = round(Pmax_mod / max(d_show["Voc"] * d_show["Isc"], 0.001), 3)

        filas = [
            ("Voc STC",    f"{d_show['Voc']} V"),
            ("Isc STC",    f"{d_show['Isc']} A"),
            ("Vmp STC",    f"{d_show['Vmp']} V"),
            ("Imp STC",    f"{d_show['Imp']} A"),
            ("Pmax STC",   f"{Pmax_mod} W"),
            ("FF",         f"{FF}"),
            ("beta Voc",   f"{d_show['KV']} V/C"),
            ("alpha Isc",  f"{d_show['KI']} A/C"),
            ("Ns (cells)", f"{d_show['Ns']}"),
            ("NOCT",       f"{d_show['noct']} C"),
        ]
        params_table = html.Table([
            html.Tbody([
                html.Tr([
                    html.Td(k, style={"fontSize": 11, "color": C["dim"],
                                      "paddingRight": 12, "paddingBottom": 4}),
                    html.Td(v, style={"fontSize": 11, "fontWeight": 700,
                                      "fontFamily": "monospace",
                                      "color": C["textMed"], "paddingBottom": 4}),
                ]) for k, v in filas
            ])
        ], style={"width": "100%", "borderCollapse": "collapse"})

        # Limits — row with a status badge (mockup style).
        Pmp_arr = Vmp * Imp * Ns * Np
        checks = []
        for val, lim, unit, lbl in [
            (Voc*Ns, V_MAX, "V", f"V {_arr} (Voc)"),
            (Isc*Np, I_MAX, "A", f"I {_arr} (Isc)"),
            (Pmp_arr, P_MAX, "W", f"P {_arr} (Pmp)"),
        ]:
            ok = val <= lim
            checks.append(html.Div([
                html.Span(f"{lbl} ≤ {lim:g} {unit}",
                          style={"fontSize": 12.5, "color": C["textMed"]}),
                status_badge(f"{val:.1f} {unit}", "ok" if ok else "error"),
            ], style={"display": "flex", "alignItems": "center",
                      "justifyContent": "space-between", "padding": "9px 0",
                      "borderBottom": f'1px solid {C["borderLight"]}'}))

        limites = html.Div([
            *checks,
            html.Div("EA-PS 10060-170 · 5000 W · Autoranging · Δt min 200 ms",
                     style={"marginTop": 10, "fontSize": 10.5,
                            "color": C["label"]}),
        ])

        return diagram, limites, params_table