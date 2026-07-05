"""
hmi/callbacks/profile_cb.py
============================
Callbacks of Tab 2 — Setpoint profiles.

Registered callbacks (2):
    - update_profile  -> compute Vset/Iset/Pset with the chosen model and module
    - export_csv      -> generate and download the SeqLog CSV
"""

import plotly.graph_objects as go
from dash import Input, Output, State, html, no_update

from config.hardware        import C, DT_MIN
from config.locations       import LOCATIONS
from config.modules_catalog import CUSTOM_MODULE_KEY, get_params, to_module_params
from models.simplified      import SimplifiedModel
from models.single_diode    import SingleDiodeModel
from models.two_diode       import TwoDiodeModel
from models.base            import ModuleParams
from models.panel_factory   import panel_from_datasheet
from pipeline.profile       import build as build_profile, apply_strategy, densify
from pipeline.seqlog        import to_csv_string
from hmi.layout.components  import stat_box
from hmi.layout.figtheme    import style_fig
from hmi.i18n               import t

# Selectable curve sources — manual electrical models. Unknown keys (incl. the
# legacy "pvlib" from old saved profiles) fall back to SingleDiodeModel via
# _MODEL_MAP.get(key, ...).
_MODEL_MAP = {
    "simplified":   SimplifiedModel,
    "single_diode": SingleDiodeModel,
    "two_diode":    TwoDiodeModel,
}
_DEFAULT_MODEL = "single_diode"


def _make_empty_fig(height=180):
    fig = go.Figure()
    fig.update_layout(height=height, paper_bgcolor="white",
                      plot_bgcolor="white")
    return fig


def register(app):

    @app.callback(
        Output("store-profile",      "data"),
        Output("store-profile-meta", "data"),   # metadata for the persistence JSON
        Output("profile-stats",    "children"),
        Output("chart-vi",         "figure"),
        Output("chart-pt",         "figure"),
        Output("perfiles-warning", "children"),
        Input("store-nasa",    "data"),
        Input("dd-module",     "value"),
        Input("sl-Ns",         "value"),
        Input("sl-Np",         "value"),
        Input("sl-Voc",        "value"),
        Input("sl-Isc",        "value"),
        Input("sl-Vmp",        "value"),
        Input("sl-Imp",        "value"),
        Input("sl-betaVoc",    "value"),
        Input("sl-alphaIsc",   "value"),
        Input("sl-Ns-cells",   "value"),
        Input("sl-noct",       "value"),
        Input("sl-tilt",       "value"),
        Input("dd-model",      "value"),
        Input("dd-strategy",   "value"),
        Input("inp-dt-perfiles", "value"),
        Input("inp-pph",       "value"),
        Input("lang",          "data"),
        State("store-loc-idx", "data"),
    )
    def update_profile(nasa_data, module_key, Ns, Np,
                       Voc, Isc, Vmp, Imp, beta, alpha, Ns_cells, noct,
                       tilt, model_key, strategy, dt, pph, lang, loc_idx):
        lang = lang or "en"

        if not nasa_data:
            warn = html.Div(
                "Descarga datos ambientales en Ubicacion & Datos primero.",
                style={"padding": "16px 20px", "background": C["redLight"],
                       "border": "1px solid #fecaca", "borderRadius": 10,
                       "color": C["red"], "marginBottom": 12,
                       "fontWeight": 600, "fontSize": 13},
            )
            return no_update, no_update, [], _make_empty_fig(190), _make_empty_fig(170), warn

        # ── Construir ModuleParams ───────────────────────────────────────────
        # Catalogo: usa panel_from_datasheet con auto-deteccion de convencion
        # Custom: use the sliders directly (already in absolute values)
        if module_key and module_key != CUSTOM_MODULE_KEY:
            params = to_module_params(module_key)
        else:
            # Custom mode: the sliders are always in absolute values
            params = panel_from_datasheet(
                Isc  = Isc   or 13.5,
                Voc  = Voc   or 47.0,
                Imp  = Imp   or 13.0,
                Vmp  = Vmp   or 39.0,
                KI   = alpha or 0.0005,
                KV   = beta  or -0.13,
                Ns   = int(Ns_cells or 144),
                noct = noct  or 45.0,
                coefficients_in_percent=False,  # sliders siempre absolutos
            )

        # ── Instanciar y ajustar modelo ──────────────────────────────────────
        ModelClass = _MODEL_MAP.get(model_key or _DEFAULT_MODEL,
                                    SingleDiodeModel)
        model = ModelClass(params)
        model.fit()

        # ── Pipeline ─────────────────────────────────────────────────────────
        # attach_curve=True adjunta Voc/Isc (y la curva I-V) por hora —
        # requerido por la envolvente "cp" que usa scpi_cb al ejecutar
        # (mismo flujo que experiments/run_experiment.py).
        full_profile = build_profile(
            nasa_data, model=model,
            Ns_arr=Ns or 1, Np_arr=Np or 1,
            tilt=tilt or 10.0,
            attach_curve=True,
        )
        profile = apply_strategy(full_profile, strategy or "day")

        # Suavizado opcional (opción A): densifica la escalera horaria interpolando
        # G/T y recalculando el MPP + curva I(V) por sub-paso. attach_curve=True
        # para preservar Voc/Isc/curve_v que necesita la envolvente cp/curve.
        pph = int(pph or 1)
        if pph > 1:
            profile = densify(profile, model, pph,
                              Ns_arr=Ns or 1, Np_arr=Np or 1,
                              attach_curve=True)

        dt_ms   = max(dt or 1000, DT_MIN)

        if not profile:
            return [], {}, [], _make_empty_fig(190), _make_empty_fig(170), html.Div()

        # ── Metricas ─────────────────────────────────────────────────────────
        peak_P  = max(d["P_set"] for d in profile)
        peak_V  = max(d["V_set"] for d in profile)
        peak_I  = max(d["I_set"] for d in profile)
        total_E = sum(d["P_set"] for d in profile) / 1000.0
        lab_s   = len(profile) * dt_ms / 1000.0

        stats = [
            stat_box(t("prof.peak_p", lang), f"{peak_P:.0f}",  "W",   C["accent"]),
            stat_box(t("prof.peak_v", lang), f"{peak_V:.1f}",  "V",   C["red"]),
            stat_box(t("prof.peak_i", lang), f"{peak_I:.1f}",  "A",   C["blue"]),
            stat_box(t("prof.energy", lang), f"{total_E:.2f}", "kWh", C["accentDark"]),
            stat_box(t("prof.lab_dur", lang), f"{lab_s:.1f}",  "s",   C["purple"]),
        ]

        labels  = [d["label"] for d in profile]
        n_ticks = max(1, len(profile) // 24)

        fig_vi = go.Figure()
        fig_vi.add_trace(go.Scatter(
            x=labels, y=[d["V_set"] for d in profile],
            name="V (V)", line=dict(color=C["red"], width=2, shape="hv")))
        fig_vi.add_trace(go.Scatter(
            x=labels, y=[d["I_set"] for d in profile],
            name="I (A)", line=dict(color=C["blue"], width=2, shape="hv"),
            yaxis="y2"))
        style_fig(fig_vi, height=200)
        fig_vi.update_xaxes(dtick=n_ticks)
        # Auto-scale anchored at 0 (was fixed to [0,65] = hardware limit,
        # que aplastaba la curva V cuando el pico real es ~20 V).
        fig_vi.update_layout(
            yaxis=dict(rangemode="tozero"),
            yaxis2=dict(overlaying="y", side="right", rangemode="tozero",
                        showgrid=False,
                        tickfont=dict(size=9, color=C["label"])))

        fig_pt = go.Figure()
        fig_pt.add_trace(go.Scatter(
            x=labels, y=[d["P_set"] for d in profile],
            name="P (W)", fill="tozeroy",
            line=dict(color=C["accent"], width=2),
            fillcolor="rgba(22,163,74,0.12)"))
        fig_pt.add_trace(go.Scatter(
            x=labels, y=[d["Tcell"] for d in profile],
            name="T_cell (C)", line=dict(color=C["cyan"], width=1.5),
            yaxis="y2"))
        style_fig(fig_pt, height=190)
        fig_pt.update_xaxes(dtick=n_ticks)
        fig_pt.update_layout(
            yaxis=dict(rangemode="tozero"),
            yaxis2=dict(overlaying="y", side="right", showgrid=False,
                        tickfont=dict(size=9, color=C["label"])))

        # ── Metadata for JSON persistence ────────────────────────────────────
        _STRATEGY_LABELS = {
            "full":    "Serie completa",
            "day":     "Ventana diurna 06:00–18:00",
            "average": "Statistical average day",
        }
        _MODEL_LABELS = {
            "simplified":   "Simplificado",
            "single_diode": "1 diodo",
            "two_diode":    "2 diodos",
        }
        loc = LOCATIONS[loc_idx or 0] if loc_idx is not None else LOCATIONS[0]

        # NASA date range — extracted from the header returned by the API
        nasa_start = nasa_fin = ""
        if isinstance(nasa_data, dict):
            hdr = nasa_data.get("header", {})
            s, e = hdr.get("start", ""), hdr.get("end", "")
            if len(s) == 8:
                nasa_start = f"{s[:4]}-{s[4:6]}-{s[6:]}"
            if len(e) == 8:
                nasa_fin = f"{e[:4]}-{e[4:6]}-{e[6:]}"

        # Module parameters — catalog or custom (sliders)
        if module_key and module_key != CUSTOM_MODULE_KEY:
            modulo_params = {"fuente": "catalogo", "clave": module_key}
        else:
            modulo_params = {
                "fuente":    "custom",
                "Voc_V":     Voc   or 0,
                "Isc_A":     Isc   or 0,
                "Vmp_V":     Vmp   or 0,
                "Imp_A":     Imp   or 0,
                "betaVoc_V_C": beta  or 0,
                "alphaIsc_A_C": alpha or 0,
                "Ns_celdas": int(Ns_cells or 36),
                "NOCT_C":    noct  or 45,
            }

        # Full theoretical setpoint profile — allows comparison with measurements
        perfil_consignas = [
            {
                "paso":   i,
                "label":  d.get("label", ""),
                "V_set":  d.get("V_set", 0),
                "I_set":  d.get("I_set", 0),
                "P_set":  d.get("P_set", 0),
                "Tcell":  d.get("Tcell", 0),
                "Gpoa":   d.get("Gpoa",  0),
            }
            for i, d in enumerate(profile)
        ]

        meta = {
            "ciudad":          loc.get("name", "Desconocida"),
            "lat":             loc.get("lat"),
            "lon":             loc.get("lon"),
            "nasa_rango_inicio": nasa_start,
            "nasa_rango_fin":    nasa_fin,
            "estrategia":      _STRATEGY_LABELS.get(strategy or "day", strategy),
            "modelo":          _MODEL_LABELS.get(model_key or _DEFAULT_MODEL, model_key),
            "modelo_key":      model_key or _DEFAULT_MODEL,
            "modulo":          modulo_params,
            "Ns_arreglo":      Ns or 1,
            "Np_arreglo":      Np or 1,
            "tilt_deg":        tilt or 10.0,
            "dt_ms":           max(dt or 1000, DT_MIN),
            "points_per_hour": pph,
            "n_pasos":         len(profile),
            "perfil_consignas": perfil_consignas,  # full theoretical curve
        }

        return profile, meta, stats, fig_vi, fig_pt, html.Div()

    @app.callback(
        Output("download-csv", "data"),
        Input("btn-export-csv", "n_clicks"),
        State("store-profile", "data"),
        State("inp-dt-perfiles", "value"),
        prevent_initial_call=True,
    )
    def export_csv(_n, profile, dt):
        if not profile:
            return no_update
        return {"content": to_csv_string(profile, dt or 1000),
                "filename": "perfil_seqlog.csv", "type": "text/csv"}