"""
hmi/layout/tab_scpi.py
=======================
Layout of Tab 3 — SCPI / Control in real time.

Left column  : port selection, manual OUTP ON/OFF control,
               RUN / STOP profile buttons, progress bar.
Right column : live readout (current V, I, P), dark SCPI terminal.

All outputs are filled by the callbacks in scpi_cb.py.
"""

from dash import dcc, html
from config.hardware import C, MONO, DEFAULT_MODBUS_HOST, DEFAULT_MODBUS_PORT
from config.devices import get_dropdown_options, DEFAULT_DEVICE_KEY
from hmi.layout.components import card, dot
from hmi.i18n import t


def tab_scpi(lang: str = "en") -> html.Div:
    """Return the full layout of Tab 3."""
    return html.Div([
        html.Div([
            _panel_control(lang),
            _panel_monitor(lang),
        ], style={"display": "grid", "gridTemplateColumns": "340px 1fr", "gap": 14}),
    ])


def _panel_control(lang: str) -> html.Div:
    """Left column: connection, manual control and profile execution."""
    from comm.scpi import list_ports   # local import
    ports = list_ports()
    return card([

        # ── Port selection ────────────────────────────────────────────────────
        html.Div(t("scpi.port", lang),
                 style={"fontSize": 11, "color": C["dim"], "marginBottom": 4}),
        dcc.Dropdown(
            id="dd-port",
            options=ports,
            value=ports[0]["value"] if ports else "NONE",
            clearable=False,
            style={"fontSize": 12, "marginBottom": 12},
        ),
        html.Button(
            t("scpi.connect", lang),
            id="btn-connect",
            n_clicks=0,
            style=_btn_style(C["accentBg"], C["accentDark"], C["border"]),
        ),
        html.Div(id="connect-status", style={"fontSize": 11, "marginBottom": 14}),

        html.Hr(style={"borderColor": C["border"]}),

        # ── Manual control ────────────────────────────────────────────────────
        _section_label(t("scpi.manual", lang)),
        html.Div([
            html.Div([
                html.Div(t("scpi.voltage", lang),
                         style={"fontSize": 11, "color": C["dim"], "marginBottom": 3}),
                dcc.Input(id="inp-manual-v", type="number",
                          value=10.0, min=0, max=60, step=0.1,
                          style=_input_style()),
            ], style={"flex": 1}),
            html.Div([
                html.Div(t("scpi.current", lang),
                         style={"fontSize": 11, "color": C["dim"], "marginBottom": 3}),
                dcc.Input(id="inp-manual-i", type="number",
                          value=5.0, min=0, max=170, step=0.1,
                          style=_input_style()),
            ], style={"flex": 1}),
        ], style={"display": "flex", "gap": 8, "marginBottom": 10}),

        html.Div([
            html.Button("OUTP ON",  id="btn-outp-on",  n_clicks=0,
                        style=_btn_style(C["accent"], "#fff", "none")),
            html.Button("OUTP OFF", id="btn-outp-off", n_clicks=0,
                        style=_btn_style(C["red"],    "#fff", "none")),
        ], style={"display": "flex", "gap": 8, "marginBottom": 14}),

        html.Div(id="manual-status", style={"fontSize": 11}),

        html.Hr(style={"borderColor": C["border"]}),

        # ── Profile execution ─────────────────────────────────────────────────
        _section_label(t("scpi.exec", lang)),

        # ── Device under test (DUT) ───────────────────────────────────────────
        # Sets the emulation envelope and how the session is analyzed/recorded.
        html.Div(t("scpi.dut", lang),
                 style={"fontSize": 11, "color": C["dim"], "marginBottom": 4}),
        dcc.Dropdown(
            id="dd-dut",
            options=get_dropdown_options(),
            value=DEFAULT_DEVICE_KEY,
            clearable=False,
            style={"fontSize": 12, "marginBottom": 10},
        ),

        html.Div(id="exec-step-info",
                 style={"fontSize": 11, "color": C["dim"], "marginBottom": 8}),

        # Time-step control + step count and duration info
        html.Div([
            html.Div([
                html.Div(t("scpi.dt_step", lang),
                         style={"fontSize": 11, "color": C["dim"],
                                "marginBottom": 3}),
                dcc.Input(
                    id="inp-dt",
                    type="number",
                    value=1000,
                    min=200,
                    max=60000,
                    step=100,
                    style=_input_style(),
                ),
            ], style={"flex": 1}),
            html.Div([
                html.Div(t("scpi.total_steps", lang),
                         style={"fontSize": 11, "color": C["dim"],
                                "marginBottom": 3}),
                html.Div(id="exec-n-steps",
                         style={"fontSize": 13, "fontWeight": 700,
                                "fontFamily": "monospace",
                                "color": C["textMed"], "padding": "6px 0"}),
            ], style={"flex": 1}),
            html.Div([
                html.Div(t("scpi.total_dur", lang),
                         style={"fontSize": 11, "color": C["dim"],
                                "marginBottom": 3}),
                html.Div(id="exec-duration",
                         style={"fontSize": 13, "fontWeight": 700,
                                "fontFamily": "monospace",
                                "color": C["textMed"], "padding": "6px 0"}),
            ], style={"flex": 1}),
        ], style={"display": "flex", "gap": 10, "marginBottom": 10}),

        html.Div([
            html.Button(t("scpi.run", lang), id="btn-exec-start", n_clicks=0,
                        style=_btn_style(C["accent"], "#fff", "none",
                                         font_size=12, padding="10px")),
            html.Button(t("scpi.stop", lang), id="btn-exec-stop",  n_clicks=0,
                        style=_btn_style(C["red"],   "#fff", "none",
                                         font_size=12, padding="10px")),
        ], style={"display": "flex", "gap": 8, "marginBottom": 10}),

        # Progress bar
        html.Div(
            style={
                "width":        "100%",
                "height":       6,
                "background":   C["borderLight"],
                "borderRadius": 4,
                "overflow":     "hidden",
            },
            children=[
                html.Div(
                    id="progress-bar",
                    style={
                        "width":        "0%",
                        "height":       "100%",
                        "background":   C["accent"],
                        "borderRadius": 4,
                        "transition":   "width 0.3s",
                    },
                )
            ],
        ),

        html.Hr(style={"borderColor": C["border"]}),

        # ── Modbus TCP bridge ─────────────────────────────────────────────────
        _section_label(t("scpi.bridge", lang)),

        # Modbus host and port
        html.Div([
            html.Div([
                html.Div(t("scpi.host", lang),
                         style={"fontSize": 11, "color": C["dim"], "marginBottom": 3}),
                dcc.Input(
                    id="inp-modbus-host",
                    type="text",
                    value=DEFAULT_MODBUS_HOST,
                    debounce=True,
                    style=_input_style(),
                ),
            ], style={"flex": 2}),
            html.Div([
                html.Div(t("scpi.tcp_port", lang),
                         style={"fontSize": 11, "color": C["dim"], "marginBottom": 3}),
                dcc.Input(
                    id="inp-modbus-port",
                    type="number",
                    value=DEFAULT_MODBUS_PORT,
                    min=1,
                    max=65535,
                    style=_input_style(),
                ),
            ], style={"flex": 1}),
            html.Div([
                html.Div("Poll (ms)",
                         style={"fontSize": 11, "color": C["dim"], "marginBottom": 3}),
                dcc.Input(
                    id="inp-bridge-poll",
                    type="number",
                    value=20,
                    min=20,
                    max=5000,
                    step=10,
                    style=_input_style(),
                ),
            ], style={"flex": 1}),
        ], style={"display": "flex", "gap": 8, "marginBottom": 10}),

        html.Div([
            html.Button(t("scpi.bridge_start", lang), id="btn-bridge-start", n_clicks=0,
                        style=_btn_style(C["accent"], "#fff", "none",
                                         font_size=11, padding="8px")),
            html.Button(t("scpi.bridge_stop", lang), id="btn-bridge-stop",  n_clicks=0,
                        style=_btn_style(C["red"],   "#fff", "none",
                                         font_size=11, padding="8px")),
        ], style={"display": "flex", "gap": 8, "marginBottom": 8}),

        html.Div(id="bridge-status",
                 style={"fontSize": 11, "marginBottom": 10}),

    ], title=t("scpi.card_title", lang))


def _dark_label(text: str) -> html.Div:
    """Section label for the dark monitor."""
    return html.Div(text, style={
        "fontSize": 10, "fontWeight": 700, "color": C["onDarkDimmer"],
        "textTransform": "uppercase", "letterSpacing": 1, "marginBottom": 8,
    })


def _grid3(component_id: str) -> html.Div:
    return html.Div(id=component_id, style={
        "display": "grid", "gridTemplateColumns": "1fr 1fr 1fr",
        "gap": 10, "marginBottom": 16,
    })


def _panel_monitor(lang: str) -> html.Div:
    """Right column: dark monitor — green readouts and SCPI terminal."""
    return html.Div([

        # Header with LIVE indicator
        html.Div([
            html.Div(t("scpi.monitor", lang), style={
                "fontSize": 10, "fontWeight": 700, "color": C["onDarkDim"],
                "textTransform": "uppercase", "letterSpacing": 1.4}),
            html.Div([
                dot(C["pulse"], pulse=True, size=7),
                html.Span(t("scpi.live", lang), style={"fontSize": 10, "fontWeight": 600,
                                                       "color": C["greenDim"]}),
            ], style={"display": "flex", "alignItems": "center", "gap": 6}),
        ], style={"display": "flex", "alignItems": "center",
                  "justifyContent": "space-between", "marginBottom": 16}),

        # Sent setpoints (filled by update_exec_progress)
        _dark_label(t("scpi.setpoints_sent", lang)),
        _grid3("live-readout"),

        # Live measurements (MEAS:VOLT?/CURR?/POW?)
        _dark_label(t("scpi.live_meas", lang)),
        _grid3("live-measured"),

        # Modbus bridge (filled by update_bridge_status)
        _dark_label(t("scpi.bridge_meas", lang)),
        _grid3("bridge-modbus-readout"),

        # SCPI terminal
        _dark_label(t("scpi.commands", lang)),
        html.Div(id="scpi-terminal", style={
            "background": C["monitorTerm"],
            "border": f'1px solid {C["monitorTermBd"]}',
            "borderRadius": 10, "padding": "13px 15px", "maxHeight": 340,
            "overflowY": "auto", "fontFamily": MONO, "fontSize": 11,
            "lineHeight": 1.9,
        }),
        html.Div(id="scpi-summary",
                 style={"marginTop": 8, "fontSize": 10, "color": C["onDarkDimmer"]}),

    ], style={"background": C["monitorBg"], "borderRadius": 13,
              "padding": "18px 20px"})


# ── Private helpers ───────────────────────────────────────────────────────────

def _section_label(text: str) -> html.Div:
    return html.Div(text, style={
        "fontSize":      10,
        "fontWeight":    800,
        "color":         C["dim"],
        "textTransform": "uppercase",
        "letterSpacing": 1,
        "marginBottom":  8,
    })


def _btn_style(bg: str, color: str, border: str,
               font_size: int = 11, padding: str = "8px") -> dict:
    return {
        "flex":         1,
        "padding":      padding,
        "borderRadius": 10,
        "border":       f"1px solid {border}" if border != "none" else "none",
        "background":   bg,
        "color":        color,
        "fontWeight":   800,
        "fontSize":     font_size,
        "cursor":       "pointer",
    }


def _input_style() -> dict:
    return {
        "width":        "100%",
        "padding":      "6px 8px",
        "borderRadius": 8,
        "border":       f"1px solid {C['border']}",
        "fontSize":     12,
        "fontFamily":   "monospace",
        "color":        C["text"],
        "background":   C["white"],
        "boxSizing":    "border-box",
    }
