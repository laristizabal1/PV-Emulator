"""
hmi/layout/
===========
Functions that build the layout of each HMI tab.
They contain no logic or callbacks — only Dash structure.

    components      — card(), stat_box(), divider(), badge(), slider_row()
    tab_location    — Tab 0: city selection + NASA POWER download
    tab_array       — Tab 1: module parameters, electrical model
    tab_profiles    — Tab 2: V/I/P setpoints, strategy, export CSV
    tab_scpi        — Tab 3: real-time SCPI control
    tab_summary     — Tab 4: final metrics and full configuration
    tab_diagnostics — Tab 5: post-execution device diagnostics
"""
from hmi.layout.tab_location    import tab_location
from hmi.layout.tab_array       import tab_array
from hmi.layout.tab_profiles    import tab_profiles
from hmi.layout.tab_scpi        import tab_scpi
from hmi.layout.tab_summary     import tab_summary
from hmi.layout.tab_diagnostics import tab_diagnostics

TAB_RENDERERS: dict = {
    "tab-0": tab_location,
    "tab-1": tab_array,
    "tab-2": tab_profiles,
    "tab-3": tab_scpi,
    "tab-4": tab_summary,
    "tab-5": tab_diagnostics,
}

__all__ = [
    "tab_location", "tab_array", "tab_profiles",
    "tab_scpi", "tab_summary", "tab_diagnostics", "TAB_RENDERERS",
]
