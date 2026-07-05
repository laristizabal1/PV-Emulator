"""
hmi/
====
HMI graphical interface built with Dash (Plotly).

    layout/    — functions that return the layout of each tab
    callbacks/ — Dash callbacks grouped by functional domain

Each callbacks module exposes register(app) which must be called from app.py
after creating the dash.Dash instance:

    from hmi.callbacks import nasa_cb, array_cb, profile_cb, scpi_cb, summary_cb
    nasa_cb.register(app)
    array_cb.register(app)
    profile_cb.register(app)
    scpi_cb.register(app)
    summary_cb.register(app)
"""
