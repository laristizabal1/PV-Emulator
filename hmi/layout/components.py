# hmi/layout/components.py
"""
Reusable HMI components (approved visual system).

  card / stat_box / divider — containers and metrics
  page_header               — title + subtitle of each panel
  status_badge / dot        — status indicators (replace the emojis ✓ ❌ ● 📍)
  icon                      — SVG icons (Dash html has no SVG elements, so they
                              are embedded as a data-URI inside an html.Img)
"""
import urllib.parse
from dash import html
from config.hardware import C, MONO


# ── SVG icons (data-URI) ──────────────────────────────────────────────────────
# "Feather"-style strokes in a 24x24 viewBox. _STROKE = painted with stroke;
# _FILL = painted with fill (play/stop).
_ICONS_STROKE = {
    "check":    '<polyline points="20 6 9 17 4 12"/>',
    "x":        '<line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/>',
    "location": '<path d="M20 10c0 6-8 12-8 12s-8-6-8-12a8 8 0 0 1 16 0Z"/><circle cx="12" cy="10" r="3"/>',
    "refresh":  '<polyline points="23 4 23 10 17 10"/><path d="M20.49 15a9 9 0 1 1-2.12-9.36L23 10"/>',
    "power":    '<path d="M12 2v9"/><path d="M5.6 8a8 8 0 1 0 12.8 0"/>',
    "download": '<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/>',
    "alert":    '<path d="M10.29 3.86 1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0Z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/>',
    "chevron":  '<polyline points="6 9 12 15 18 9"/>',
    # Navigation icons (sidebar)
    "grid":     '<rect x="3" y="3" width="7" height="7" rx="1"/><rect x="14" y="3" width="7" height="7" rx="1"/><rect x="3" y="14" width="7" height="7" rx="1"/><rect x="14" y="14" width="7" height="7" rx="1"/>',
    "activity": '<polyline points="22 12 18 12 15 21 9 3 6 12 2 12"/>',
    "sliders":  '<line x1="4" y1="21" x2="4" y2="14"/><line x1="4" y1="10" x2="4" y2="3"/><line x1="12" y1="21" x2="12" y2="12"/><line x1="12" y1="8" x2="12" y2="3"/><line x1="20" y1="21" x2="20" y2="16"/><line x1="20" y1="12" x2="20" y2="3"/><line x1="1" y1="14" x2="7" y2="14"/><line x1="9" y1="8" x2="15" y2="8"/><line x1="17" y1="16" x2="23" y2="16"/>',
    "file":     '<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/><line x1="8" y1="13" x2="16" y2="13"/><line x1="8" y1="17" x2="14" y2="17"/>',
    "pulse":    '<path d="M3 12h4l3 8 4-16 3 8h4"/>',
}
_ICONS_FILL = {
    "play": '<polygon points="6 4 20 12 6 20"/>',
    "stop": '<rect x="6" y="6" width="12" height="12" rx="2"/>',
}


def icon(name: str, color: str = "#1b2430", size: int = 14):
    """SVG icon as an html.Img (data-URI). Replaces the emojis in the callbacks."""
    if name in _ICONS_FILL:
        body = (f'<svg xmlns="http://www.w3.org/2000/svg" width="{size}" '
                f'height="{size}" viewBox="0 0 24 24" fill="{color}">'
                f'{_ICONS_FILL[name]}</svg>')
    else:
        inner = _ICONS_STROKE.get(name, _ICONS_STROKE["check"])
        body = (f'<svg xmlns="http://www.w3.org/2000/svg" width="{size}" '
                f'height="{size}" viewBox="0 0 24 24" fill="none" '
                f'stroke="{color}" stroke-width="2" stroke-linecap="round" '
                f'stroke-linejoin="round">{inner}</svg>')
    src = "data:image/svg+xml," + urllib.parse.quote(body)
    return html.Img(src=src, style={"width": size, "height": size,
                                    "display": "block", "flex": "none"})


def pv_panel(width: int = 130):
    """SVG illustration of a PV module (replica of the mockup diagram)."""
    h = round(width * 86 / 120)
    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" '
        f'width="{width}" height="{h}" viewBox="0 0 120 86">'
        '<rect x="2" y="2" width="116" height="82" rx="4" '
        'fill="#1e3a5f" stroke="#16a34a" stroke-width="2"/>'
        '<g stroke="#3a6ea5" stroke-width="1">'
        '<line x1="40" y1="4" x2="40" y2="82"/>'
        '<line x1="80" y1="4" x2="80" y2="82"/>'
        '<line x1="4" y1="30" x2="116" y2="30"/>'
        '<line x1="4" y1="56" x2="116" y2="56"/></g></svg>'
    )
    src = "data:image/svg+xml," + urllib.parse.quote(svg)
    return html.Img(src=src, style={"width": width, "height": h, "flex": "none"})


def diode_model(width: int = 230):
    """Single-diode model equivalent circuit (Iph ∥ D ∥ Rsh + Rs)."""
    h = round(width * 120 / 240)
    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" '
        f'width="{width}" height="{h}" viewBox="0 0 240 120" fill="none" '
        'stroke="#3b4654" stroke-width="1.6" stroke-linecap="round" '
        'stroke-linejoin="round">'
        # rails (start at the current source — no short before Iph)
        '<path d="M55 95 H205"/><path d="M55 25 H150"/>'
        # current source Iph
        '<line x1="55" y1="25" x2="55" y2="48"/>'
        '<circle cx="55" cy="60" r="12"/>'
        '<line x1="55" y1="67" x2="55" y2="54"/>'
        '<path d="M51 58 L55 53 L59 58"/>'
        '<line x1="55" y1="72" x2="55" y2="95"/>'
        # diode D
        '<line x1="100" y1="25" x2="100" y2="50"/>'
        '<path d="M90 50 H110 L100 64 Z" fill="#3b4654"/>'
        '<line x1="90" y1="64" x2="110" y2="64"/>'
        '<line x1="100" y1="64" x2="100" y2="95"/>'
        # shunt resistor Rsh
        '<line x1="145" y1="25" x2="145" y2="46"/>'
        '<rect x="139" y="46" width="12" height="28" rx="1"/>'
        '<line x1="145" y1="74" x2="145" y2="95"/>'
        # series resistor Rs + output
        '<rect x="150" y="19" width="30" height="12" rx="1"/>'
        '<line x1="180" y1="25" x2="205" y2="25"/>'
        '<circle cx="205" cy="25" r="3.2" fill="#16a34a" stroke="none"/>'
        '<circle cx="205" cy="95" r="3.2" fill="#3b4654" stroke="none"/>'
        # labels
        '<text x="26" y="63" font-size="9" fill="#7c8794" stroke="none">Iph</text>'
        '<text x="105" y="44" font-size="9" fill="#7c8794" stroke="none">D</text>'
        '<text x="155" y="63" font-size="9" fill="#7c8794" stroke="none">Rsh</text>'
        '<text x="158" y="14" font-size="9" fill="#7c8794" stroke="none">Rs</text>'
        '<text x="211" y="22" font-size="11" fill="#16a34a" stroke="none">+</text>'
        '<text x="211" y="101" font-size="11" fill="#7c8794" stroke="none">−</text>'
        '</svg>'
    )
    src = "data:image/svg+xml," + urllib.parse.quote(svg)
    return html.Img(src=src, style={"width": width, "height": h,
                                    "display": "block"})


def _circuit_img(body: str, width: int):
    """Wrap an SVG body (paths/labels) in the shared 240x120 circuit canvas."""
    h = round(width * 120 / 240)
    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" '
        f'width="{width}" height="{h}" viewBox="0 0 240 120" fill="none" '
        'stroke="#3b4654" stroke-width="1.6" stroke-linecap="round" '
        'stroke-linejoin="round">'
        + body +
        '</svg>'
    )
    src = "data:image/svg+xml," + urllib.parse.quote(svg)
    return html.Img(src=src, style={"width": width, "height": h,
                                    "display": "block"})


def simplified_model(width: int = 230):
    """Simplified explicit model — ideal PV current source, no diode network.

    The MPP is computed in closed form from the temperature coefficients; there
    is no diode/Rs/Rsh equation to solve, so the equivalent circuit is just the
    photocurrent source feeding the output terminals.
    """
    body = (
        # rails
        '<path d="M55 95 H205"/><path d="M55 25 H205"/>'
        # current source Iph
        '<line x1="55" y1="25" x2="55" y2="48"/>'
        '<circle cx="55" cy="60" r="12"/>'
        '<line x1="55" y1="67" x2="55" y2="54"/>'
        '<path d="M51 58 L55 53 L59 58"/>'
        '<line x1="55" y1="72" x2="55" y2="95"/>'
        # output terminals
        '<circle cx="205" cy="25" r="3.2" fill="#16a34a" stroke="none"/>'
        '<circle cx="205" cy="95" r="3.2" fill="#3b4654" stroke="none"/>'
        # labels
        '<text x="26" y="63" font-size="9" fill="#7c8794" stroke="none">Iph</text>'
        '<text x="92" y="58" font-size="9" fill="#7c8794" stroke="none">MPP = f(G, T)</text>'
        '<text x="211" y="22" font-size="11" fill="#16a34a" stroke="none">+</text>'
        '<text x="211" y="101" font-size="11" fill="#7c8794" stroke="none">−</text>'
    )
    return _circuit_img(body, width)


def two_diode_model(width: int = 230):
    """Two-diode model equivalent circuit (Iph ∥ D1 ∥ D2 ∥ Rsh + Rs)."""
    body = (
        # rails
        '<path d="M55 95 H205"/><path d="M55 25 H160"/>'
        # current source Iph
        '<line x1="55" y1="25" x2="55" y2="48"/>'
        '<circle cx="55" cy="60" r="12"/>'
        '<line x1="55" y1="67" x2="55" y2="54"/>'
        '<path d="M51 58 L55 53 L59 58"/>'
        '<line x1="55" y1="72" x2="55" y2="95"/>'
        # diode D1
        '<line x1="92" y1="25" x2="92" y2="50"/>'
        '<path d="M82 50 H102 L92 64 Z" fill="#3b4654"/>'
        '<line x1="82" y1="64" x2="102" y2="64"/>'
        '<line x1="92" y1="64" x2="92" y2="95"/>'
        # diode D2
        '<line x1="120" y1="25" x2="120" y2="50"/>'
        '<path d="M110 50 H130 L120 64 Z" fill="#3b4654"/>'
        '<line x1="110" y1="64" x2="130" y2="64"/>'
        '<line x1="120" y1="64" x2="120" y2="95"/>'
        # shunt resistor Rsh
        '<line x1="155" y1="25" x2="155" y2="46"/>'
        '<rect x="149" y="46" width="12" height="28" rx="1"/>'
        '<line x1="155" y1="74" x2="155" y2="95"/>'
        # series resistor Rs + output
        '<rect x="162" y="19" width="26" height="12" rx="1"/>'
        '<line x1="188" y1="25" x2="205" y2="25"/>'
        '<circle cx="205" cy="25" r="3.2" fill="#16a34a" stroke="none"/>'
        '<circle cx="205" cy="95" r="3.2" fill="#3b4654" stroke="none"/>'
        # labels
        '<text x="26" y="63" font-size="9" fill="#7c8794" stroke="none">Iph</text>'
        '<text x="86" y="44" font-size="9" fill="#7c8794" stroke="none">D1</text>'
        '<text x="114" y="44" font-size="9" fill="#7c8794" stroke="none">D2</text>'
        '<text x="164" y="63" font-size="9" fill="#7c8794" stroke="none">Rsh</text>'
        '<text x="166" y="14" font-size="9" fill="#7c8794" stroke="none">Rs</text>'
        '<text x="211" y="22" font-size="11" fill="#16a34a" stroke="none">+</text>'
        '<text x="211" y="101" font-size="11" fill="#7c8794" stroke="none">−</text>'
    )
    return _circuit_img(body, width)


def model_diagram(model_key: str, width: int = 230):
    """Return the equivalent-circuit diagram for the selected electrical model.

    The pvlib reference (De Soto) is a single-diode model, so it shares the
    single-diode circuit.
    """
    if model_key == "simplified":
        return simplified_model(width)
    if model_key == "two_diode":
        return two_diode_model(width)
    # single_diode and pvlib (De Soto) → single-diode circuit
    return diode_model(width)


def dot(color: str = None, pulse: bool = False, size: int = 8):
    """Status dot (CSS circle — not an emoji). pulse uses @keyframes pvpulse."""
    st = {"width": size, "height": size, "borderRadius": "50%",
          "background": color or C["pulse"], "display": "inline-block",
          "flex": "none"}
    if pulse:
        st["animation"] = "pvpulse 2s infinite"
    return html.Span(style=st)


# ── Structure ─────────────────────────────────────────────────────────────────

def card(children, title: str = None, style: dict = None) -> html.Div:
    """White container with a soft border and an optional section title."""
    s = {
        "background":   C["white"],
        "border":       f'1px solid {C["border"]}',
        "borderRadius": 13,
        "padding":      18,
    }
    if style:
        s.update(style)
    inner = []
    if title:
        inner.append(html.Div(
            title,
            style={"fontSize": 10, "fontWeight": 700,
                   "textTransform": "uppercase", "letterSpacing": 1.3,
                   "color": C["textMed"], "marginBottom": 12},
        ))
    inner.extend(children if isinstance(children, list) else [children])
    return html.Div(inner, style=s, className="pv-card")


def section_label(text: str) -> html.Div:
    """Sub-section label inside a card (uppercase, gray)."""
    return html.Div(text, style={
        "fontSize": 10, "fontWeight": 700, "textTransform": "uppercase",
        "letterSpacing": 1, "color": C["label"], "marginBottom": 9,
    })


def stat_box(label: str, value: str, unit: str,
             color: str = None, bg: str = None) -> html.Div:
    """Numeric tile: large (mono) value + unit + label."""
    return html.Div([
        html.Div(value, style={"fontSize": 25, "fontWeight": 700,
                               "color": color or C["text"],
                               "fontFamily": MONO, "lineHeight": 1}),
        html.Div(unit,  style={"fontSize": 10, "color": C["label"],
                               "marginTop": 6}),
        html.Div(label, style={"fontSize": 10, "color": C["label"],
                               "textTransform": "uppercase",
                               "letterSpacing": 0.8, "marginTop": 2}),
    ], style={"textAlign": "center", "padding": "14px 10px",
              "background": bg or C["white"],
              "border":     f'1px solid {C["border"]}',
              "borderRadius": 12})


def divider() -> html.Hr:
    """Soft divider line inside a card."""
    return html.Hr(style={"borderColor": C["borderLight"], "border": "none",
                          "borderTop": f'1px solid {C["borderLight"]}',
                          "margin": "14px 0"})


def page_header(title: str, subtitle: str = "") -> html.Div:
    """Panel header: large title + description."""
    children = [html.H1(title, style={
        "fontSize": 22, "fontWeight": 700, "color": C["text"],
        "margin": "0 0 3px", "letterSpacing": -0.3})]
    if subtitle:
        children.append(html.P(subtitle, style={
            "fontSize": 13, "color": C["dim"], "margin": 0}))
    return html.Div(children, style={"marginBottom": 18})


# ── Status (replaces ✓ ❌ ● 📍) ───────────────────────────────────────────────

def status_badge(text: str, kind: str = "ok"):
    """
    Status pill with icon. kind:
        ok      — green check on a light-green background
        error   — red x on a light-red background
        neutral — gray dot
    """
    if kind == "ok":
        ic, col, bg, bd = "check", C["accentDark"], C["accentLight"], C["accentBorder"]
    elif kind == "error":
        ic, col, bg, bd = "x", C["red"], C["redLight"], "#f3c7c7"
    else:
        ic, col, bg, bd = None, C["dim"], C["borderLight"], C["border"]
    glyph = (icon(ic, color=col, size=12) if ic
             else dot(C["label"], size=8))
    return html.Div([
        glyph,
        html.Span(text, style={"fontSize": 11.5, "fontWeight": 600,
                               "color": col}),
    ], style={"display": "inline-flex", "alignItems": "center", "gap": 6,
              "background": bg, "border": f"1px solid {bd}",
              "borderRadius": 7, "padding": "5px 10px"})
