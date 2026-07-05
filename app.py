"""
app.py — Photovoltaic panel emulator
====================================
HMI entry point. The EA-PS source emulates a photovoltaic source (I-V curve via
a single/two-diode model) and the device under test (DUT) —a programmable
electronic load or MPPT inverter— is connected at its output. It only assembles:
  - the Dash instance
  - the layout (header + sidebar + panels + stores)
  - callback registration (delegated to hmi/callbacks/)
  - the launch mode (browser or desktop via pywebview)

The UI is bilingual (EN/ES) via hmi/i18n.py; the active language lives in
dcc.Store(id="lang") and a selector sits at the bottom-left of the sidebar.
Code comments and docstrings are kept in English.

All logic lives in the modules:
    config/        — constants, palette and catalogs (PV modules, DUT)
    models/        — PV curve source (pvlib De Soto)
    pipeline/      — NASA POWER, profile, SeqLog
    comm/          — SCPI, Ethernet
    hmi/layout/    — layout of each tab
    hmi/callbacks/ — Dash callbacks

Run:
    python app.py            # browser mode  → http://localhost:8050
    python app.py --desktop  # desktop mode (requires: pip install pywebview)
"""

import sys
import time
import threading

import dash
from dash import dcc, html, Input, Output, State, ctx
import dash_bootstrap_components as dbc
from config.hardware  import C, FONT
from hmi.layout       import TAB_RENDERERS
from hmi.layout.components import icon, page_header
from hmi.i18n         import t, DEFAULT_LANG
import hmi.callbacks  as cb


# Fonts of the approved visual system: DM Sans (UI) + JetBrains Mono (numbers).
_GOOGLE_FONTS = ("https://fonts.googleapis.com/css2?family=DM+Sans:opsz,wght@"
                 "9..40,400;9..40,500;9..40,600;9..40,700&family=JetBrains+"
                 "Mono:wght@400;500;700&display=swap")

# ── Dash instance ─────────────────────────────────────────────────────────────
app = dash.Dash(
    __name__,
    external_stylesheets=[dbc.themes.BOOTSTRAP, _GOOGLE_FONTS],
    suppress_callback_exceptions=True,
    title="Photovoltaic panel emulator",
)
server = app.server   # exposed for future gunicorn deployment

# The visual polish (pvpulse keyframes, scrollbar, hover, dropdown restyle)
# lives in assets/styles.css — Dash loads it automatically.

# Navigation metadata: (tab_id, icon). Labels/subtitles come from hmi.i18n.
NAV = [
    ("tab-0", "location"),
    ("tab-1", "grid"),
    ("tab-2", "activity"),
    ("tab-3", "sliders"),
    ("tab-4", "file"),
    ("tab-5", "pulse"),
]


# ── Register all callbacks ────────────────────────────────────────────────────
cb.register_all(app)


# ── Layout helpers (rebuilt on language change) ───────────────────────────────

def _nav_style(active: bool) -> dict:
    base = {
        "display": "flex", "alignItems": "center", "gap": 11,
        "padding": "10px 12px", "margin": "0 12px 3px",
        "borderRadius": 9, "cursor": "pointer", "fontSize": 13,
        "borderLeft": "3px solid transparent",
    }
    if active:
        base.update({"background": "rgba(22,163,74,0.16)", "color": "#ffffff",
                     "borderLeft": f"3px solid {C['pulse']}", "fontWeight": 700})
    else:
        base.update({"background": "transparent", "color": C["sidebarText"],
                     "fontWeight": 600})
    return base


def _nav_item(tab_id: str, icon_name: str, active: bool, lang: str) -> html.Div:
    return html.Div(
        [icon(icon_name, color="#cdd5df", size=17),
         html.Span(t(f"nav.{tab_id}", lang))],
        id=f"nav-{tab_id}", n_clicks=0, className="pv-nav",
        style=_nav_style(active),
    )


def _lang_button(code: str, active: bool) -> html.Button:
    return html.Button(
        code.upper(), id=f"lang-{code}", n_clicks=0,
        style={
            "flex": 1, "padding": "5px 0", "borderRadius": 7,
            "border": "1px solid rgba(255,255,255,0.12)",
            "background": "rgba(22,163,74,0.18)" if active else "transparent",
            "color": "#e8edf3" if active else C["sidebarText"],
            "fontWeight": 700, "fontSize": 11, "cursor": "pointer",
        },
    )


def _language_selector(lang: str) -> html.Div:
    return html.Div([
        html.Div(t("sidebar.language", lang), style={
            "fontSize": 9.5, "fontWeight": 700, "textTransform": "uppercase",
            "letterSpacing": 1.2, "color": C["sidebarLabel"], "marginBottom": 6}),
        html.Div([_lang_button("en", lang == "en"),
                  _lang_button("es", lang == "es")],
                 style={"display": "flex", "gap": 6}),
    ], style={"padding": "0 16px 6px"})


def _header(lang: str) -> html.Div:
    return html.Div([
        html.Div([
            html.Div(icon("grid", color="#ffffff", size=19), style={
                "width": 36, "height": 36, "borderRadius": 9,
                "background": C["accent"], "display": "flex",
                "alignItems": "center", "justifyContent": "center"}),
            html.Div([
                html.Div(t("app.title", lang),
                         style={"fontSize": 15, "fontWeight": 700,
                                "color": C["onDark"], "letterSpacing": -0.2}),
                html.Div(t("app.subtitle", lang),
                         style={"fontSize": 11, "color": C["onDarkDim"],
                                "marginTop": 1}),
            ]),
        ], style={"display": "flex", "alignItems": "center", "gap": 13}),
        html.Div(id="header-live",
                 style={"display": "flex", "alignItems": "center", "gap": 12}),
    ], style={"flex": "none", "background": C["header"], "padding": "0 22px",
              "height": 60, "display": "flex", "alignItems": "center",
              "justifyContent": "space-between"})


def _sidebar(lang: str, tab: str) -> html.Div:
    items = [_nav_item(tid, ic, tid == tab, lang) for tid, ic in NAV]
    return html.Div([
        html.Div(t("sidebar.workflow", lang), style={
            "fontSize": 9.5, "fontWeight": 700, "textTransform": "uppercase",
            "letterSpacing": 1.4, "color": C["sidebarLabel"],
            "padding": "0 22px", "marginBottom": 12}),
        *items,
        # Footer pinned at the bottom-left: language selector + source info.
        html.Div([
            _language_selector(lang),
            html.Div([t("sidebar.source", lang), html.Br(), "60 V · 170 A · 5 kW"],
                     style={"fontSize": 10, "color": C["sidebarLabel"],
                            "lineHeight": 1.6, "padding": "8px 22px 4px"}),
        ], style={"marginTop": "auto"}),
    ], style={"flex": "none", "width": 212, "background": C["sidebar"],
              "padding": "16px 0", "display": "flex",
              "flexDirection": "column"})


def _content(lang: str, tab: str) -> html.Div:
    return html.Div(
        [html.Div([page_header(t(f"nav.{tid}", lang), t(f"sub.{tid}", lang)),
                   TAB_RENDERERS[tid](lang)],
                  id=f"panel-{tid}",
                  style={"display": "block" if tid == tab else "none"})
         for tid, _ic in NAV],
        style={"flex": 1, "overflow": "auto", "padding": 22,
               "background": C["bg"]},
    )


def _shell(lang: str, tab: str) -> list:
    return [
        _header(lang),
        html.Div([_sidebar(lang, tab), _content(lang, tab)],
                 style={"flex": 1, "display": "flex", "minHeight": 0}),
    ]


# ── Layout ────────────────────────────────────────────────────────────────────
app.layout = html.Div([

    # Stores — client-side memory
    dcc.Store(id="store-nasa",         storage_type="memory"),
    dcc.Store(id="store-profile",      storage_type="memory"),
    dcc.Store(id="store-profile-meta", storage_type="memory"),   # active profile metadata
    dcc.Store(id="store-loc-idx",      data=0),
    dcc.Store(id="store-scpi-log",     data=[]),
    dcc.Store(id="store-exec-state",   data={"running": False, "step": 0}),
    dcc.Store(id="main-tabs",          data="tab-0"),       # active tab (navigation)
    dcc.Store(id="lang",               data=DEFAULT_LANG),  # UI language (EN/ES)

    # Intervals — polling
    dcc.Interval(id="interval-exec", interval=300, disabled=True),
    dcc.Interval(id="interval-live", interval=500, disabled=True),

    # Header + sidebar + panels. Pre-built for the default language so every
    # component id exists at load (callbacks reference them); render_shell only
    # rebuilds it when the language actually changes.
    html.Div(id="app-shell",
             children=_shell(DEFAULT_LANG, "tab-0"),
             style={"flex": 1, "display": "flex", "flexDirection": "column",
                    "minHeight": 0}),

], style={"display": "flex", "flexDirection": "column", "height": "100vh",
          "background": C["bg"], "fontFamily": FONT, "color": C["text"]})


# ── Build/rebuild the shell on language change ───────────────────────────────
@app.callback(
    Output("app-shell", "children"),
    Input("lang", "data"),
    State("main-tabs", "data"),
    prevent_initial_call=True,   # initial shell is pre-built in the layout
)
def render_shell(lang, tab):
    return _shell(lang or DEFAULT_LANG, tab or "tab-0")


# ── Language selector: EN/ES buttons → lang store ────────────────────────────
@app.callback(
    Output("lang", "data"),
    Input("lang-en", "n_clicks"),
    Input("lang-es", "n_clicks"),
    prevent_initial_call=True,
)
def set_language(_en, _es):
    trig = ctx.triggered_id
    if trig == "lang-es":
        return "es"
    if trig == "lang-en":
        return "en"
    return dash.no_update


# ── Navigation: sidebar click → active tab ───────────────────────────────────
@app.callback(
    Output("main-tabs", "data"),
    [Input(f"nav-{t_}", "n_clicks") for t_, _ic in NAV],
    prevent_initial_call=True,
)
def nav_click(*_clicks):
    trig = ctx.triggered_id
    if not trig:
        return dash.no_update
    return trig.replace("nav-", "", 1)   # "nav-tab-3" → "tab-3"


# ── Show/hide panels and highlight the active sidebar item ───────────────────
@app.callback(
    [Output(f"panel-{t_}", "style") for t_, _ic in NAV]
    + [Output(f"nav-{t_}", "style") for t_, _ic in NAV],
    Input("main-tabs", "data"),
)
def switch_tab(tab: str):
    panels = [{"display": "block" if t_ == tab else "none"} for t_, _ic in NAV]
    navs = [_nav_style(t_ == tab) for t_, _ic in NAV]
    return panels + navs


# ── Entry point ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    # debug=False by default (Fase 3.2): the Werkzeug auto-reloader restarts the
    # process on file changes, which can duplicate/orphan the live serial handle
    # (EAMonitor, bridge). Opt in explicitly with --debug, same pattern as
    # --desktop. threaded=True (Fase 2.1) so a slow NASA POWER call (up to 30 s)
    # in one callback doesn't freeze the single worker feeding the live monitor.
    debug = "--debug" in sys.argv
    if "--desktop" in sys.argv:
        try:
            import webview
            th = threading.Thread(
                target=lambda: app.run(debug=False, port=8050, threaded=True),
                daemon=True)
            th.start()
            time.sleep(1.5)
            webview.create_window("Photovoltaic panel emulator",
                                  "http://localhost:8050",
                                  width=1280, height=800, resizable=True)
            webview.start()
        except ImportError:
            print("pywebview not installed — opening in browser.")
            app.run(debug=False, port=8050, threaded=True)
    else:
        print("\n  Photovoltaic panel emulator  →  http://localhost:8050\n")
        app.run(debug=debug, port=8050, threaded=True)
