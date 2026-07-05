"""
experiments/paper_figs.py
=========================
Post-análisis de los resultados de run_experiment.py para el paper (6 pág.):
recalcula métricas separando transitorio de régimen permanente y genera las
figuras estratégicas en PNG (300 dpi) + PDF (para LaTeX).

Métricas recalculadas desde los buffers crudos (no re-corre hardware):
    raw    — todas las muestras con P_set > 0.5 W (lo que reporta el runner)
    estab  — descartando los primeros --skip-s segundos de cada hora emulada
             (el transition_bench midió t_estab ~ 6.8 s para la envolvente cp;
             con dt = 7 s el paso entero es transitorio si no se filtra)
    estab≥ — régimen Y excluyendo horas con P_set < --pmin (umbral mínimo de
             entrada del DUT, p.ej. config.devices.get_device(dut).p_min_w: por
             debajo de ese piso el dispositivo no sostiene el punto de operación
             — límite del hardware del DUT, no error de emulación)

Salidas (figuras en INGLÉS, ancho de UNA columna IEEE de 3.5 in, paneles
apilados con etiquetas (a)/(b)/(c) y leyendas en esquinas libres de datos):
    fig1_day_profile     — P_set vs P_dc (régimen, media ± std), (a)(b)(c)
    fig2_metrics         — (a) η y (b) MAE: crudo vs régimen, por modelo
    fig3_voltage_profile — perfil de voltaje: consigna vs medición, (a)(b)(c)
    fig4_current_profile — perfil de corriente: ídem
    fig5_transient       — zoom de una transición horaria (P_dc, V_dc vs t)
    tabla_metricas.md    — tabla resumen por modelo (crudo vs régimen)
    tabla_estrategias.md — comparación de envolventes (del transition_bench;
                           en el paper va como tabla, no como figura)

Uso:
    python -m experiments.paper_figs                       # último run
    python -m experiments.paper_figs --ts 20260610_125202
    python -m experiments.paper_figs --skip-s 4 --pmin 5
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

RESULTS_DIR = _ROOT / "experiments" / "results"
BENCH_DIR   = _ROOT / "tools" / "bench" / "bench_results"

# Modelos eléctricos manuales que run_experiment corre INTERCALADOS (default:
# los 3 primeros; pvlib entra solo con --models all como referencia). El orden
# fija el orden de barras/colores en las figuras. find_session/load_models solo
# levantan los que existan en la sesión.
MODEL_ORDER  = ["single_diode", "two_diode", "simplified"]
# Etiquetas en inglés — las figuras van a un paper IEEE en inglés.
MODEL_LABELS = {
    "single_diode": "Single diode",
    "two_diode":    "Two diodes",
    "simplified":   "Simplified",
}

# Ancho de UNA columna IEEE: 3.5 in (88.9 mm). Todas las figuras se diseñan
# a ese ancho para insertarlas directo en el documento a dos columnas.
COL_W = 3.5


def _panel_label(ax, idx: int, name: str | None = None):
    """Etiqueta de subfigura estilo IEEE: (a), (b), (c) + nombre opcional."""
    txt = f"({chr(97 + idx)})" + (f" {name}" if name else "")
    ax.text(0.02, 0.97, txt, transform=ax.transAxes,
            va="top", ha="left", fontsize=8, fontweight="bold")

# Estilo compacto para paper a dos columnas (IEEE-like)
plt.rcParams.update({
    "font.family":      "serif",
    "font.size":        8,
    "axes.titlesize":   8,
    "axes.labelsize":   8,
    "legend.fontsize":  7,
    "xtick.labelsize":  7,
    "ytick.labelsize":  7,
    "axes.linewidth":   0.6,
    "lines.linewidth":  1.0,
    "figure.dpi":       300,
    "savefig.dpi":      300,
    "savefig.bbox":     "tight",
})

_COLORS = {
    "single_diode": "#1f77b4",
    "two_diode":    "#d62728",
    "simplified":   "#2ca02c",
}


# ── Carga ─────────────────────────────────────────────────────────────────────

def find_session(ts: str | None) -> tuple[str, Path]:
    """
    Localiza la sesión por timestamp (o la más reciente) buscando los JSON
    por modelo en results/ y sus subcarpetas (una por sesión). Se buscan los
    *_<modelo>.json y no el *_summary.json: si una corrida falla a mitad,
    el summary no existe pero los parciales sí — y NO se quiere caer en
    silencio a una sesión vieja.
    """
    candidates: dict[str, Path] = {}
    for key in MODEL_ORDER:
        for f in RESULTS_DIR.rglob(f"*_{key}.json"):
            f_ts = f.name.replace(f"_{key}.json", "")
            candidates[f_ts] = f.parent
    if not candidates:
        sys.exit(f"No hay resultados *_<modelo>.json en {RESULTS_DIR}")
    if ts is None:
        ts = max(candidates)          # timestamps YYYYMMDD_HHMMSS ordenan bien
    if ts not in candidates:
        sys.exit(f"No hay sesión {ts}. Disponibles: {sorted(candidates)}")
    return ts, candidates[ts]


def load_models(ts: str, session_dir: Path) -> dict:
    out = {}
    for key in MODEL_ORDER:
        f = session_dir / f"{ts}_{key}.json"
        if f.exists():
            out[key] = json.loads(f.read_text(encoding="utf-8"))
    if not out:
        sys.exit(f"No hay resultados {ts}_*.json en {session_dir}")
    return out


def latest_bench() -> dict | None:
    benches = sorted(BENCH_DIR.glob("transition_bench_*.json"))
    if not benches:
        return None
    return json.loads(benches[-1].read_text(encoding="utf-8"))


# ── Métricas régimen/transitorio ──────────────────────────────────────────────

def split_blocks(buffer: list[dict]) -> list[dict]:
    """
    Divide el buffer cronológico en bloques por hora emulada.
    Retorna [{hora, t0, rows}], donde t0 = timestamp de la primera muestra
    del bloque (inicio del paso — la consigna se notifica al inicio).
    """
    blocks = []
    _UNSET = object()          # centinela: hora_emulada puede ser None/""
    cur_h, cur = _UNSET, None
    for r in buffer:
        h = r.get("hora_emulada")
        if h != cur_h or cur is None:
            cur_h = h
            cur = {"hora": h, "t0": r["timestamp"], "rows": []}
            blocks.append(cur)
        cur["rows"].append(r)
    return blocks


def rep_metrics(buffer: list[dict], skip_s: float, pmin: float) -> dict:
    """Métricas raw / régimen / régimen≥pmin de una repetición."""
    blocks = split_blocks(buffer)

    def _filter(steady: bool, with_pmin: bool):
        rows = []
        for b in blocks:
            for r in b["rows"]:
                ps = r.get("P_set")
                if ps is None or ps <= 0.5:
                    continue
                if steady and (r["timestamp"] - b["t0"]) < skip_s:
                    continue
                if with_pmin and ps < pmin:
                    continue
                rows.append(r)
        return rows

    def _calc(rows):
        if not rows:
            return {"mae_w": None, "rmse_w": None, "eta_pct": None, "n": 0}
        err = np.array([abs(r["P_set"] - r["P_dc"]) for r in rows])
        eta = np.array([r["P_dc"] / r["P_set"] * 100 for r in rows])
        return {"mae_w":  round(float(err.mean()), 3),
                "rmse_w": round(float(np.sqrt((err**2).mean())), 3),
                "eta_pct": round(float(eta.mean()), 2),
                "n": len(rows)}

    return {
        "raw":       _calc(_filter(steady=False, with_pmin=False)),
        "estab":     _calc(_filter(steady=True,  with_pmin=False)),
        "estab_op":  _calc(_filter(steady=True,  with_pmin=True)),
    }


def hourly_steady(runs: list[dict], skip_s: float,
                  field: str = "P_dc") -> dict:
    """Señal medida de régimen por hora emulada, agregada sobre repeticiones."""
    acc: dict[str, list[float]] = {}
    for run in runs:
        if run.get("error"):
            continue
        for b in split_blocks(run["buffer"]):
            if not b["hora"]:
                continue
            vals = [r[field] for r in b["rows"]
                    if (r["timestamp"] - b["t0"]) >= skip_s
                    and r.get(field) is not None]
            if vals:
                acc.setdefault(b["hora"], []).extend(vals)
    return {h: (float(np.mean(v)), float(np.std(v)))
            for h, v in acc.items()}


def _hour_num(label: str) -> float:
    """Hora numérica desde la etiqueta. Soporta enteras ("13h"), fraccionales
    ("13.5h", de pipeline.profile.densify) y con prefijo de fecha ("02/19 13h",
    de strategy='day' — toma el último token e ignora la fecha)."""
    s = str(label).strip()
    if s:
        s = s.split()[-1].rstrip("h")     # último token "HHh", ignora "MM/DD "
    try:
        return float(s)
    except ValueError:
        if ":" in s:                      # tolera formato "HH:MM" por si acaso
            hh, _, mm = s.partition(":")
            try:
                return int(hh) + (int(mm) / 60 if mm else 0)
            except ValueError:
                return 0.0
        return 0.0


# ── Figuras ───────────────────────────────────────────────────────────────────

def fig_compuesta(models: dict, skip_s: float, pmin: float, out: Path):
    """
    Composite daily-profile figure for the PRIMARY curve source (single_diode,
    the experiment default): setpoint (model) vs measured steady-state (mean ±
    std over reps), in THREE stacked panels — (a) power, (b) current, (c) voltage
    — at one IEEE column width.

    The day profile is illustrative (one model); the per-model comparison lives in
    fig_metrics. Shaded bands mark hours below the DUT minimum input threshold
    (P_set < pmin). Legend once, in the power panel (the power curve is
    bell-shaped, so the upper-right corner is free of data).
    """
    key = next((k for k in MODEL_ORDER if k in models), list(models)[0])
    d   = models[key]
    profile = d["runs"][0]["meta"]["perfil_consignas"]
    hours   = [_hour_num(s["label"]) for s in profile]
    color   = _COLORS.get(key, "#1f77b4")

    # (set_key, meas_key, y-label, head-room, legend location | None)
    specs = [
        ("P_set", "P_dc", "Power (W)",   1.45, "upper right"),
        ("I_set", "I_dc", "Current (A)", 1.45, None),
        ("V_set", "V_dc", "Voltage (V)", 1.30, None),
    ]

    fig, axes = plt.subplots(3, 1, figsize=(COL_W, 4.7), sharex=True)
    for idx, (ax, (set_key, meas_key, ylabel, headroom, legend_loc)) in \
            enumerate(zip(axes, specs)):
        set_vals = [s[set_key] for s in profile]
        ax.step(hours, set_vals, where="mid", color="0.35", lw=1.0,
                label="Setpoint (model)")

        hs = hourly_steady(d["runs"], skip_s, meas_key)
        hh = sorted(hs, key=_hour_num)
        ax.errorbar([_hour_num(h) for h in hh],
                    [hs[h][0] for h in hh],
                    yerr=[hs[h][1] for h in hh],
                    fmt="o", ms=2.5, capsize=1.5, lw=0.8,
                    color=color, label="Measured (steady-state)")

        # Shade hours below the DUT minimum input threshold
        for h, p in zip(hours, (s["P_set"] for s in profile)):
            if 0 < p < pmin:
                ax.axvspan(h - 0.5, h + 0.5, color="0.85", zorder=0)

        _panel_label(ax, idx)
        ax.set_ylabel(ylabel)
        ax.grid(True, lw=0.3, alpha=0.5)
        ax.set_ylim(0, max(set_vals) * headroom)
        if legend_loc:
            ax.legend(loc=legend_loc, frameon=False, fontsize=6.5)

    axes[-1].set_xlabel("Hour of day")
    axes[-1].set_xticks(range(6, 20, 2))
    fig.align_ylabels(axes)
    for ext in ("png", "pdf"):
        fig.savefig(out / f"fig_composite_pvi.{ext}")
    plt.close(fig)


def fig_metrics(table: dict, out: Path):
    """
    fig2 — η y MAE, crudo vs régimen, barras por modelo. Paneles (a)(b)
    apilados a una columna. Leyenda en la franja superior del panel (a)
    (las barras llegan a ~96 % y el techo se fija en 130 → libre).
    """
    keys   = [k for k in MODEL_ORDER if k in table]
    labels = [MODEL_LABELS[k] for k in keys]
    x      = np.arange(len(keys))
    w      = 0.36

    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(COL_W, 3.9))

    for idx, (ax, metric, ylabel) in enumerate(
            ((ax1, "eta_pct", r"Efficiency $\eta$ (%)"),
             (ax2, "mae_w",  "MAE (W)"))):
        raw_m  = [table[k]["raw"][metric][0]      for k in keys]
        raw_s  = [table[k]["raw"][metric][1]      for k in keys]
        st_m   = [table[k]["estab_op"][metric][0] for k in keys]
        st_s   = [table[k]["estab_op"][metric][1] for k in keys]
        ax.bar(x - w/2, raw_m, w, yerr=raw_s, capsize=2,
               color="0.75", edgecolor="0.3", lw=0.5, label="Raw")
        ax.bar(x + w/2, st_m, w, yerr=st_s, capsize=2,
               color="#2ca02c", edgecolor="0.3", lw=0.5,
               label=r"Steady-state, $P_{set} \geq P_{min}$")
        ax.set_xticks(x, labels)
        ax.set_ylabel(ylabel)
        ax.grid(True, axis="y", lw=0.3, alpha=0.5)
        _panel_label(ax, idx)

    ax1.axhline(100, color="0.4", lw=0.5, ls="--")
    ax1.set_ylim(0, 132)               # franja superior libre para la leyenda
    ax1.legend(loc="upper right", frameon=False, fontsize=6.5)
    ax2.set_ylim(0, max(table[k]["raw"]["mae_w"][0] +
                        table[k]["raw"]["mae_w"][1] for k in keys) * 1.35)

    for ext in ("png", "pdf"):
        fig.savefig(out / f"fig2_metrics.{ext}")
    plt.close(fig)


def tabla_estrategias(bench: dict, out: Path):
    """
    Tabla markdown con la comparación de estrategias de envolvente del
    transition_bench (reemplaza la antigua fig3 — en el paper va como tabla).
    """
    res = bench.get("resultados", {})
    strategies: dict[str, dict[str, dict]] = {}
    for model_key, model_res in res.items():
        for strat, v in model_res.items():
            s = v.get("resumen", {})
            if s.get("valido"):
                strategies.setdefault(strat, {})[model_key] = s

    if not strategies:
        print("  (bench sin resúmenes válidos — se omite tabla de estrategias)")
        return

    model_keys = sorted({mk for v in strategies.values() for mk in v})
    order = sorted(strategies,
                   key=lambda st: np.mean([m.get("P_err_medio_pct") or 999
                                           for m in strategies[st].values()]))

    lines = [
        "# Envelope-strategy comparison — transition_bench",
        "",
        f"Source: `{bench.get('fecha', '?')}` | module `{bench.get('modulo', '?')}` "
        f"| dt={bench.get('dt_s', '?')} s/step | load: GMI120L (real MPPT)",
        "",
        "Mean steady-state |power error| [%] by strategy and source:",
        "",
        "| Strategy | " + " | ".join(MODEL_LABELS.get(m, m) for m in model_keys)
        + " | Mean | Mean V_err [V] | Mean t_settle [s] |",
        "|---|" + "---|" * (len(model_keys) + 3),
    ]
    for st in order:
        per_model = strategies[st]
        p_errs = [per_model.get(m, {}).get("P_err_medio_pct") for m in model_keys]
        v_errs = [v.get("V_err_medio") for v in per_model.values()
                  if v.get("V_err_medio") is not None]
        t_est  = [v.get("t_estab_medio") for v in per_model.values()
                  if v.get("t_estab_medio") is not None]
        cells = " | ".join(f"{p:.1f}" if p is not None else "--" for p in p_errs)
        mean_p = np.mean([p for p in p_errs if p is not None])
        lines.append(
            f"| `{st}` | {cells} | **{mean_p:.1f}** "
            f"| {np.mean(v_errs):.2f} | {np.mean(t_est):.1f} |")

    lines += [
        "",
        "Notes:",
        "- `cp` (VOLT=Voc, CURR=Isc, POW=Pmp — native constant-power loop of the",
        "  PS 10000): strategy selected for the experiment.",
        "- `ramp`/`instant` (V/I rectangle): the MPPT lacks a gradient and settles",
        "  far from the MPP — the original problem of direct setpoint control.",
        "- `curve`/`slope` (software I(V) loops at ~5 Hz): latch-up at Voc — the",
        "  loop interpolates I≈0 near open circuit and the MPPT cannot start.",
        "  An SCPI-latency limitation, not a limitation of the concept.",
    ]
    (out / "tabla_estrategias.md").write_text("\n".join(lines) + "\n",
                                              encoding="utf-8")


# (fig_voltage_profiles / fig_current_profiles fueron fusionadas en fig_compuesta)


def fig_transitorio(models: dict, skip_s: float, out: Path):
    """fig4 — zoom de la transición horaria con mayor ΔP (modelo más estable)."""
    key = next((k for k in MODEL_ORDER if k in models), list(models)[-1])
    run = next(r for r in models[key]["runs"] if not r.get("error"))
    blocks = [b for b in split_blocks(run["buffer"]) if b["hora"]]

    # Transición con mayor ΔP entre bloques consecutivos con potencia
    best, best_dp = None, -1.0
    for a, b in zip(blocks, blocks[1:]):
        pa = np.median([r.get("P_dc") or 0 for r in a["rows"]])
        pb = np.median([r.get("P_dc") or 0 for r in b["rows"]])
        if min(pa, pb) > 1.0 and abs(pb - pa) > best_dp:
            best_dp, best = abs(pb - pa), (a, b)
    if best is None:
        print("  (sin transición con potencia — se omite fig4)")
        return
    a, b = best

    rows = a["rows"] + b["rows"]
    t = np.array([r["timestamp"] - b["t0"] for r in rows])
    p = np.array([r.get("P_dc") or np.nan for r in rows])
    v = np.array([r.get("V_dc") or np.nan for r in rows])

    fig, ax = plt.subplots(figsize=(COL_W, 2.4))
    ax.plot(t, p, "o-", ms=2, lw=0.8, color="#d62728", label="$P_{dc}$")
    ax.axvline(0, color="0.3", lw=0.6, ls="--")
    ax.axvspan(0, skip_s, color="0.88", zorder=0,
               label=f"Discarded transient ({skip_s:.0f} s)")
    for blk in (a, b):
        ps = blk["rows"][0].get("P_set")
        if ps:
            t_blk = [r["timestamp"] - b["t0"] for r in blk["rows"]]
            ax.hlines(ps, min(t_blk), max(t_blk), color="0.35", lw=0.8,
                      ls=":")
    h_a = str(a["hora"]).rstrip("h")
    h_b = str(b["hora"]).rstrip("h")
    ax.set_xlabel(f"Time from setpoint change ({h_a} h → {h_b} h) (s)")
    ax.set_ylabel("Power (W)", color="#d62728")
    ax2 = ax.twinx()
    ax2.plot(t, v, "s-", ms=1.5, lw=0.6, color="#1f77b4", alpha=0.7)
    ax2.set_ylabel("Voltage (V)", color="#1f77b4")
    # Esquina inferior derecha: tras la transición P queda arriba y V al
    # centro — la banda inferior derecha está libre de datos.
    ax.legend(loc="lower right", frameon=False, fontsize=6.5)
    ax.grid(True, lw=0.3, alpha=0.5)
    for ext in ("png", "pdf"):
        fig.savefig(out / f"fig5_transient.{ext}")
    plt.close(fig)


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser(description="Figuras y métricas para el paper")
    ap.add_argument("--ts", default=None, help="Timestamp del run (def: último)")
    ap.add_argument("--skip-s", type=float, default=4.0,
                    help="Segundos de transitorio a descartar por hora")
    ap.add_argument("--pmin", type=float, default=5.0,
                    help="Umbral mínimo de P_set [W] (entrada del DUT — ver "
                         "config.devices p_min_w)")
    args = ap.parse_args()

    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    ts, session_dir = find_session(args.ts)
    models = load_models(ts, session_dir)
    out = session_dir / f"paper_{ts}"
    out.mkdir(exist_ok=True)

    print(f"Run: {ts} | sesión: {session_dir} | "
          f"skip={args.skip_s} s | pmin={args.pmin} W")
    print(f"Salida: {out}\n")

    # ── Recalcular métricas ──────────────────────────────────────────────────
    table: dict = {}
    lines = ["| Model | MAE raw [W] | MAE steady≥ [W] | η raw [%] | "
             "η steady≥ [%] | reps |",
             "|---|---|---|---|---|---|"]
    for key, d in models.items():
        per_rep = [rep_metrics(r["buffer"], args.skip_s, args.pmin)
                   for r in d["runs"] if not r.get("error")]

        def _agg(scope, metric):
            vals = [m[scope][metric] for m in per_rep
                    if m[scope][metric] is not None]
            return (round(float(np.mean(vals)), 2),
                    round(float(np.std(vals)), 2)) if vals else (None, None)

        table[key] = {sc: {met: _agg(sc, met)
                           for met in ("mae_w", "rmse_w", "eta_pct")}
                      for sc in ("raw", "estab", "estab_op")}

        t = table[key]
        print(f"── {MODEL_LABELS[key]} ──")
        for sc, lbl in (("raw", "crudo            "),
                        ("estab", "régimen          "),
                        ("estab_op", "régimen ≥ pmin   ")):
            m = t[sc]
            print(f"  {lbl} MAE={m['mae_w'][0]}±{m['mae_w'][1]} W   "
                  f"RMSE={m['rmse_w'][0]}±{m['rmse_w'][1]} W   "
                  f"η={m['eta_pct'][0]}±{m['eta_pct'][1]} %")
        per_rep_eta = [m["estab_op"]["eta_pct"] for m in per_rep]
        print(f"  η régimen≥ por rep: {per_rep_eta}\n")

        lines.append(
            f"| {MODEL_LABELS[key]} | {t['raw']['mae_w'][0]} ± {t['raw']['mae_w'][1]} "
            f"| {t['estab_op']['mae_w'][0]} ± {t['estab_op']['mae_w'][1]} "
            f"| {t['raw']['eta_pct'][0]} ± {t['raw']['eta_pct'][1]} "
            f"| {t['estab_op']['eta_pct'][0]} ± {t['estab_op']['eta_pct'][1]} "
            f"| {len(per_rep)} |")

    (out / "tabla_metricas.md").write_text(
        "\n".join(lines) + "\n", encoding="utf-8")

    # ── Figuras y tablas ─────────────────────────────────────────────────────
    print("Generando figuras (IEEE, una columna, inglés)...")
    # Limpiar artefactos de versiones anteriores del script
    for pat in ("fig3_estrategias.*", "fig1_perfil_dia.*", "fig2_metricas.*",
                "fig3_perfiles_vi.*", "fig4_transitorio.*",
                "fig1_day_profile.*", "fig3_voltage_profile.*",
                "fig4_current_profile.*"):
        for stale in out.glob(pat):
            stale.unlink()
    fig_compuesta(models, args.skip_s, args.pmin, out)
    fig_metrics(table, out)
    bench = latest_bench()
    if bench:
        tabla_estrategias(bench, out)
    fig_transitorio(models, args.skip_s, out)

    print(f"\nListo. Archivos en {out}:")
    for f in sorted(out.iterdir()):
        print(f"  {f.name}")


if __name__ == "__main__":
    main()
