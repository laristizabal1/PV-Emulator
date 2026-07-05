"""
hmi/i18n.py
===========
Lightweight EN/ES internationalization for the HMI.

Code comments and docstrings stay in English; only the user-facing UI text is
bilingual. The active language lives in a dcc.Store(id="lang") (default "en");
layouts and callbacks resolve their strings with t(key, lang).

Usage:
    from hmi.i18n import t
    t("nav.tab-0", lang)        # "Location & Data" / "Ubicación & Datos"

Missing keys fall back to English, then to the key itself, so the UI never
crashes on a missing translation.
"""

LANGS = ("en", "es")
DEFAULT_LANG = "en"

# key -> {"en": ..., "es": ...}
_T: dict[str, dict[str, str]] = {

    # ── App chrome ────────────────────────────────────────────────────────────
    "app.title":    {"en": "Photovoltaic panel emulator",
                     "es": "Emulador de paneles fotovoltaicos"},
    "app.subtitle": {"en": "PV emulation · NASA POWER · Uniandes DC Microgrid",
                     "es": "Emulación PV · NASA POWER · Microrred DC Uniandes"},
    "sidebar.workflow": {"en": "Workflow", "es": "Flujo de trabajo"},
    "sidebar.source":   {"en": "EA-PS 10060-170 source",
                         "es": "Fuente EA-PS 10060-170"},
    "sidebar.language": {"en": "Language", "es": "Idioma"},

    # ── Navigation labels ─────────────────────────────────────────────────────
    "nav.tab-0": {"en": "Location & Data", "es": "Ubicación & Datos"},
    "nav.tab-1": {"en": "PV Array",        "es": "Arreglo PV"},
    "nav.tab-2": {"en": "Profiles",        "es": "Perfiles"},
    "nav.tab-3": {"en": "SCPI / Control",  "es": "SCPI / Control"},
    "nav.tab-4": {"en": "Summary",         "es": "Resumen"},
    "nav.tab-5": {"en": "Diagnostics",     "es": "Diagnóstico"},

    # ── Panel subtitles ───────────────────────────────────────────────────────
    "sub.tab-0": {"en": "Select a location and download the NASA POWER weather series.",
                  "es": "Selecciona una ubicación y descarga la serie meteorológica de NASA POWER."},
    "sub.tab-1": {"en": "Define the PV module and the series/parallel array configuration.",
                  "es": "Define el módulo fotovoltaico y la configuración serie/paralelo del arreglo."},
    "sub.tab-2": {"en": "Generate the V/I/P setpoint sequence from irradiance and export the SeqLog.",
                  "es": "Genera la secuencia de consignas V/I/P a partir de la irradiancia y exporta el SeqLog."},
    "sub.tab-3": {"en": "Connect the source, control the output and replay the profile onto the device under test.",
                  "es": "Conecta la fuente, controla la salida y reproduce el perfil sobre el dispositivo bajo prueba."},
    "sub.tab-4": {"en": "Session configuration and operating chart of the last test.",
                  "es": "Configuración de la sesión y gráfica de operación del último ensayo."},
    "sub.tab-5": {"en": "Analyze the DUT response: setpoint tracking and power error.",
                  "es": "Analiza la respuesta del DUT: seguimiento de consignas y error de potencia."},

    # ── Tab 0 — Location & Data ───────────────────────────────────────────────
    "loc.card_location":  {"en": "Location", "es": "Ubicación"},
    "loc.card_dates":     {"en": "Date range", "es": "Rango de fechas"},
    "loc.start":          {"en": "Start date (YYYYMMDD)", "es": "Fecha inicio (YYYYMMDD)"},
    "loc.end":            {"en": "End date (YYYYMMDD)", "es": "Fecha fin (YYYYMMDD)"},
    "loc.download":       {"en": "Download NASA POWER data", "es": "Descargar datos NASA POWER"},
    "loc.custom_coords":  {"en": "Custom coordinates", "es": "Coordenadas personalizadas"},
    "loc.lat":            {"en": "Latitude", "es": "Latitud"},
    "loc.lon":            {"en": "Longitude", "es": "Longitud"},
    "loc.irradiance":     {"en": "Irradiance", "es": "Irradiancia"},
    "loc.temp_wind":      {"en": "Temperature & wind", "es": "Temperatura y viento"},
    "loc.no_data":        {"en": "Select a location and download data to see the charts.",
                           "es": "Selecciona una ubicación y descarga datos para ver las gráficas."},

    # ── Tab 1 — PV Array ──────────────────────────────────────────────────────
    "arr.module_array":   {"en": "Module & PV array", "es": "Módulo y arreglo PV"},
    "arr.module":         {"en": "PV module", "es": "Módulo fotovoltaico"},
    "arr.config":         {"en": "Array configuration", "es": "Configuración del arreglo"},
    "arr.ns":             {"en": "Panels in series (Ns)", "es": "Paneles en serie (Ns)"},
    "arr.np":             {"en": "Parallel strings (Np)", "es": "Ramas en paralelo (Np)"},
    "arr.tilt":           {"en": "Tilt", "es": "Inclinación"},
    "arr.model":          {"en": "Electrical model", "es": "Modelo eléctrico"},
    "model.simplified":   {"en": "Simplified", "es": "Simplificado"},
    "model.single_diode": {"en": "Single diode", "es": "1 diodo"},
    "model.two_diode":    {"en": "Two diodes", "es": "2 diodos"},
    "model.pvlib":        {"en": "Reference — PVLIB library",
                           "es": "Referencia - Librería PVLIB"},
    "arr.params":         {"en": "Active module parameters",
                           "es": "Parámetros del módulo activo"},
    "arr.diagram":        {"en": "Array diagram", "es": "Diagrama del arreglo"},
    "arr.limits":         {"en": "Limit check · EA-PS 10060-170",
                           "es": "Verificación de límites · EA-PS 10060-170"},
    "arr.modules_word":   {"en": "modules", "es": "módulos"},
    "arr.array_word":     {"en": "array", "es": "arreglo"},
    "arr.enter_manual":   {"en": "Enter the datasheet parameters manually.",
                           "es": "Ingresa los parámetros del datasheet manualmente."},

    # ── Tab 2 — Profiles ──────────────────────────────────────────────────────
    "prof.strategy":      {"en": "Strategy", "es": "Estrategia"},
    "prof.full":          {"en": "Full", "es": "Completa"},
    "prof.day":           {"en": "Daytime (6–18 h)", "es": "Diurna (6–18 h)"},
    "prof.average":       {"en": "Average", "es": "Promedio"},
    "prof.ms_step":       {"en": "ms/step", "es": "ms/paso"},
    "prof.pts_hour":      {"en": "pts/hour", "es": "pts/hora"},
    "prof.export":        {"en": "Export SeqLog CSV", "es": "Exportar SeqLog CSV"},
    "prof.setpoints":     {"en": "V_set · I_set setpoints", "es": "Consignas V_set · I_set"},
    "prof.power_temp":    {"en": "Power and cell temperature",
                           "es": "Potencia y temperatura de celda"},
    "prof.peak_p":        {"en": "Peak power", "es": "Potencia pico"},
    "prof.peak_v":        {"en": "Peak voltage", "es": "Voltaje pico"},
    "prof.peak_i":        {"en": "Peak current", "es": "Corriente pico"},
    "prof.energy":        {"en": "Energy", "es": "Energía"},
    "prof.lab_dur":       {"en": "Lab duration", "es": "Duración lab"},

    # ── Tab 3 — SCPI / Control ────────────────────────────────────────────────
    "scpi.card_title":    {"en": "SCPI Control — EA-PS Source / DUT",
                           "es": "Control SCPI — Fuente EA-PS / DUT"},
    "scpi.port":          {"en": "COM / USB port", "es": "Puerto COM / USB"},
    "scpi.connect":       {"en": "Connect / verify (*IDN?)", "es": "Conectar / verificar (*IDN?)"},
    "scpi.manual":        {"en": "Manual control", "es": "Control manual"},
    "scpi.voltage":       {"en": "Voltage (V)", "es": "Voltaje (V)"},
    "scpi.current":       {"en": "Current (A)", "es": "Corriente (A)"},
    "scpi.exec":          {"en": "Profile execution", "es": "Ejecución del perfil"},
    "scpi.dut":           {"en": "Device under test (DUT)", "es": "Dispositivo bajo prueba (DUT)"},
    "scpi.dt_step":       {"en": "Δt per step (ms)", "es": "Δt por paso (ms)"},
    "scpi.total_steps":   {"en": "Total steps", "es": "Pasos totales"},
    "scpi.total_dur":     {"en": "Total duration", "es": "Duración total"},
    "scpi.run":           {"en": "RUN PROFILE", "es": "EJECUTAR PERFIL"},
    "scpi.stop":          {"en": "STOP", "es": "DETENER"},
    "scpi.bridge":        {"en": "Modbus TCP bridge", "es": "Bridge Modbus TCP"},
    "scpi.host":          {"en": "Host (listen on)", "es": "Host (escuchar en)"},
    "scpi.tcp_port":      {"en": "TCP port", "es": "Puerto TCP"},
    "scpi.bridge_start":  {"en": "Start Bridge", "es": "Iniciar Bridge"},
    "scpi.bridge_stop":   {"en": "Stop Bridge", "es": "Detener Bridge"},
    "scpi.monitor":       {"en": "Real-time monitor", "es": "Monitor en tiempo real"},
    "scpi.live":          {"en": "LIVE", "es": "EN VIVO"},
    "scpi.setpoints_sent": {"en": "Setpoints sent (set)", "es": "Consignas enviadas (set)"},
    "scpi.live_meas":     {"en": "Live measurements (MEAS)", "es": "Mediciones reales (MEAS)"},
    "scpi.bridge_meas":   {"en": "Modbus bridge measurements", "es": "Mediciones Bridge Modbus"},
    "scpi.commands":      {"en": "Commands sent", "es": "Comandos enviados"},
    # SCPI dynamic status
    "scpi.port_first":    {"en": "Select a COM port first.", "es": "Selecciona un puerto COM primero."},
    "scpi.connected":     {"en": "Connected", "es": "Conectado"},
    "scpi.remote_active": {"en": "remote active", "es": "remoto activo"},
    "scpi.bridge_stopped": {"en": "Bridge stopped.", "es": "Bridge detenido."},
    "scpi.bridge_running": {"en": "Bridge already running.", "es": "Bridge ya en ejecución."},
    "scpi.connect_first": {"en": "Connect the EA source first (Connect/verify button).",
                           "es": "Conecta primero la fuente EA (botón Conectar/verificar)."},
    "scpi.bridge_active": {"en": "Bridge active", "es": "Bridge activo"},
    "scpi.bridge_start_err": {"en": "Error starting bridge", "es": "Error al iniciar bridge"},
    "scpi.bridge_inactive": {"en": "Bridge inactive.", "es": "Bridge inactivo."},
    "scpi.busy_profile":  {"en": "Stop the running profile before starting the bridge.",
                           "es": "Detén el perfil en ejecución antes de iniciar el bridge."},
    "scpi.busy_bridge":   {"en": "Stop the bridge before running a profile.",
                           "es": "Detén el bridge antes de ejecutar un perfil."},
    "scpi.exec_running":  {"en": "A profile is already running.",
                           "es": "Ya hay un perfil en ejecución."},
    "scpi.out_on":        {"en": "Output ON", "es": "Salida ON"},
    "scpi.out_off":       {"en": "Output OFF", "es": "Salida OFF"},
    "scpi.profile_err":   {"en": "Profile error", "es": "Error en perfil"},
    "scpi.no_profile":    {"en": "No profile running.", "es": "Sin perfil en ejecución."},
    "scpi.running_step":  {"en": "Running — step", "es": "Ejecutando — paso"},
    "scpi.of":            {"en": "of", "es": "de"},
    "scpi.monitor_idle":  {"en": "Monitor inactive — starts when the profile runs",
                           "es": "Monitor inactivo — arrancará al ejecutar el perfil"},

    # ── Tab 4 — Summary ───────────────────────────────────────────────────────
    "sum.config":         {"en": "Test configuration", "es": "Configuración del ensayo"},
    "sum.operation":      {"en": "Operation · setpoint vs measured",
                           "es": "Operación · consigna vs medido"},
    "sum.location":       {"en": "Location", "es": "Ubicación"},
    "sum.array_pv":       {"en": "PV array", "es": "Arreglo PV"},
    "sum.emulation":      {"en": "Emulation", "es": "Emulación"},
    "sum.dut":            {"en": "DUT", "es": "DUT"},
    "sum.source":         {"en": "Source", "es": "Fuente"},
    "sum.modules":        {"en": "Modules", "es": "Módulos"},
    "sum.strategy":       {"en": "Strategy", "es": "Estrategia"},
    "sum.steps":          {"en": "steps", "es": "pasos"},
    "sum.mppt":           {"en": "MPPT", "es": "MPPT"},
    "sum.envelope":       {"en": "Envelope", "es": "Envolvente"},
    "sum.no_data":        {"en": "No data. Complete the previous steps.",
                           "es": "Sin datos. Completa los pasos anteriores."},

    # ── Tab 5 — Diagnostics ───────────────────────────────────────────────────
    "diag.title":         {"en": "DUT diagnostics", "es": "Diagnóstico del DUT"},
    "diag.intro":         {"en": "Select a measurement session to diagnose the device "
                                 "under test. \"Live\" uses the last run of the "
                                 "\"SCPI / Control\" tab (still in memory).",
                           "es": "Selecciona una sesión de medición para diagnosticar el "
                                 "dispositivo bajo prueba. «En vivo» usa la última ejecución "
                                 "de la pestaña «SCPI / Control» (aún en memoria)."},
    "diag.session":       {"en": "Session", "es": "Sesión"},
    "diag.refresh":       {"en": "Refresh", "es": "Actualizar"},
    "diag.live":          {"en": "Live (last run)", "es": "En vivo (última ejecución)"},
    "diag.no_meas":       {"en": "No measurements for this session. Run a profile in the "
                                 "\"SCPI / Control\" tab (with the source connected) and "
                                 "come back.",
                           "es": "Sin mediciones para esta sesión. Ejecuta un perfil en la "
                                 "pestaña «SCPI / Control» (con la fuente conectada) y vuelve."},
    "diag.no_data":       {"en": "No data — run a profile in \"SCPI / Control\"",
                           "es": "Sin datos — ejecuta un perfil en «SCPI / Control»"},
    "diag.no_setpoints":  {"en": "The session has no setpoints (P_set) to compare",
                           "es": "La sesión no contiene consignas (P_set) para comparar"},
    "diag.samples":       {"en": "samples", "es": "muestras"},

    # ── Diagnostic / analysis figures (post_exec_plots) ───────────────────────
    "fig.pvi_title":      {"en": "Setpoint vs DC Measurement", "es": "Consigna vs Medición DC"},
    "fig.a_power":        {"en": "(a) Power", "es": "(a) Potencia"},
    "fig.b_voltage":      {"en": "(b) Voltage", "es": "(b) Voltaje"},
    "fig.c_current":      {"en": "(c) Current", "es": "(c) Corriente"},
    "fig.setpoint":       {"en": "setpoint", "es": "consigna"},
    "fig.dc_meas":        {"en": "DC measurement", "es": "medición DC"},
    "fig.hour":           {"en": "Hour of day [h]", "es": "Hora del día [h]"},
    "fig.step":           {"en": "Step", "es": "Paso"},
    "fig.mppt_eff":       {"en": "MPPT efficiency", "es": "Eficiencia MPPT"},
    "fig.power_fidelity": {"en": "Power fidelity", "es": "Fidelidad de potencia"},
    "fig.tracking":       {"en": "Tracking", "es": "Seguimiento"},
    "fig.mean":           {"en": "mean", "es": "media"},
    "fig.ref100":         {"en": "100 % ref.", "es": "Ref. 100 %"},
    "fig.power_err":      {"en": "Absolute power error", "es": "Error absoluto de potencia"},

    # ── Profile chart traces / metrics (profile_cb) ──────────────────────────
    "prof.tcell":         {"en": "T_cell (°C)", "es": "T_cell (°C)"},
}


def t(key: str, lang: str = DEFAULT_LANG) -> str:
    """Resolve a UI string for the given language (falls back to EN, then key)."""
    entry = _T.get(key)
    if not entry:
        return key
    return entry.get(lang) or entry.get("en") or key
