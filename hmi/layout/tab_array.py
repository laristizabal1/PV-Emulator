"""
hmi/layout/tab_array.py
=======================
Layout of Tab 1 — PV Array.

    - Module catalog selector (dcc.Dropdown id="dd-module")
    - Sliders show/hide depending on whether the module is "custom" or preset
    - Slider values auto-update when a catalog module is selected
      (via callback in array_cb.py)

The outputs (val-*, arreglo-diagram, limites-check) are filled by array_cb.py.
"""

from dash import dcc, html
from config.hardware        import C
from config.modules_catalog import get_dropdown_options, DEFAULT_MODULE_KEY
from hmi.layout.components  import card, divider, model_diagram
from hmi.i18n               import t

# Selectable electrical models — manual models only. Keys must match _MODEL_MAP
# in profile_cb.py and MODELS in the tools. Labels are i18n keys (clean names,
# no citations).
_MODELS = ["single_diode", "two_diode", "simplified"]
_DEFAULT_MODEL = "single_diode"

# Module electrical-parameter sliders (only visible in custom mode). Labels are
# technical abbreviations shared across languages.
_SLIDERS_MODULO = [
    ("Voc STC",    "sl-Voc",      0,   60,  0.1,   49.8,  " V",    C["red"]),
    ("Isc STC",    "sl-Isc",       0,   20,  0.1,   13.5,  " A",    C["blue"]),
    ("Vmp STC",    "sl-Vmp",      0,   55,  0.1,   41.2,  " V",    C["red"]),
    ("Imp STC",    "sl-Imp",       0,   18,  0.1,   13.35, " A",    C["blue"]),
    ("beta Voc",   "sl-betaVoc", -0.3,   0,  0.001,  -0.13, " V/C",  C["dim"]),
    ("alpha Isc",  "sl-alphaIsc",  0, 1, 0.0001, 0.0005," A/C", C["dim"]),
    ("Ns (cells)", "sl-Ns-cells", 0, 200,   1,    144,   "",      C["dim"]),
    ("NOCT",       "sl-noct",     0,   50,   1,     45,   " C",    C["accent"]),
]

# Array-configuration sliders (always visible). (i18n_key, id, min, max, step,
# value, color)
_SLIDERS_ARREGLO = [
    ("arr.ns",   "sl-Ns",   1, 4, 1, 1,  C["red"]),
    ("arr.np",   "sl-Np",   1, 8, 1, 1,  C["blue"]),
    ("arr.tilt", "sl-tilt", 0, 45, 1, 10, C["dim"]),
]


def tab_array(lang: str = "en") -> html.Div:
    return html.Div([
        _panel_parametros(lang),
        _panel_diagrama(lang),
    ], style={"display": "flex", "gap": 14})


def _panel_parametros(lang: str) -> html.Div:
    catalogo = html.Div([
        html.Div(t("arr.module", lang), style={
            "fontSize": 10, "fontWeight": 800, "color": C["dim"],
            "textTransform": "uppercase", "letterSpacing": 1, "marginBottom": 6,
        }),
        dcc.Dropdown(
            id="dd-module",
            options=get_dropdown_options(),
            value=DEFAULT_MODULE_KEY,
            clearable=False,
            style={"fontSize": 11, "marginBottom": 4},
        ),
        html.Div(id="module-pmax-badge",
                 style={"fontSize": 10, "color": C["dim"], "marginBottom": 2}),
    ])

    sliders_modulo = html.Div(
        id="custom-module-sliders",
        style={"display": "none"},
        children=[
            divider(),
            html.Div("Module parameters (manual)", style={
                "fontSize": 10, "fontWeight": 800, "color": C["dim"],
                "textTransform": "uppercase", "letterSpacing": 1, "marginBottom": 6,
            }),
            *[_slider_row(label, sl_id, min_, max_, step_, val, color)
              for label, sl_id, min_, max_, step_, val, _unit, color
              in _SLIDERS_MODULO],
        ],
    )

    sliders_arreglo = html.Div([
        divider(),
        html.Div(t("arr.config", lang), style={
            "fontSize": 10, "fontWeight": 800, "color": C["dim"],
            "textTransform": "uppercase", "letterSpacing": 1, "marginBottom": 6,
        }),
        *[_slider_row(t(key, lang), sl_id, min_, max_, step_, val, color)
          for key, sl_id, min_, max_, step_, val, color in _SLIDERS_ARREGLO],
    ])

    modelo_selector = html.Div([
        divider(),
        html.Div(t("arr.model", lang), style={
            "fontSize": 10, "fontWeight": 700, "color": C["textMed"],
            "textTransform": "uppercase", "letterSpacing": 1.3,
            "marginBottom": 10,
        }),
        dcc.RadioItems(
            id="dd-model",
            options=[{"label": "  " + t(f"model.{k}", lang), "value": k}
                     for k in _MODELS],
            value=_DEFAULT_MODEL,
            labelStyle={"display": "block", "fontSize": 12,
                        "color": C["textMed"], "marginBottom": 5,
                        "cursor": "pointer"},
        ),
        # Equivalent-circuit diagram of the selected model (filled by array_cb).
        html.Div(id="model-diagram",
                 children=model_diagram(_DEFAULT_MODEL, 230),
                 style={"display": "flex", "justifyContent": "center",
                        "marginTop": 12}),
    ])

    return html.Div([
        card([catalogo, sliders_modulo, sliders_arreglo, modelo_selector],
             title=t("arr.module_array", lang)),
    ], style={"width": "48%"})


def _panel_diagrama(lang: str) -> html.Div:
    return html.Div([
        card([html.Div(id="module-params-table")],
             title=t("arr.params", lang)),
        card([html.Div(id="arreglo-diagram")],
             title=t("arr.diagram", lang), style={"marginTop": 12}),
        card([html.Div(id="limites-check")],
             title=t("arr.limits", lang),
             style={"marginTop": 12}),
    ], style={"width": "48%", "display": "flex",
              "flexDirection": "column", "gap": 12})


def _slider_row(label, sl_id, min_, max_, step_, value, color):
    return html.Div([
        html.Div([
            html.Span(label, style={"fontSize": 12, "color": C["textMed"]}),
            html.Span(id=f"val-{sl_id}",
                      style={"fontSize": 12, "fontWeight": 700,
                             "color": color, "fontFamily": "monospace"}),
        ], style={"display": "flex", "justifyContent": "space-between",
                  "marginBottom": 3}),
        dcc.Slider(id=sl_id, min=min_, max=max_, step=step_, value=value,
                   marks=None, tooltip={"always_visible": False}),
    ], style={"marginBottom": 12})
