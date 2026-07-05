"""
tools/test_smoke.py
===================
Smoke tests SIN hardware del flujo principal del emulador. Validan que el
pipeline (perfil, térmico, curva pvlib) y un run_profile mockeado funcionan sin
la fuente física conectada.

Ejecutar:
    python tools/test_smoke.py          # runner propio (exit 0 = OK)
    pytest tools/test_smoke.py          # también funciona bajo pytest

Usa la caché NASA local (no requiere red). Si falta la caché de Cali, los tests
de perfil se saltan con aviso.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from models.base import ModuleParams
from models.single_diode import SingleDiodeModel
from models import thermal
from pipeline.nasa_power import fetch as nasa_fetch, DEFAULT_CACHE_DIR
from pipeline.profile import build as build_profile, apply_strategy, _hourly_curve

# Módulo custom de Cali (igual que run_experiment).
_MODULE = ModuleParams(Isc_n=5.2, Voc_n=24.3, Imp_n=4.9, Vmp_n=20.4,
                       KI=0.0026, KV=-0.068, Ns=36, noct=47.0)
# Rango corto (3 días) para que build() sea rápido en los tests; existe en caché.
_CALI = dict(lat=3.45, lon=-76.53, start="20240315", end="20240317")


def _fitted_model() -> SingleDiodeModel:
    m = SingleDiodeModel(_MODULE)
    m.fit()
    return m


def _cali_nasa():
    """NASA de Cali desde caché; None si no está (sin red en CI/offline)."""
    f = DEFAULT_CACHE_DIR / f"nasa_{_CALI['lat']}_{_CALI['lon']}_{_CALI['start']}_{_CALI['end']}.json"
    if not f.exists():
        return None
    return nasa_fetch(cache_dir=DEFAULT_CACHE_DIR, **_CALI)


# ── Tests ───────────────────────────────────────────────────────────────────

def test_thermal_dispatch():
    """noct (default) y faiman (pvlib) coinciden con sus fórmulas; bad → error."""
    G, Ta, w, noct = 800.0, 25.0, 2.0, 47.0
    assert thermal.cell_temperature(G, Ta, noct, model="noct") == \
        thermal.noct_cell_temp(G, Ta, noct)
    assert abs(thermal.cell_temperature(G, Ta, noct, wind=w, model="faiman")
               - (Ta + G / (25.0 + 6.84 * w))) < 1e-9
    # default ahora es faiman (usa viento)
    assert thermal.cell_temperature(G, Ta, noct, wind=w) == thermal.faiman_cell_temp(G, Ta, w)
    try:
        thermal.cell_temperature(G, Ta, noct, model="xxx")
        assert False, "debió lanzar ValueError"
    except ValueError:
        pass


def test_single_diode_model_basic():
    """fit + get_mpp + iv_curve a STC dan valores físicos coherentes."""
    m = _fitted_model()
    mpp = m.get_mpp(G_poa=1000.0, T_cell=25.0)
    assert mpp.Pmp > 50.0
    assert 0 < mpp.Vmp < 60 and 0 < mpp.Imp < 170
    res = m.iv_curve(G_poa=1000.0, T_cell=25.0, n_pts=100)
    assert res.V_arr is not None and len(res.V_arr) == 100
    assert res.I_arr[0] > 0          # Isc > 0
    assert res.V_arr[-1] > 0         # Voc > 0
    # noche (G≈0) → MPP cero, sin curva
    dark = m.get_mpp(G_poa=0.0, T_cell=25.0)
    assert dark.Pmp == 0.0


def test_profile_build_and_strategy():
    """Perfil offline: estructura correcta y día promedio compacto."""
    nasa = _cali_nasa()
    if nasa is None:
        print("  SKIP test_profile_build_and_strategy (sin caché NASA)")
        return
    m = _fitted_model()
    full = build_profile(nasa, m, Ns_arr=1, Np_arr=1, tilt=10.0,
                         V_max=60, I_max=170, attach_curve=True)
    assert full and all(k in full[0] for k in ("Gpoa", "Tcell", "V_set", "I_set", "P_set"))
    avg = apply_strategy(full, "average")
    assert 10 <= len(avg) <= 16          # ~15 horas diurnas
    assert any(s["P_set"] > 0 for s in avg)


def test_default_is_faiman():
    """El default de build() == thermal_model='faiman' y difiere de 'noct'."""
    nasa = _cali_nasa()
    if nasa is None:
        print("  SKIP test_default_is_faiman (sin caché NASA)")
        return
    m = _fitted_model()
    kw = dict(Ns_arr=1, Np_arr=1, tilt=10.0, V_max=60, I_max=170)
    base = build_profile(nasa, m, **kw)
    faim = build_profile(nasa, m, thermal_model="faiman", **kw)
    noct = build_profile(nasa, m, thermal_model="noct", **kw)
    assert max(abs(a["P_set"] - b["P_set"]) for a, b in zip(base, faim)) == 0.0   # default==faiman
    assert any(abs(a["P_set"] - b["P_set"]) > 0 for a, b in zip(base, noct))      # difiere de noct


def test_curve_reattach():
    """Tras 'average' no hay curve_v; re-adjuntar la repone en pasos activos."""
    nasa = _cali_nasa()
    if nasa is None:
        print("  SKIP test_curve_reattach (sin caché NASA)")
        return
    m = _fitted_model()
    full = build_profile(nasa, m, Ns_arr=1, Np_arr=1, tilt=10.0, V_max=60, I_max=170,
                         attach_curve=True)
    avg = apply_strategy(full, "average")
    active = [s for s in avg if s["P_set"] > 0]
    assert active and not any(s.get("curve_v") for s in avg)   # average descarta la curva
    for s in active:
        c = _hourly_curve(m, s["Gpoa"], s["Tcell"], 1, 1, 40, 60, 170)
        s.update(c)
    assert all(s.get("curve_v") and s.get("Voc") and s.get("Isc") for s in active)


def test_run_profile_mock_cp():
    """run_profile con envolvente cp sobre serial mockeado produce muestras
    etiquetadas con la consigna (P_set/hora_emulada), sin hardware."""
    from comm.scpi import SCPIController
    from comm.monitor import EAMonitor
    from tools.bench.transition_bench import FakeSerial

    # Perfil sintético de 2 pasos con Voc/Isc (lo que cp necesita).
    profile = [
        {"label": "h10", "V_set": 20.0, "I_set": 4.0, "P_set": 80.0, "Voc": 24.0, "Isc": 5.0},
        {"label": "h11", "V_set": 21.0, "I_set": 3.5, "P_set": 73.5, "Voc": 24.0, "Isc": 5.0},
    ]
    ctrl = SCPIController()
    ctrl._ser = FakeSerial()                 # serial mockeado (is_open=True)
    monitor = EAMonitor(ctrl)

    # _read_once sintético: V_dc ≈ 0.97·V_set vigente, I_dc ≈ 0.95·I_set.
    def _mock_read():
        return {
            "timestamp": round(time.time(), 3),
            "V_dc": round((ctrl._last_v_set or 0.0) * 0.97, 4),
            "I_dc": round((ctrl._last_i_set or 0.0) * 0.95, 4),
            "P_dc": round((ctrl._last_v_set or 0.0) * 0.97
                          * (ctrl._last_i_set or 0.0) * 0.95, 4),
        }
    monitor._read_once = _mock_read

    # Warm-up del import diferido de _get_step() (cadena Dash) e inyección del
    # primer paso: sin esto la primera llamada importa hmi.callbacks.scpi_cb
    # DENTRO del hilo (1-10 s) y se roba las primeras muestras (igual que en
    # run_experiment / transition_bench).
    try:
        import hmi.callbacks.scpi_cb  # noqa: F401
    except Exception:
        pass
    monitor.set_step(profile[0])

    monitor.start(meta={"smoke": True})
    time.sleep(0.4)
    try:
        ctrl.run_profile(profile, dt_ms=300, envelope="cp",
                         progress_cb=lambda i, n, s: monitor.set_step(s))
    finally:
        monitor.set_step(None)
        monitor.stop()
    buf = monitor.get_buffer()
    assert len(buf) >= 1, "el monitor no produjo muestras"
    assert all("V_dc" in r and "I_dc" in r for r in buf)
    # al menos una muestra quedó etiquetada con la consigna del paso
    assert any(r.get("P_set") in (80.0, 73.5) for r in buf), "no se etiquetó P_set"


# ── Runner propio (sin pytest) ──────────────────────────────────────────────

def _main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    tests = [v for k, v in sorted(globals().items())
             if k.startswith("test_") and callable(v)]
    ok = 0
    for t in tests:
        try:
            t()
            print(f"[OK]   {t.__name__}")
            ok += 1
        except AssertionError as e:
            print(f"[FAIL] {t.__name__}: {e}")
        except Exception as e:
            print(f"[ERR]  {t.__name__}: {type(e).__name__}: {e}")
    print(f"\n{ok}/{len(tests)} tests OK")
    return 0 if ok == len(tests) else 1


if __name__ == "__main__":
    sys.exit(_main())
