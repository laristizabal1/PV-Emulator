"""
hmi/layout/figtheme.py
======================
Shared Plotly theme so that ALL HMI charts share the clean look of the approved
redesign (transparent background over a white card, thin grid, DM Sans
typography, discreet axes, no modebar).

Usage:
    from hmi.layout.figtheme import style_fig, GRAPH_CONFIG
    fig = go.Figure(...)
    style_fig(fig, height=190)
    dcc.Graph(figure=fig, config=GRAPH_CONFIG)
"""

from config.hardware import C, FONT

_GRID  = C["grid"]        # #eef1f5
_TICK  = C["label"]       # #9aa4b1
_AXLBL = C["textMed"]     # #3b4654

# No modebar; accidental zoom/scroll is annoying on monitoring panels.
GRAPH_CONFIG = {"displayModeBar": False, "responsive": True}


def style_fig(fig, height: int = None, legend: bool = True,
              margin: dict = None):
    """Apply the common visual theme to a Plotly figure (in place) and return it."""
    fig.update_layout(
        font=dict(family=FONT, size=11, color=_AXLBL),
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        margin=margin or dict(l=44, r=18, t=24, b=28),
        hovermode="x unified",
    )
    if height:
        fig.update_layout(height=height)
    if legend:
        fig.update_layout(legend=dict(
            orientation="h", x=0, y=1.14, xanchor="left",
            font=dict(size=10), bgcolor="rgba(0,0,0,0)"))
    else:
        fig.update_layout(showlegend=False)
    fig.update_xaxes(gridcolor=_GRID, zerolinecolor=_GRID, linecolor=_GRID,
                     tickfont=dict(size=9, color=_TICK), title=None)
    fig.update_yaxes(gridcolor=_GRID, zerolinecolor=_GRID, linecolor=_GRID,
                     tickfont=dict(size=9, color=_TICK))
    return fig
