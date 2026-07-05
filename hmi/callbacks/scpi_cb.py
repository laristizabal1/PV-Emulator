"""
hmi/callbacks/scpi_cb.py
=========================
Callbacks of Tab 3 — SCPI Control + Modbus TCP Bridge.

Registered callbacks (8):
    - connect_scpi          -> open COM port and verify *IDN? + enable SYST:LOCK ON
    - bridge_control        -> start / stop ScpiModbusBridge in a thread
    - update_bridge_status  -> poll the bridge status (interval-live)
    - manual_control        -> OUTP ON/OFF with manual V and I
    - toggle_exec           -> start / stop the PV profile in a thread
    - update_exec_progress  -> profile progress (interval-exec)
    - update_terminal       -> SCPI terminal with sent commands
    - update_exec_info      -> shows step count and estimated duration

FIXES APPLIED:
    - set_output() -> set_output_fast() in manual_control (correct method)
    - SCPI terminal shows SYST:LOCK ON instead of SYST:REM:TRAN ON
    - the terminal now receives inp-dt as State and shows the user's real wait
      instead of the hardcoded DT_MIN (bug: always showed 200ms)
    - update_exec_progress receives inp-dt as State to show the correct Δt
    - bridge_control calls monitor.set_bridge() when starting/stopping the bridge
      to avoid serial collision between EAMonitor and ScpiModbusBridge
"""

import threading
from dash import Input, Output, State, html, ctx, no_update

from config.hardware import (C, MONO, DT_MIN, DEFAULT_PORT, DEFAULT_MODBUS_HOST,
                             DEFAULT_MODBUS_PORT, ENVELOPE_MODE)
from config.devices  import get_device, DEFAULT_DEVICE_KEY
from comm.scpi       import SCPIController
from comm.monitor    import EAMonitor
from hmi.layout.components import status_badge
from hmi.i18n        import t

# Bridge — importacion defensiva
try:
    from comm.bridge import ScpiModbusBridge, BridgeConfig
    BRIDGE_AVAILABLE = True
except ImportError:
    BRIDGE_AVAILABLE = False


# ── Estado global compartido entre callbacks ──────────────────────────────────
_controller:    SCPIController              = SCPIController(port=DEFAULT_PORT)
_monitor:       EAMonitor                  = EAMonitor(_controller)   # comparte el mismo puerto
_exec_thread:   threading.Thread           = None
_bridge:        "ScpiModbusBridge | None"  = None
_bridge_thread: threading.Thread           = None

# Real profile progress — updated by progress_cb in run_profile()
# The callback reads from here instead of estimating with n_intervals
_exec_progress: dict = {"idx": 0, "total": 0, "step": {}}


def register(app):

    # ─── 1. Conectar SCPI ────────────────────────────────────────────────────
    @app.callback(
        Output("connect-status", "children"),
        Input("btn-connect", "n_clicks"),
        State("dd-port",     "value"),
        State("lang",        "data"),
        prevent_initial_call=True,
    )
    def connect_scpi(_n, port, lang):
        lang = lang or "en"
        from comm.scpi import SERIAL_AVAILABLE
        if not SERIAL_AVAILABLE:
            return html.Div(
                "pyserial not installed: pip install pyserial",
                style={"color": C["red"], "fontSize": 11},
            )
        if not port or port == "NONE":
            return html.Div(t("scpi.port_first", lang),
                            style={"color": C["red"], "fontSize": 11})
        try:
            _controller.port = port
            idn = _controller.connect()
            return status_badge(
                f"{t('scpi.connected', lang)}: {idn[:48]} · "
                f"{t('scpi.remote_active', lang)}", "ok")
        except Exception as exc:
            return html.Div(f"Error: {exc}",
                            style={"color": C["red"], "fontSize": 11})

    # ─── 2. Iniciar / detener Bridge Modbus ──────────────────────────────────
    @app.callback(
        Output("bridge-status", "children"),
        Input("btn-bridge-start", "n_clicks"),
        Input("btn-bridge-stop",  "n_clicks"),
        State("dd-port",          "value"),
        State("inp-modbus-host",  "value"),
        State("inp-modbus-port",  "value"),
        State("inp-bridge-poll",  "value"),
        State("lang",             "data"),
        prevent_initial_call=True,
    )
    def bridge_control(_start, _stop, port, host, mb_port, poll, lang):
        global _bridge, _bridge_thread
        lang = lang or "en"

        triggered = ctx.triggered_id

        # STOP
        if triggered == "btn-bridge-stop":
            if _bridge:
                _bridge.stop()
                _bridge = None
            _monitor.set_bridge(None)   # the monitor reads SCPI directly again
            return html.Div(t("scpi.bridge_stopped", lang),
                            style={"color": C["red"], "fontWeight": 600,
                                   "fontSize": 11})

        # START
        if not BRIDGE_AVAILABLE:
            return html.Div(
                "pymodbus not installed: pip install 'pymodbus>=3.12'",
                style={"color": C["red"], "fontSize": 11},
            )
        if not port or port == "NONE":
            return html.Div(t("scpi.port_first", lang),
                            style={"color": C["red"], "fontSize": 11})

        if _bridge and _bridge.running:
            return html.Div(t("scpi.bridge_running", lang),
                            style={"color": C["dim"], "fontSize": 11})

        # Cross-check (Fase 0.2): the shared lock (0.1) keeps the serial safe if
        # both run, but a bridge polling the port while a profile drives it is
        # confusing — block it with a clear message instead.
        if _exec_thread is not None and _exec_thread.is_alive():
            return status_badge(t("scpi.busy_profile", lang), "error")

        if not _controller.connected:
            return html.Div(
                t("scpi.connect_first", lang),
                style={"color": C["red"], "fontSize": 11},
            )

        try:
            cfg = BridgeConfig(
                serial_port      = port,
                modbus_host      = host  or DEFAULT_MODBUS_HOST,
                modbus_port      = int(mb_port or DEFAULT_MODBUS_PORT),
                poll_interval_ms = float(poll or 20),
            )
            _bridge = ScpiModbusBridge(cfg)

            _bridge_thread = threading.Thread(
                target=lambda: _bridge.start_shared(_controller._ser,
                                                    _controller._lock),
                daemon=True, name="ModbusBridge"
            )
            _bridge_thread.start()
            _monitor.set_bridge(_bridge)   # the monitor reads from the bridge cache

            return html.Div(
                f"{t('scpi.bridge_active', lang)}: "
                f"{host or DEFAULT_MODBUS_HOST}:{mb_port or DEFAULT_MODBUS_PORT} "
                f"(poll {int(poll or 20)} ms)",
                style={"color": C["accentDark"], "fontWeight": 600,
                       "fontSize": 11},
            )
        except Exception as exc:
            return html.Div(f"{t('scpi.bridge_start_err', lang)}: {exc}",
                            style={"color": C["red"], "fontSize": 11})

    # ─── 3. Bridge status in the HMI (polling via interval-live) ─────────────
    @app.callback(
        Output("bridge-modbus-readout", "children"),
        Input("interval-live", "n_intervals"),
        State("lang",          "data"),
    )
    def update_bridge_status(_n, lang):
        lang = lang or "en"
        if not _bridge or not _bridge.running:
            return html.Div(t("scpi.bridge_inactive", lang),
                            style={"color": C["dim"], "fontSize": 11,
                                   "gridColumn": "1 / -1"})

        r = _bridge.get_readings()
        items = [
            ("V meas",  f"{r['V']:.3f} V",    C["onDark"]),
            ("I meas",  f"{r['I']:.4f} A",    C["onDark"]),
            ("P meas",  f"{r['P']:.2f} W",    C["onDark"]),
            ("V set",   f"{r['Vset']:.3f} V", C["onDarkDim"]),
            ("I set",   f"{r['Iset']:.4f} A", C["onDarkDim"]),
            ("OUT",     "ON" if r["output"] else "OFF",
             C["green"] if r["output"] else C["red"]),
        ]
        return [
            html.Div([
                html.Div(val, style={"fontSize": 15, "fontWeight": 700,
                                     "color": color, "fontFamily": MONO}),
                html.Div(lbl, style={"fontSize": 9, "color": C["onDarkDimmer"]}),
            ], style={"textAlign": "center", "padding": "8px 4px",
                      "background": C["monitorCard"], "borderRadius": 8,
                      "border": f'1px solid {C["monitorBorder"]}'})
            for lbl, val, color in items
        ]

    # ─── 4. Manual control ────────────────────────────────────────────────────
    @app.callback(
        Output("manual-status",  "children"),
        Output("store-scpi-log", "data"),
        Input("btn-outp-on",     "n_clicks"),
        Input("btn-outp-off",    "n_clicks"),
        State("inp-manual-v",    "value"),
        State("inp-manual-i",    "value"),
        State("store-scpi-log",  "data"),
        State("lang",            "data"),
        prevent_initial_call=True,
    )
    def manual_control(_n_on, _n_off, V, I, log, lang):
        lang = lang or "en"
        triggered = ctx.triggered_id
        log = log or []
        try:
            if triggered == "btn-outp-on":
                V = float(V or 0.0)
                I = float(I or 0.0)
                _controller.set_output_fast(V, I, on=True)
                log.extend([f"VOLT {V:.3f}", f"CURR {I:.3f}", "OUTP ON"])
                status = html.Div(
                    f"{t('scpi.out_on', lang)} — {V} V / {I} A",
                    style={"color": C["accentDark"], "fontWeight": 600},
                )
            else:
                _controller.output_off()
                log.append("OUTP OFF")
                status = html.Div(t("scpi.out_off", lang),
                                  style={"color": C["red"], "fontWeight": 600})
        except Exception as exc:
            status = html.Div(f"Error: {exc}", style={"color": C["red"]})

        return status, log[-60:]

    # ─── 5. Ejecutar / detener perfil completo ────────────────────────────────
    @app.callback(
        Output("interval-exec",    "disabled"),
        Output("store-exec-state", "data"),
        Input("btn-exec-start",    "n_clicks"),
        Input("btn-exec-stop",     "n_clicks"),
        State("store-profile",     "data"),
        State("store-profile-meta","data"),
        State("inp-dt",            "value"),
        State("dd-port",           "value"),
        State("dd-dut",            "value"),
        State("store-exec-state",  "data"),
        prevent_initial_call=True,
    )
    def toggle_exec(_n_start, _n_stop, profile, profile_meta, dt, port, dut, state):
        global _exec_thread

        triggered = ctx.triggered_id

        if triggered == "btn-exec-stop":
            _controller.stop()
            return True, {"running": False, "step": 0}

        if triggered == "btn-exec-start":
            if not profile:
                return True, state

            # Reentrancy guard (Fase 0.3): ignore a second Start while a profile
            # thread is still alive — otherwise two _run() threads drive the same
            # port. Mirrors how bridge_control guards on _bridge.running.
            if _exec_thread is not None and _exec_thread.is_alive():
                return no_update, no_update

            # Cross-check (Fase 0.2): don't drive a profile while the bridge is
            # polling the port. The shared lock (0.1) prevents corruption, but
            # both owning the port at once is confusing.
            if _bridge is not None and _bridge.running:
                return no_update, no_update

            _controller.port = port or DEFAULT_PORT
            dt_ms = max(dt or 1000, DT_MIN)

            # The selected DUT derives the emulation envelope and is recorded in
            # the session metadata. ENVELOPE_MODE remains the global fallback when
            # there is no DUT (an invalid key falls back to the catalog default).
            device   = get_device(dut or DEFAULT_DEVICE_KEY)
            envelope = device.envelope or ENVELOPE_MODE
            meta = dict(profile_meta or {})
            meta["dut"]          = device.key
            meta["dut_label"]    = device.label
            meta["envelope"]     = envelope
            meta["dut_has_mppt"] = device.has_mppt
            # dt_ms real de ejecución (inp-dt de Tab 3). profile_meta["dt_ms"]
            # viene del campo Δt de la pestaña Perfiles (inp-dt-perfiles) y puede
            # desincronizarse; el que de verdad rige run_profile es éste.
            meta["dt_ms"]        = dt_ms

            def _progress_cb(i, total, step):
                """Updates the real step — read by update_exec_progress."""
                _exec_progress["idx"]   = i
                _exec_progress["total"] = total
                _exec_progress["step"]  = step

            def _run():
                global _exec_progress
                _exec_progress = {"idx": 0, "total": len(profile), "step": profile[0] if profile else {}}
                try:
                    if not _controller.connected:
                        _controller.connect()
                    _monitor.start(meta=meta)
                    # envelope derived from the selected DUT (plan area B):
                    # mppt_inverter→cp, eload→curve, generic→curve. Si el
                    # perfil no trae Voc/Isc (perfil viejo en el store),
                    # run_profile degrada a "direct" con warning.
                    _controller.run_profile(profile, dt_ms,
                                            progress_cb=_progress_cb,
                                            envelope=envelope)
                except Exception as exc:
                    _controller._last_error = str(exc)
                    print(f"[SCPI ERROR] {exc}")
                finally:
                    _monitor.stop()

            _exec_thread = threading.Thread(target=_run, daemon=True,
                                             name="ProfileExec")
            _exec_thread.start()
            return False, {"running": True, "step": 0}

        return True, state

    # ─── 6. Profile progress + live measurements ─────────────────────────────
    @app.callback(
        Output("progress-bar",    "style"),
        Output("exec-step-info",  "children"),
        Output("live-readout",    "children"),   # setpoints
        Output("live-measured",   "children"),   # live MEAS readings
        Input("interval-exec",    "n_intervals"),
        State("store-profile",    "data"),
        State("store-exec-state", "data"),
        State("inp-dt",           "value"),
        State("lang",             "data"),
    )
    def update_exec_progress(n_intervals, profile, state, dt, lang):
        lang = lang or "en"
        bar_base = {
            "height":       "100%",
            "background":   C["accent"],
            "borderRadius": 4,
            "transition":   "width 0.3s",
        }

        # ── Error in the thread ───────────────────────────────────────────────
        last_err = getattr(_controller, "_last_error", None)
        if last_err:
            _controller._last_error = None
            bar_rojo = {**bar_base, "width": "100%", "background": C["red"]}
            msg = status_badge(f"{t('scpi.profile_err', lang)}: {last_err}", "error")
            return bar_rojo, msg, [], []

        # ── No profile or stopped ─────────────────────────────────────────────
        if not profile or not state.get("running"):
            return {**bar_base, "width": "0%"}, t("scpi.no_profile", lang), [], []

        # ── Normal progress ───────────────────────────────────────────────────
        dt_ms = max(dt or 1000, DT_MIN)
        total = len(profile)

        idx  = min(_exec_progress.get("idx", 0), total - 1)
        step = _exec_progress.get("step") or profile[idx]
        pct  = round(idx / max(total - 1, 1) * 100, 1)

        info = (f"{t('scpi.running_step', lang)} {idx + 1} {t('scpi.of', lang)} "
                f"{total} · Δt {dt_ms} ms/step")

        # ── Readout tile for the DARK monitor ────────────────────────────────
        # set → green number (#4ade80); meas → light number. Background #1e2632.
        def _card(val, label, color):
            return html.Div([
                html.Div(f"{val}", style={
                    "fontSize": 30, "fontWeight": 700,
                    "color": color, "fontFamily": MONO, "lineHeight": 1,
                }),
                html.Div(label, style={
                    "fontSize": 10, "color": C["dim"], "marginTop": 7,
                }),
            ], style={
                "padding": "16px 6px", "background": C["monitorCard"],
                "borderRadius": 10, "textAlign": "center",
                "border": f'1px solid {C["monitorBorder"]}',
            })

        readout = [
            _card(step.get("V_set", 0), "V_set · V", C["green"]),
            _card(step.get("I_set", 0), "I_set · A", C["green"]),
            _card(step.get("P_set", 0), "P_set · W", C["green"]),
        ]

        # ── Live readings from EAMonitor (reads cache, non-blocking) ─────────
        meas = _monitor.get_latest()
        if meas:
            measured = [
                _card(f"{meas['V_dc']:.3f}", "V_meas · V", C["onDark"]),
                _card(f"{meas['I_dc']:.4f}", "I_meas · A", C["onDark"]),
                _card(f"{meas['P_dc']:.2f}", "P_meas · W", C["onDark"]),
            ]
        else:
            measured = [html.Div(
                t("scpi.monitor_idle", lang),
                style={"color": C["onDarkDimmer"], "fontSize": 11,
                       "gridColumn": "1 / -1", "padding": 8},
            )]

        return {**bar_base, "width": f"{pct}%"}, info, readout, measured

    # ─── 7. Terminal SCPI ─────────────────────────────────────────────────────
    @app.callback(
        Output("scpi-terminal", "children"),
        Output("scpi-summary",  "children"),
        Input("store-scpi-log", "data"),
        Input("store-profile",  "data"),
        State("inp-dt",         "value"),
    )
    def update_terminal(log, profile, dt):
        dt_ms = max(dt or 1000, DT_MIN)

        preview = []
        if profile:
            for step in profile[:8]:
                preview += [
                    f"VOLT {step['V_set']:.3f}",
                    f"CURR {step['I_set']:.3f}",
                    f"# wait {dt_ms}ms",
                ]
            if len(profile) > 8:
                preview.append(f"# ... {len(profile) - 8} more steps")

        all_cmds = (
            ["SYST:LOCK ON", "OUTP ON"]
            + preview
            + ["OUTP OFF"]
            + (log or [])
        )[:60]

        lines = [
            html.Div([
                html.Span(f"{i+1:03d}",
                          style={"color": C["onDarkDimmer"], "marginRight": 10}),
                html.Span(cmd, style={
                    "color": C["onDarkDim"] if cmd.startswith("#") else C["green"]
                }),
            ])
            for i, cmd in enumerate(all_cmds)
        ]

        total = len(profile) * 3 + 4 if profile else 0
        summary = (
            f"Estimated total: {total} cmds · SCPI ASCII · "
            f"USB/COM · Δt {dt_ms} ms/step"
        )
        return lines, summary

    # ─── 8. Profile step count and duration ──────────────────────────────────
    @app.callback(
        Output("exec-n-steps",  "children"),
        Output("exec-duration", "children"),
        Input("inp-dt",         "value"),
        Input("store-profile",  "data"),
    )
    def update_exec_info(dt, profile):
        if not profile:
            return "—", "—"
        n       = len(profile)
        dt_ms   = max(dt or 1000, DT_MIN)
        total_s = n * dt_ms / 1000.0
        if total_s < 60:
            dur = f"{total_s:.0f} s"
        elif total_s < 3600:
            dur = f"{total_s/60:.1f} min"
        else:
            dur = f"{total_s/3600:.2f} h"
        return str(n), dur