"""
experiments/run_experiment.py
==============================
Orquestador CLI para ensayos repetidos del emulador contra un DUT, generando
los datos crudos para el paper.

ENCUADRE: el paper es sobre el EMULADOR (universal, válido para cualquier carga),
NO sobre el inversor. La validación universal del emulador es la fidelidad de la
curva I-V (independiente del DUT — ver tools/probe/curve_trace_manual.py). Este
script aporta los casos de validación experimental: el seguimiento de un DUT real.
Se elige el DUT con --dut (o el menú interactivo); los dos casos típicos son:

    --dut mppt_inverter   inversor MPPT  → envolvente cp, métrica η = P_dc/P_set
    --dut eload           carga DL3000   → envolvente curve (la EA presenta la
                          curva; el barrido R lo hace la carga, ver la guía y
                          tools/probe/curve_trace_manual.py)

MODELOS INTERCALADOS: el experimento corre los 3 modelos eléctricos manuales
(1 diodo, 2 diodos, simplificado) INTERCALADOS — en cada repetición se ejecuta
el perfil de cada modelo uno tras otro, de modo que los tres ven condiciones
(deriva térmica, estado del bus serie) emparejadas, no en bloques separados.
Cada modelo escribe su propio experiments/results/<ts>/<ts>_<key>.json — el
formato que consume experiments/paper_figs.py.

Para el inversor MPPT la envolvente recomendada es `cp` (VOLT=Voc / CURR=Isc /
POW=Pmp, lazo CP nativo de la PS 10000), validada a ~1 % de error de potencia.

Uso básico (inversor MPPT, 3 modelos intercalados, envolvente cp):
    python experiments/run_experiment.py

Uso personalizado:
    python experiments/run_experiment.py --reps 5 --port COM3
    python experiments/run_experiment.py --dut eload          # carga DL3000
    python experiments/run_experiment.py --models single_diode,two_diode
    python experiments/run_experiment.py --models all          # + pvlib referencia
    python experiments/run_experiment.py --dry-run             # sin conectar fuente

Parámetros sobreescribibles:
    --reps          Repeticiones (default: 5)
    --dut           DUT de config/devices.py (def: menú interactivo / mppt_inverter)
    --models        Modelos a intercalar: lista por comas, 'default' (los 3
                    manuales) o 'all' (+ pvlib). Default: los 3 manuales.
    --envelope      Envolvente de run_profile: direct|cp|curve
                    (default: la recomendada por el DUT)
    --port          Puerto serie (default: PV_SERIAL_PORT o autodetect)
    --dt-ms         ms por paso (default: 7000, igual que el experimento original)
    --city          Nombre de ciudad (solo para metadatos)
    --nasa-start    Fecha inicio rango NASA YYYYMMDD (default: 20240315)
    --nasa-end      Fecha fin rango NASA   YYYYMMDD (default: 20250317)
    --strategy      Estrategia de perfil: average|day|full (default: average)
    --out-dir       Directorio de salida (default: experiments/results)
    --dry-run       Genera el perfil y muestra el plan, sin conectar la fuente
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Callable

# ── Bootstrap de sys.path ─────────────────────────────────────────────────────
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

# ── Imports internos ──────────────────────────────────────────────────────────
from config.hardware import DEFAULT_PORT, DEFAULT_BAUD, V_MAX, I_MAX
from config.devices import get_device, DEFAULT_DEVICE_KEY, CATALOG
from models.base import ModuleParams
from models.simplified   import SimplifiedModel
from models.single_diode import SingleDiodeModel
from models.two_diode    import TwoDiodeModel
from pipeline.nasa_power import fetch as nasa_fetch, DEFAULT_CACHE_DIR
from pipeline.profile import build as build_profile, apply_strategy, _hourly_curve, densify
from comm.scpi import SCPIController, autodetect_port
from comm.monitor import EAMonitor


# ─────────────────────────────────────────────────────────────────────────────
# CONFIGURACIÓN FIJA DEL EXPERIMENTO ORIGINAL
# Módulo custom usado en Cali, abril 2026 (fuente: datasheet del panel físico)
# ─────────────────────────────────────────────────────────────────────────────
ORIGINAL_MODULE = ModuleParams(
    Isc_n = 5.2,    # A  — corriente de cortocircuito STC
    Voc_n = 24.3,   # V  — voltaje de circuito abierto STC
    Imp_n = 4.9,    # A  — corriente en MPP STC
    Vmp_n = 20.4,   # V  — voltaje en MPP STC
    KI    = 0.0026, # A/°C — coef. temperatura corriente (alphaIsc)
    KV    = -0.068, # V/°C — coef. temperatura voltaje  (betaVoc)
    Ns    = 36,     # celdas en serie
    noct  = 47.0,   # °C — temperatura nominal operación
)

ORIGINAL_CITY    = "Cali"
ORIGINAL_LAT     = 3.45
ORIGINAL_LON     = -76.53
ORIGINAL_NASA_START = "20240315"
ORIGINAL_NASA_END   = "20250317"
ORIGINAL_STRATEGY   = "average"   # día promedio estadístico
ORIGINAL_NS      = 1
ORIGINAL_NP      = 1
ORIGINAL_TILT    = 10.0
ORIGINAL_DT_MS   = 7000

# Modelos eléctricos disponibles para el experimento (los 3 manuales).
# La clave alimenta el nombre {ts}_<key>.json que consume paper_figs.py.
MODELS: dict[str, tuple[str, type]] = {
    "single_diode": ("1 diodo",      SingleDiodeModel),
    "two_diode":    ("2 diodos",     TwoDiodeModel),
    "simplified":   ("Simplificado", SimplifiedModel),
}
DEFAULT_MODELS = ["single_diode", "two_diode", "simplified"]


def resolve_models(arg: str | None) -> list[str]:
    """Lista de claves de modelo a intercalar desde el argumento --models."""
    if not arg or arg.strip().lower() == "default":
        return list(DEFAULT_MODELS)
    if arg.strip().lower() == "all":
        return list(MODELS)
    keys = [k.strip() for k in arg.split(",") if k.strip()]
    unknown = [k for k in keys if k not in MODELS]
    if unknown:
        raise SystemExit(
            f"Modelos desconocidos: {unknown}. Válidos: {list(MODELS)} | 'default' | 'all'")
    return keys


# ─────────────────────────────────────────────────────────────────────────────
# MÉTRICAS POR RUN
# ─────────────────────────────────────────────────────────────────────────────

def _compute_metrics(buffer: list[dict]) -> dict:
    """Extrae MAE, RMSE y η_MPPT medio de un buffer de EAMonitor."""
    import numpy as np

    rows = [r for r in buffer if r.get("P_set") is not None and r.get("P_set", 0) > 0.5]
    if not rows:
        return {"mae_w": None, "rmse_w": None, "eta_mean_pct": None, "n_rows": 0}

    p_set  = [r["P_set"] for r in rows]
    p_meas = [r["P_dc"]  for r in rows]
    errors = [abs(s - m) for s, m in zip(p_set, p_meas)]
    eta    = [m / s * 100 for s, m in zip(p_set, p_meas) if s > 0]

    return {
        "mae_w":       round(float(np.mean(errors)), 3),
        "rmse_w":      round(float(np.sqrt(np.mean([e**2 for e in errors]))), 3),
        "eta_mean_pct":round(float(np.mean(eta)), 2) if eta else None,
        "n_rows":      len(rows),
    }


# ─────────────────────────────────────────────────────────────────────────────
# CORE: EJECUTAR UNA REPETICIÓN
# ─────────────────────────────────────────────────────────────────────────────

def run_one(
    profile:    list[dict],
    rep_idx:    int,
    controller: SCPIController,
    monitor:    EAMonitor,
    dt_ms:      int,
    envelope:   str,
    meta:       dict,
    log:        Callable[[str], None] = print,
    cp_curr_ceiling: float | None = None,
    curve_drive: str = "voltage",
) -> dict:
    """
    Ejecuta una repetición del perfil sobre la fuente y retorna el resultado.

    El monitor etiqueta cada muestra con la consigna del paso vigente mediante
    progress_cb -> monitor.set_step(step) (el formato que lee paper_figs.py:
    P_set / hora_emulada por muestra).

    Retorna dict con:
        buffer    — lista de mediciones del EAMonitor
        metrics   — MAE, RMSE, η calculados
        meta      — metadatos de la repetición
        error     — mensaje de error si falló, None si OK
    """
    run_meta = {
        **meta,
        "repeticion":  rep_idx,
        "dt_ms":       dt_ms,
        "envolvente":  envelope,
        "n_pasos":     len(profile),
        "perfil_consignas": profile,
    }

    log(f"  → Iniciando monitor...")
    monitor.start(meta=run_meta)
    time.sleep(0.7)                       # deja que corra el primer ciclo de polling

    try:
        log(f"  → Ejecutando perfil ({len(profile)} pasos × {dt_ms} ms, "
            f"envolvente={envelope})...")
        controller.run_profile(
            profile, dt_ms=dt_ms, envelope=envelope,
            progress_cb=lambda i, total, step: monitor.set_step(step),
            cp_curr_ceiling=cp_curr_ceiling, curve_drive=curve_drive,
        )
    except Exception as exc:
        monitor.set_step(None)
        monitor.stop()
        return {"buffer": [], "metrics": {}, "meta": run_meta, "error": str(exc)}

    monitor.set_step(None)
    monitor.stop()
    buffer = monitor.get_buffer()
    metrics = _compute_metrics(buffer)

    log(f"  ✓ Completado — {len(buffer)} muestras | "
        f"MAE={metrics.get('mae_w')} W | "
        f"RMSE={metrics.get('rmse_w')} W | "
        f"η̄={metrics.get('eta_mean_pct')} %")

    return {"buffer": buffer, "metrics": metrics, "meta": run_meta, "error": None}


# ─────────────────────────────────────────────────────────────────────────────
# ANÁLISIS ESTADÍSTICO SOBRE N REPETICIONES
# ─────────────────────────────────────────────────────────────────────────────

def aggregate_runs(runs: list[dict]) -> dict:
    """
    Promedia métricas escalares y buffer punto a punto sobre N repeticiones.

    Para cada hora emulada calcula media y desviación estándar de P_dc, V_dc,
    I_dc y η_MPPT. Los resultados se usan para graficar con bandas de error.
    """
    import numpy as np

    ok = [r for r in runs if not r["error"]]
    if not ok:
        return {"status": "all_failed", "n_ok": 0}

    # ── Métricas escalares ────────────────────────────────────────────────────
    def _stat(key):
        vals = [r["metrics"][key] for r in ok if r["metrics"].get(key) is not None]
        if not vals:
            return None, None
        return round(float(np.mean(vals)), 3), round(float(np.std(vals)), 3)

    mae_mean,  mae_std  = _stat("mae_w")
    rmse_mean, rmse_std = _stat("rmse_w")
    eta_mean,  eta_std  = _stat("eta_mean_pct")

    # ── Buffer punto a punto por hora emulada ─────────────────────────────────
    # Agrupa todas las muestras de todas las repeticiones por hora_emulada,
    # luego calcula media y std.
    from collections import defaultdict
    by_hour: dict[str, list] = defaultdict(list)
    for r in ok:
        for row in r["buffer"]:
            h = row.get("hora_emulada")
            if h:
                by_hour[h].append(row)

    hourly = {}
    for h, rows in sorted(by_hour.items()):
        p = [r["P_dc"] for r in rows if r.get("P_dc") is not None]
        v = [r["V_dc"] for r in rows if r.get("V_dc") is not None]
        i = [r["I_dc"] for r in rows if r.get("I_dc") is not None]
        ps = [r["P_set"] for r in rows if r.get("P_set") is not None]
        hourly[h] = {
            "P_dc_mean":  round(float(np.mean(p)),  3) if p  else None,
            "P_dc_std":   round(float(np.std(p)),   3) if p  else None,
            "V_dc_mean":  round(float(np.mean(v)),  3) if v  else None,
            "V_dc_std":   round(float(np.std(v)),   3) if v  else None,
            "I_dc_mean":  round(float(np.mean(i)),  3) if i  else None,
            "I_dc_std":   round(float(np.std(i)),   3) if i  else None,
            "P_set_mean": round(float(np.mean(ps)), 1) if ps else None,
            "n_samples":  len(rows),
        }
        if hourly[h]["P_set_mean"] and hourly[h]["P_set_mean"] > 0 and hourly[h]["P_dc_mean"] is not None:
            eta = hourly[h]["P_dc_mean"] / hourly[h]["P_set_mean"] * 100
            hourly[h]["eta_pct"] = round(eta, 2)

    return {
        "status":        "ok",
        "n_ok":          len(ok),
        "n_failed":      len(runs) - len(ok),
        "mae_mean_w":    mae_mean,
        "mae_std_w":     mae_std,
        "rmse_mean_w":   rmse_mean,
        "rmse_std_w":    rmse_std,
        "eta_mean_pct":  eta_mean,
        "eta_std_pct":   eta_std,
        "hourly":        hourly,
    }


# ─────────────────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────────────────

def _select_dut_interactive() -> str:
    """
    Menú numerado para elegir el DUT cuando no se pasa --dut y hay terminal.
    Devuelve la clave del DUT en CATALOG (config/devices.py).
    """
    items = list(CATALOG.items())   # orden: inverter, eload, generic
    print("\nSelecciona el dispositivo bajo prueba (DUT):")
    for i, (k, d) in enumerate(items, 1):
        mppt = "MPPT" if d.has_mppt else "sin MPPT"
        print(f"  {i}) {d.label:<28} [{k}]  envolvente={d.envelope}, {mppt}")
    default_i = next((i for i, (k, _) in enumerate(items, 1)
                      if k == DEFAULT_DEVICE_KEY), 1)
    while True:
        sel = input(f"Opción [{default_i}]: ").strip() or str(default_i)
        if sel.isdigit() and 1 <= int(sel) <= len(items):
            return items[int(sel) - 1][0]
        if sel in CATALOG:
            return sel
        print(f"  Opción inválida. Elige 1-{len(items)} o una clave de {list(CATALOG)}.")


def _resolve_strategy(explicit: str | None, envelope: str, log=print) -> str:
    """
    Estrategia de perfil. Default 'average' (día promedio, ~15 pasos) para TODAS
    las envolventes: para cp/curve la curva I(V) se re-adjunta luego a los pasos
    promediados (ver main), así 'average' sirve también al lazo curve sin inflar
    el perfil a miles de pasos ('day'/'full' conservan toda la serie horaria).
    """
    return explicit or "average"


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Ensayo repetido del emulador contra un DUT — EA-PS 10060-170",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    p.add_argument("--reps", type=int, default=5,
                   help="Repeticiones del ensayo")
    p.add_argument("--dut", default=None, choices=list(CATALOG),
                   help="DUT de config/devices.py (si se omite: menú interactivo "
                        "en terminal, o '%s' si no hay TTY)" % DEFAULT_DEVICE_KEY)
    p.add_argument("--models", default=None,
                   help="Modelos a intercalar: lista por comas (%s), "
                        "'default' (los 3 manuales) o 'all' (+ pvlib)."
                        % ",".join(MODELS))
    p.add_argument("--envelope", default=None,
                   choices=["direct", "cp", "curve"],
                   help="Envolvente de run_profile (def. la recomendada por el DUT)")
    p.add_argument("--curve-drive", default="voltage",
                   choices=["voltage", "current"],
                   help="Sentido del lazo curve. 'voltage'=device-agnostic (fuente "
                        "de corriente); 'current'=lazo espejo (fuente de VOLTAJE "
                        "regida por la curva) — necesario para que un MPPT inverter "
                        "encuentre el Vmp real (con 'voltage' o 'cp' se queda en 13 V).")
    p.add_argument("--port", default=None,
                   help="Puerto serie (def. PV_SERIAL_PORT o autodetect)")
    p.add_argument("--dt-ms", type=int, default=ORIGINAL_DT_MS,
                   help="ms por paso del perfil")
    p.add_argument("--points-per-hour", type=int, default=1,
                   help="Sub-puntos por hora para SUAVIZAR el perfil (1=escalera "
                        "horaria; 6=cada 10 min). Interpola G/T y recalcula el MPP "
                        "por sub-paso → el MPPT sigue un MPP lento (menos "
                        "transitorio). OJO: multiplica los pasos — baja --dt-ms "
                        "para no alargar el ensayo ×N.")
    p.add_argument("--city", default=ORIGINAL_CITY,
                   help="Nombre de ciudad (solo para metadatos)")
    p.add_argument("--lat", type=float, default=ORIGINAL_LAT,
                   help="Latitud del sitio (def: Cali)")
    p.add_argument("--lon", type=float, default=ORIGINAL_LON,
                   help="Longitud del sitio (def: Cali)")
    p.add_argument("--nasa-start", default=ORIGINAL_NASA_START,
                   help="Inicio rango NASA YYYYMMDD")
    p.add_argument("--nasa-end",   default=ORIGINAL_NASA_END,
                   help="Fin rango NASA YYYYMMDD")
    p.add_argument("--cp-curr-ceiling", default="auto",
                   help="Techo CURR para cp: 'auto' (Isc pico del día, evita el "
                        "colapso de hombros a poca luz), 'off' (Isc por hora), o A.")
    p.add_argument("--strategy", default=None,
                   choices=["average", "day", "full"],
                   help="Estrategia de perfil (def: 'average' para cp/direct, "
                        "'day' para curve — average descarta la curva I(V))")
    p.add_argument("--thermal", default="faiman", choices=["faiman", "noct"],
                   help="Modelo de temperatura de celda (def: faiman, usa WS2M)")
    p.add_argument("--out-dir", type=Path,
                   default=_ROOT / "experiments" / "results",
                   help="Directorio de salida")
    p.add_argument("--dry-run", action="store_true",
                   help="Solo genera el perfil y muestra el plan, sin conectar")
    return p.parse_args()


def main() -> int:
    args = parse_args()

    # La consola de Windows usa cp1252 y no codifica los símbolos Unicode del log
    # (→, ±, η, ✓). Forzar UTF-8 en stdout (el log a archivo ya usa utf-8).
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    # Resolver DUT: --dut explícito, menú interactivo si hay terminal, o default.
    dut_key = args.dut
    if dut_key is None:
        dut_key = _select_dut_interactive() if sys.stdin.isatty() else DEFAULT_DEVICE_KEY
    device   = get_device(dut_key)
    envelope = args.envelope or device.envelope
    models_sel = resolve_models(args.models)

    ts_run = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir: Path = args.out_dir / ts_run
    out_dir.mkdir(parents=True, exist_ok=True)
    log_path = out_dir / f"run_{ts_run}.log"

    def log(msg: str):
        print(msg)
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(msg + "\n")

    strategy = _resolve_strategy(args.strategy, envelope, log)

    log("=" * 60)
    log(f"PV Experiment Runner — {ts_run}")
    log(f"DUT:          {device.label}  (MPPT={'sí' if device.has_mppt else 'no'})")
    log(f"Envolvente:   {envelope}"
        + ("" if args.envelope else f"  (recomendada para {device.key})"))
    log(f"Modelos:      {', '.join(MODELS[k][0] for k in models_sel)}  (intercalados)")
    log(f"Repeticiones: {args.reps}")
    smooth = (f"escalera horaria" if args.points_per_hour <= 1
              else f"suavizado {args.points_per_hour} pts/hora")
    log(f"Estrategia:   {strategy} | dt={args.dt_ms} ms | térmico={args.thermal} | {smooth}")
    log(f"NASA:         {args.nasa_start} → {args.nasa_end}")
    log("=" * 60)

    # ── Descargar datos NASA POWER ────────────────────────────────────────────
    log("\n[1/4] Descargando datos NASA POWER...")
    nasa_data = nasa_fetch(
        lat   = args.lat,
        lon   = args.lon,
        start = args.nasa_start,
        end   = args.nasa_end,
        cache_dir = DEFAULT_CACHE_DIR,
    )
    log(f"      {len(nasa_data)} registros horarios descargados/cargados de caché")

    # ── Construir UN perfil por modelo (con curva I-V para envolventes cp/curve) ─
    log("\n[2/4] Generando perfiles de consignas (uno por modelo)...")

    def _build_profile(model) -> list[dict]:
        full = build_profile(nasa_data, model,
                             Ns_arr=ORIGINAL_NS, Np_arr=ORIGINAL_NP,
                             tilt=ORIGINAL_TILT, V_max=V_MAX, I_max=I_MAX,
                             attach_curve=True, thermal_model=args.thermal)
        prof = apply_strategy(full, strategy=strategy)
        want_curve = envelope in ("cp", "curve")

        # Opción (A): suavizar la escalera horaria interpolando G/T y recalculando
        # el MPP (y la curva I(V) para cp/curve) por sub-paso. densify re-adjunta
        # curve_v/curve_i, así que sustituye al re-attach de abajo cuando aplica.
        if args.points_per_hour and args.points_per_hour > 1:
            return densify(prof, model, args.points_per_hour,
                           Ns_arr=ORIGINAL_NS, Np_arr=ORIGINAL_NP,
                           V_max=V_MAX, I_max=I_MAX, attach_curve=want_curve,
                           thermal_model=args.thermal)

        # apply_strategy('average') reconstruye los pasos y descarta curve_v/curve_i
        # (conserva Voc/Isc). cp solo necesita Voc/Isc, pero el lazo 'curve' necesita
        # la tabla I(V): se re-adjunta al día promedio usando el modelo en el (Gpoa,
        # Tcell) promedio de cada paso. Mantiene el perfil compacto (~15 pasos).
        if want_curve:
            for s in prof:
                if s.get("P_set", 0) > 0 and s.get("Gpoa") is not None and s.get("Tcell") is not None:
                    curve = _hourly_curve(model, s["Gpoa"], s["Tcell"],
                                          ORIGINAL_NS, ORIGINAL_NP, 40, V_MAX, I_MAX)
                    if curve:
                        s.update(curve)
        return prof

    profiles: dict[str, list[dict]] = {}
    for key in models_sel:
        label, Cls = MODELS[key]
        m = Cls(ORIGINAL_MODULE)
        m.fit()
        prof = _build_profile(m)
        if not prof or all(s["P_set"] <= 0 for s in prof):
            log(f"ERROR: el perfil de '{label}' quedó vacío o sin potencia "
                "(revisa rango NASA, ciudad o estrategia). Aborta.")
            return 1
        profiles[key] = prof

        p_vals = [s["P_set"] for s in prof]
        active = [s for s in prof if s["P_set"] > 0]
        log(f"  {label:<28} {len(prof)} pasos ({len(active)} con potencia) | "
            f"P_set max={max(p_vals):.1f} W min={min(p_vals):.1f} W")

        # Diagnóstico fiel a la envolvente: cp necesita Voc/Isc por paso activo;
        # curve necesita además curve_v/curve_i.
        if envelope in ("cp", "curve"):
            with_env   = sum(1 for s in active if s.get("Voc") and s.get("Isc"))
            with_curve = sum(1 for s in active if s.get("curve_v"))
            log(f"    envolvente {envelope}: {with_env}/{len(active)} con Voc/Isc"
                + (f" · {with_curve}/{len(active)} con curva I(V)" if envelope == "curve" else ""))
            if with_env < len(active):
                log("    AVISO: faltan Voc/Isc en pasos activos → run_profile degradaría a 'direct'.")
            if envelope == "curve" and with_curve < len(active):
                log("    AVISO: faltan curve_v/curve_i en pasos activos para el lazo I(V).")

    # Techo CURR para cp por modelo: 'auto'=Isc pico del día (evita el colapso de
    # hombros a poca luz, ver comm/scpi.run_profile), 'off'=Isc por hora, o un valor.
    ceilings: dict = {}
    cc = str(args.cp_curr_ceiling).strip().lower()
    for key in models_sel:
        if envelope != "cp" or cc == "off":
            ceilings[key] = None
        elif cc == "imp":                     # techo = Imp/hora → fija el MPPT en Vmp
            ceilings[key] = "imp"
        elif cc == "auto":
            ceilings[key] = round(max((s["Isc"] for s in profiles[key]
                                       if s.get("Isc")), default=I_MAX), 3)
        else:
            try:
                ceilings[key] = float(cc)
            except ValueError:
                ceilings[key] = None
    if envelope == "cp" and cc != "off":
        log("  Techo CURR cp (%s): %s" % (cc,
            ", ".join(f"{MODELS[k][0]}={ceilings[k]}" for k in models_sel)))

    if args.dry_run:
        log("\n[dry-run] Plan de experimento (modelos INTERCALADOS):")
        steps_per_rep = sum(len(profiles[k]) for k in models_sel)
        total_steps   = steps_per_rep * args.reps
        total_time_min = total_steps * args.dt_ms / 1000 / 60
        log(f"  {len(models_sel)} modelos × {args.reps} reps "
            f"= {len(models_sel) * args.reps} ejecuciones de perfil")
        log(f"  {total_steps} pasos totales · ~{total_time_min:.0f} min  "
            f"(DUT={device.label}, envolvente={envelope})")
        log("  Orden: rep1[" + ", ".join(models_sel) + "], rep2[…], …")
        log("  (no se conectó la fuente — usa sin --dry-run para ejecutar)")
        return 0

    # ── Conectar a la fuente ──────────────────────────────────────────────────
    log("\n[3/4] Conectando a la fuente EA-PS 10060-170...")
    port = args.port or os.getenv("PV_SERIAL_PORT") or DEFAULT_PORT
    resolved = autodetect_port(port)
    if resolved is None:
        log(f"ERROR: No se detectó ningún puerto serie. "
            f"Conecta la fuente o define PV_SERIAL_PORT.")
        return 1
    if resolved != port:
        log(f"  Puerto '{port}' no disponible — usando '{resolved}'")
        port = resolved

    controller = SCPIController(port=port, baud=DEFAULT_BAUD)
    try:
        idn = controller.connect()
        log(f"  Conectado: {idn}")
    except Exception as exc:
        log(f"ERROR al conectar: {exc}")
        return 1

    # Protecciones acotadas al panel ANTES de habilitar salida (seguridad).
    ovp = ORIGINAL_MODULE.Voc_n * 1.10
    ocp = ORIGINAL_MODULE.Isc_n * 1.15
    opp = ORIGINAL_MODULE.Voc_n * ORIGINAL_MODULE.Isc_n
    try:
        controller.set_protections(ovp=ovp, ocp=ocp, opp=opp)
        log(f"  Protecciones: OVP={ovp:.1f} V  OCP={ocp:.2f} A  OPP={opp:.0f} W")
    except Exception as exc:
        log(f"  AVISO: no se pudieron fijar protecciones: {exc}")

    # Warm-up del import diferido de EAMonitor._get_step() (cadena Dash, 1-10 s en
    # frío) para que no robe las primeras muestras de la primera repetición.
    try:
        import hmi.callbacks.scpi_cb  # noqa: F401
    except Exception:
        pass

    monitor = EAMonitor(controller)

    # ── Ejecutar experimentos ─────────────────────────────────────────────────
    log("\n[4/4] Ejecutando experimentos...")

    meta_base = {
        "ciudad":           args.city,
        "lat":              args.lat,
        "lon":              args.lon,
        "nasa_rango_inicio":args.nasa_start,
        "nasa_rango_fin":   args.nasa_end,
        "estrategia":       strategy,
        "envolvente":       envelope,
        "modelo_termico":   args.thermal,
        "dut":              device.key,
        "dut_label":        device.label,
        "dut_has_mppt":     device.has_mppt,
        "Ns_arreglo":       ORIGINAL_NS,
        "Np_arreglo":       ORIGINAL_NP,
        "tilt_deg":         ORIGINAL_TILT,
        "modulo":           {
            "fuente": "custom",
            "Voc_V":  ORIGINAL_MODULE.Voc_n,
            "Isc_A":  ORIGINAL_MODULE.Isc_n,
            "Vmp_V":  ORIGINAL_MODULE.Vmp_n,
            "Imp_A":  ORIGINAL_MODULE.Imp_n,
            "betaVoc_V_C":  ORIGINAL_MODULE.KV,
            "alphaIsc_A_C": ORIGINAL_MODULE.KI,
            "Ns_celdas":    ORIGINAL_MODULE.Ns,
            "NOCT_C":       ORIGINAL_MODULE.noct,
        },
        "runner_ts": ts_run,
    }

    # Ejecución INTERCALADA: rep externa, modelo interna. Cada modelo acumula sus
    # repeticiones en runs[key]; así los 3 ven condiciones emparejadas por rep.
    runs: dict[str, list[dict]] = {key: [] for key in models_sel}
    for rep in range(1, args.reps + 1):
        for key in models_sel:
            label, _ = MODELS[key]
            log(f"\n── [rep {rep}/{args.reps}] {label} ({key}) / envolvente {envelope} ──")
            result = run_one(
                profile    = profiles[key],
                rep_idx    = rep,
                controller = controller,
                monitor    = monitor,
                dt_ms      = args.dt_ms,
                envelope   = envelope,
                meta       = {**meta_base, "fuente_curva": key,
                              "modelo_label": label},
                log        = log,
                cp_curr_ceiling = ceilings[key],
                curve_drive = args.curve_drive,
            )
            runs[key].append(result)

            # Pausa entre ejecuciones (deja enfriar el bus serie / el DUT)
            is_last = (rep == args.reps and key == models_sel[-1])
            if not is_last:
                log("  ⏱  Pausa 3 s...")
                time.sleep(3)

    # ── Agregar y guardar un JSON por modelo (lo que lee paper_figs) ───────────
    aggs: dict[str, dict] = {}
    summary_models: dict[str, dict] = {}
    for key in models_sel:
        label, _ = MODELS[key]
        agg = aggregate_runs(runs[key])
        aggs[key] = agg

        model_out = out_dir / f"{ts_run}_{key}.json"
        model_out.write_text(
            json.dumps({"model": label, "model_key": key,
                        "config": {**meta_base, "fuente_curva": key},
                        "runs": runs[key], "aggregate": agg},
                       indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        log(f"\n  → Guardado: {model_out.name}")
        log(f"  ── RESUMEN {label} ──")
        log(f"     n_ok={agg.get('n_ok')}/{args.reps} | "
            f"MAE={agg.get('mae_mean_w')} ± {agg.get('mae_std_w')} W | "
            f"RMSE={agg.get('rmse_mean_w')} ± {agg.get('rmse_std_w')} W | "
            f"η̄={agg.get('eta_mean_pct')} ± {agg.get('eta_std_pct')} %")

        summary_models[key] = {
            "model_name": label,
            **{k: agg[k] for k in
               ["n_ok","n_failed","mae_mean_w","mae_std_w",
                "rmse_mean_w","rmse_std_w","eta_mean_pct","eta_std_pct"]
               if k in agg},
        }

    # ── Desconectar ───────────────────────────────────────────────────────────
    try:
        controller.disconnect()
        log("\nFuente desconectada.")
    except Exception:
        pass

    # ── Resumen final ─────────────────────────────────────────────────────────
    summary_path = out_dir / f"{ts_run}_summary.json"
    summary = {
        "runner_ts":   ts_run,
        "config":      meta_base,
        "dt_ms":       args.dt_ms,
        "reps":        args.reps,
        "strategy":    strategy,
        "envelope":    envelope,
        "models":      summary_models,
    }
    summary_path.write_text(
        json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    log("\n" + "=" * 64)
    log(f"RESULTADO — DUT: {device.label} / envolvente {envelope}")
    log("=" * 64)
    log(f"{'Modelo':<24} {'MAE (W)':>14} {'RMSE (W)':>14} {'η̄ (%)':>12} {'N ok':>6}")
    log("-" * 64)
    for key in models_sel:
        label, _ = MODELS[key]
        a = aggs[key]
        mae  = f"{a.get('mae_mean_w','?')} ± {a.get('mae_std_w','?')}"
        rmse = f"{a.get('rmse_mean_w','?')} ± {a.get('rmse_std_w','?')}"
        eta  = f"{a.get('eta_mean_pct','?')} ± {a.get('eta_std_pct','?')}"
        log(f"{label:<24} {mae:>14} {rmse:>14} {eta:>12} {a.get('n_ok','?'):>6}")
    log("=" * 64)
    log("=" * 60)
    log(f"\nResultados en: {out_dir}/")
    log(f"Resumen:       {summary_path.name}")
    log(f"Log:           {log_path.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
