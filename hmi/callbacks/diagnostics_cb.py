"""
hmi/callbacks/diagnostics_cb.py
===============================
Callbacks of Tab 5 — Power device diagnostics.

Renders the post-execution analysis figures (pipeline.post_exec_plots) INSIDE
Dash, from:
  - the LIVE session (EAMonitor buffer of scpi_cb, still in memory), or
  - a session saved in data/sessions/sesion_*.json.

This lets the operator diagnose the DUT without running any script. Static image
generation for the paper still lives in experiments/paper_figs.py.

Callbacks (2):
    - refresh_session_list  → repopulates the session dropdown when entering the tab
    - render_diagnostico    → builds the 3 figures + the metadata line
"""

import json
from datetime import datetime

import plotly.graph_objects as go
from dash import Input, Output, State, html, no_update

from config.hardware import C
from comm.monitor import _SAVE_DIR
from pipeline.post_exec_plots import build_post_exec_figs
from hmi.i18n import t

_LIVE = "__live__"


# ── Helpers ───────────────────────────────────────────────────────────────────

def _fmt_ts(stem: str) -> str:
    """'sesion_20260620_022946' → '2026-06-20 02:29:46'."""
    try:
        ts = stem.replace("sesion_", "")
        return datetime.strptime(ts, "%Y%m%d_%H%M%S").strftime("%Y-%m-%d %H:%M:%S")
    except ValueError:
        return stem


def _session_options(lang: str = "en") -> list[dict]:
    """Dropdown options: "Live" + saved sessions (most recent first)."""
    opts = [{"label": t("diag.live", lang), "value": _LIVE}]
    if _SAVE_DIR.exists():
        files = sorted(_SAVE_DIR.glob("sesion_*.json"), reverse=True)
        opts += [{"label": _fmt_ts(f.stem), "value": f.name} for f in files]
    return opts


def _load_session(value: str) -> tuple[list[dict], dict]:
    """
    Return (buffer, meta) for the selected session.

    "Live" reads from the EAMonitor shared with scpi_cb (deferred import to
    avoid a circular dependency). A saved session is read from its JSON.
    """
    if value == _LIVE:
        try:
            from hmi.callbacks.scpi_cb import _monitor
            return _monitor.get_buffer(), _monitor.get_meta()
        except Exception:
            return [], {}
    path = _SAVE_DIR / value
    if not path.exists():
        return [], {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return [], {}
    return data.get("mediciones", []), data.get("perfil", {})


def _empty_fig(msg: str) -> go.Figure:
    fig = go.Figure()
    fig.update_layout(
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        font=dict(color="#94a3b8", size=12),
        margin=dict(l=20, r=20, t=20, b=20), height=200,
        xaxis=dict(visible=False), yaxis=dict(visible=False),
        annotations=[dict(text=msg, showarrow=False,
                          xref="paper", yref="paper", x=0.5, y=0.5)],
    )
    return fig


def _meta_line(meta: dict, n: int, source_label: str, lang: str = "en") -> html.Div:
    if n == 0:
        return html.Div(
            t("diag.no_meas", lang),
            style={"color": C["red"], "fontSize": 11},
        )
    bits = [f"{t('diag.session', lang)}: {source_label}",
            f"{n} {t('diag.samples', lang)}"]
    if meta.get("dut_label"):
        bits.append(f"{t('sum.dut', lang)}: {meta['dut_label']}")
    if meta.get("envelope"):
        bits.append(f"{t('sum.envelope', lang)}: {meta['envelope']}")
    if meta.get("ciudad"):
        bits.append(f"{t('sum.location', lang)}: {meta['ciudad']}")
    if meta.get("estrategia"):
        bits.append(f"{t('sum.strategy', lang)}: {meta['estrategia']}")
    return html.Div(" · ".join(bits),
                    style={"color": C["textMed"], "fontSize": 11,
                           "fontWeight": 600})


# ── Registration ──────────────────────────────────────────────────────────────

def register(app):

    # ─── 1. Repopulate the session list on tab entry or refresh ──────────────
    @app.callback(
        Output("dd-diag-session", "options"),
        Input("main-tabs",        "data"),
        Input("btn-diag-refresh", "n_clicks"),
        Input("lang",             "data"),
    )
    def refresh_session_list(tab, _n, lang):
        # Only rescan disk when the diagnostics tab is visible.
        if tab != "tab-5":
            return no_update
        return _session_options(lang or "en")

    # ─── 2. Build figures + metadata ─────────────────────────────────────────
    @app.callback(
        Output("diag-fig-pvi",   "figure"),
        Output("diag-fig-eff",   "figure"),
        Output("diag-fig-error", "figure"),
        Output("diag-meta",      "children"),
        Input("btn-diag-refresh", "n_clicks"),
        Input("dd-diag-session",  "value"),
        Input("main-tabs",        "data"),
        Input("lang",             "data"),
    )
    def render_diagnostico(_n, session, tab, lang):
        # Do not rebuild while the tab is not visible (saves work).
        if tab != "tab-5":
            return no_update, no_update, no_update, no_update

        lang = lang or "en"
        buffer, meta = _load_session(session or _LIVE)
        src = (t("diag.live", lang) if (session or _LIVE) == _LIVE
               else _fmt_ts(session.replace(".json", "")))

        if not buffer:
            empty = _empty_fig(t("diag.no_data", lang))
            return empty, empty, empty, _meta_line(meta, 0, src, lang)

        figs = build_post_exec_figs(buffer, meta, lang=lang)
        if not figs:
            empty = _empty_fig(t("diag.no_setpoints", lang))
            return empty, empty, empty, _meta_line(meta, len(buffer), src, lang)

        return (figs["pvi"], figs["mppt"], figs["error"],
                _meta_line(meta, len(buffer), src, lang))
