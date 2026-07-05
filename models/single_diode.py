"""
Single-Diode Photovoltaic Model

Implementation following:
    Villalva, M.G., Gazoli, J.R. & Filho, E.R. (2009).
    "Comprehensive Approach to Modeling and Simulation of Photovoltaic Arrays."
    IEEE Transactions on Power Electronics, 24(5), 1198-1208.

Adaptation: automatic detection of the effective series cell count (Ns_eff)
for modules whose reported Ns includes cells in internal parallel sub-strings
(half-cut, third-cut, shingled, etc.).

All equation references (ec. X) refer to the above paper.
"""

import math
import numpy as np
from .base import PVModel, ModuleParams, MPPResult

# ── Physical constants ──────────────────────────────────────────────
k     = 1.380649e-23      # J/K  — Boltzmann constant
q     = 1.602176634e-19   # C    — electron charge
T_STC = 298.15            # K    — 25 °C
G_STC = 1000.0            # W/m²

# Threshold: if Voc_n / Ns falls below this value, the module uses
# internal parallel sub-strings and Ns must be corrected.
_VOC_PER_CELL_THRESHOLD = 0.50  # V — standard Si cells are 0.60–0.70 V


def _effective_ns(Ns: int, Voc_n: float, Isc_n: float,
                  Vmp_n: float, Imp_n: float, a: float) -> int:
    """Determine the effective series cell count for the thermal voltage.

    For modules where Voc_n / Ns < 0.50 V (half-cut, third-cut, shingled),
    the reported Ns overstates the series count.  This function binary-searches
    for the largest Ns_eff where the ideal single-diode Pmax (Rs=0, Rp→∞)
    exceeds Pmax_e by a safety margin, ensuring the iterative algorithm can
    converge for any module technology.
    """
    if Ns <= 0:
        return 1
    if Voc_n / Ns >= _VOC_PER_CELL_THRESHOLD:
        return Ns  # standard module — no correction

    Pmax_e = Vmp_n * Imp_n
    margin = max(Pmax_e * 0.01, 5.0)  # 1 % or 5 W, whichever is larger
    target = Pmax_e + margin

    lo, hi = max(Ns // 5, 1), Ns
    best = Ns // 2  # fallback

    while lo <= hi:
        mid = (lo + hi) // 2
        Vt = mid * k * T_STC / q
        aVt = a * Vt
        if aVt <= 0:
            hi = mid - 1
            continue
        exp_voc = math.exp(min(Voc_n / aVt, 500.0))
        if exp_voc <= 1.0:
            hi = mid - 1
            continue
        I0 = Isc_n / (exp_voc - 1.0)

        # Quick ideal sweep (Rs=0, Rp=∞)
        Pmax = 0.0
        for i in range(400):
            V = Voc_n * i / 400.0
            I = Isc_n - I0 * (math.exp(min(V / aVt, 500.0)) - 1.0)
            if I > 0:
                P = V * I
                if P > Pmax:
                    Pmax = P

        if Pmax >= target:
            best = mid
            lo = mid + 1   # try a larger (less aggressive) Ns_eff
        else:
            hi = mid - 1   # need smaller Ns_eff

    return best


class SingleDiodeModel(PVModel):
    """Single-diode PV model with Rs and Rp (Villalva et al. 2009).

    Works with any crystalline-Si or thin-film PV module.  For modules
    that use internal parallel sub-strings (half-cut, third-cut, shingled),
    the effective series cell count is detected automatically.

    Parameters
    ----------
    params : ModuleParams
        Datasheet parameters of the PV module.
    a : float, optional
        Diode ideality factor (default 1.3, valid range 1.0–1.5 for mono-Si).
    """

    def __init__(self, params: ModuleParams, a: float = 1.3):
        super().__init__(params)
        self.a = a
        self._Ns_eff = _effective_ns(
            params.Ns, params.Voc_n, params.Isc_n,
            params.Vmp_n, params.Imp_n, a
        )
        self.Rs: float = 0.0
        self.Rp: float = 0.0
        self.Ipv_stc: float = 0.0
        self.I0_stc: float = 0.0
        self.Vt_stc: float = 0.0

    # ────────────────────────────────────────────────────────────────
    # fit()
    # ────────────────────────────────────────────────────────────────
    def fit(self) -> None:
        """Adjust Rs and Rp at STC — iterative algorithm of Fig. 13.

        Implements the Pmax-matching loop.  When ec. 9 yields a negative Rp
        (common for high-fill-factor modules), the solver uses a large Rp
        and tracks the minimum-error {Rs, Rp} pair as a fallback.

        LIMITACIÓN DOCUMENTADA — Rs = 0 en módulos pequeños de alto FF:
        el barrido incrementa Rs desde 0 y se detiene cuando Pmax_modelo
        iguala Pmax_datasheet. En módulos de pocas celdas y alto fill factor
        (p.ej. Renogy RNG-100D-SS, 36 celdas, FF≈0.79) la igualdad ya se
        cumple en Rs=0 y el barrido termina ahí, dejando el codo más afilado
        y Vmp ligeramente alto (+0.36 V medido vs datasheet). El ajuste
        simultáneo de 5 parámetros de De Soto (pvlib fit_desoto, solver LM)
        encuentra Rs=0.225 Ω para el mismo panel. Es una limitación del
        método de igualación de Pmax publicado (Villalva 2009), no de esta
        implementación; el error de Pmp resultante (MAE 3.4 % vs referencia
        pvlib en G=100–1000) queda dentro de la tolerancia del datasheet.
        """
        if self._fitted:
            return

        p = self.p
        Pmax_e = p.Vmp_n * p.Imp_n

        self.Vt_stc = self._Ns_eff * k * T_STC / q
        Vt = self.Vt_stc
        a  = self.a

        # Initial Rp — ec. 11
        denom_rp = p.Isc_n - p.Imp_n
        if denom_rp > 0:
            Rp = p.Vmp_n / denom_rp - (p.Voc_n - p.Vmp_n) / p.Imp_n
        else:
            Rp = 1000.0
        if Rp <= 0:
            Rp = 1000.0

        Rs = 0.0
        step = 0.001
        max_Rs = 3.0

        best_Rs, best_Rp = 0.0, Rp
        best_Ipv, best_I0 = p.Isc_n, 0.0
        best_err = float('inf')

        while Rs < max_Rs:
            # ec. 7 — saturation current (improved)
            I0 = (p.Isc_n + p.KI * 0.0) / (
                math.exp(min((p.Voc_n) / (a * Vt), 500.0)) - 1.0
            )

            # ec. 10 — photovoltaic current refinement
            Ipv = ((Rp + Rs) / Rp) * p.Isc_n if Rp > 0 else p.Isc_n

            # ec. 9 — Rp as function of Rs
            exp_mpp = math.exp(min((p.Vmp_n + p.Imp_n * Rs) / (a * Vt), 500.0))
            denom = (p.Vmp_n * Ipv
                     - p.Vmp_n * I0 * exp_mpp
                     + p.Vmp_n * I0
                     - Pmax_e)
            if abs(denom) > 1e-15:
                Rp_new = p.Vmp_n * (p.Vmp_n + p.Imp_n * Rs) / denom
                if Rp_new > 0:
                    Rp = Rp_new

            # Sweep I-V curve for Pmax
            Pmax_m = self._calc_pmax(Ipv, I0, Rs, Rp, Vt, a, p.Voc_n)

            err = abs(Pmax_m - Pmax_e)
            if err < best_err:
                best_err = err
                best_Rs, best_Rp = Rs, Rp
                best_Ipv, best_I0 = Ipv, I0

            if Pmax_m >= Pmax_e:
                break
            Rs += step

        self.Rs = best_Rs
        self.Rp = best_Rp
        self.Ipv_stc = best_Ipv
        self.I0_stc = best_I0
        self._fitted = True

    # ────────────────────────────────────────────────────────────────
    # get_mpp()
    # ────────────────────────────────────────────────────────────────
    def get_mpp(self, G_poa: float, T_cell: float,
                Ns_arr: int = 1, Np_arr: int = 1,
                V_max_hw: float = 60.0, I_max_hw: float = 170.0) -> MPPResult:
        """Return the MPP of the array for given G and T.

        Scales module parameters to the array and sweeps the I-V curve.
        """
        if G_poa < 5:
            return MPPResult(0.0, 0.0, 0.0)

        Ipv, I0, Rs, Rp, Vt, a, Voc_est = self._scale_to_conditions(
            G_poa, T_cell, Ns_arr, Np_arr)

        n_pts = 500
        V_arr = np.linspace(0.0, Voc_est * 1.05, n_pts)
        I_arr = np.array([
            self._newton_raphson(v, Ipv, I0, Rs, Rp, Vt, a) for v in V_arr])
        I_arr = np.clip(I_arr, 0.0, None)

        P_arr = V_arr * I_arr
        idx = int(np.argmax(P_arr))
        Vmp = float(np.clip(V_arr[idx], 0.0, V_max_hw))
        Imp = float(np.clip(I_arr[idx], 0.0, I_max_hw))

        return MPPResult(round(Vmp, 3), round(Imp, 3), round(Vmp * Imp, 1))

    # ────────────────────────────────────────────────────────────────
    # iv_curve()
    # ────────────────────────────────────────────────────────────────
    def iv_curve(self, G_poa: float, T_cell: float,
                 Ns_arr: int = 1, Np_arr: int = 1,
                 n_pts: int = 200) -> MPPResult:
        """Return the full I-V curve and MPP. Implements ec. 3 via NR."""
        if G_poa < 5:
            return MPPResult(0.0, 0.0, 0.0)

        Ipv, I0, Rs, Rp, Vt, a, Voc_est = self._scale_to_conditions(
            G_poa, T_cell, Ns_arr, Np_arr)

        V_arr = np.linspace(0.0, Voc_est * 1.05, n_pts)
        I_arr = np.array([
            self._newton_raphson(v, Ipv, I0, Rs, Rp, Vt, a) for v in V_arr])
        I_arr = np.clip(I_arr, 0.0, None)

        P_arr = V_arr * I_arr
        idx = int(np.argmax(P_arr))
        Vmp = float(V_arr[idx])
        Imp = float(I_arr[idx])
        return MPPResult(round(Vmp, 3), round(Imp, 3), round(Vmp * Imp, 1),
                         V_arr, I_arr)

    # ================================================================
    # Private helpers
    # ================================================================

    def _scale_to_conditions(self, G_poa, T_cell, Ns_arr, Np_arr):
        """Scale module parameters to (G, T) and array dimensions.

        Implements ec. 4 (Ipv), ec. 7 (I0), and array scaling.
        """
        p  = self.p
        T  = T_cell + 273.15
        dT = T_cell - 25.0
        a  = self.a

        Vt_mod = self._Ns_eff * k * T / q

        Ipv_n = ((self.Rp + self.Rs) / self.Rp) * p.Isc_n if self.Rp > 0 else p.Isc_n
        Ipv = (Ipv_n + p.KI * dT) * (G_poa / G_STC)

        Voc_T = p.Voc_n + p.KV * dT
        denom_exp = math.exp(min(Voc_T / (a * Vt_mod), 500.0)) - 1.0
        if denom_exp < 1e-30:
            denom_exp = 1e-30
        I0 = (p.Isc_n + p.KI * dT) / denom_exp

        # Resistencia shunt dependiente de irradiancia — De Soto et al. (2006),
        # ec. validada en campo (Sandia) y usada por pvlib: Rsh = Rsh_ref·(G_ref/G).
        # Villalva (2009) usa Rp constante (ajustado en STC), lo que subestima
        # Voc y Pmp a baja irradiancia (-13 % a G=200 vs De Soto): con poca
        # fotocorriente, un Rp fijo drena proporcionalmente más cerca de Voc.
        # A G=1000 el factor es 1 → el anclaje STC del fit() no cambia.
        Rp_G = self.Rp * (G_STC / max(G_poa, 1.0))

        Ipv_arr = Ipv * Np_arr
        I0_arr  = I0  * Np_arr
        Rs_arr  = self.Rs * Ns_arr / max(Np_arr, 1)
        Rp_arr  = Rp_G    * Ns_arr / max(Np_arr, 1)
        Vt_arr  = Vt_mod  * Ns_arr

        Voc_est = max(Voc_T, 1.0) * Ns_arr
        return Ipv_arr, I0_arr, Rs_arr, Rp_arr, Vt_arr, a, Voc_est

    @staticmethod
    def _newton_raphson(V, Ipv, I0, Rs, Rp, Vt, a,
                        tol=1e-9, max_iter=50):
        """Solve ec. 3 implicitly for I at given V (Newton-Raphson)."""
        I = (Ipv - V / Rp) if Rp > 0 else Ipv
        I = max(I, 0.0)
        aVt = a * Vt
        if aVt <= 0:
            return max(I, 0.0)
        for _ in range(max_iter):
            z = V + I * Rs
            exp_x = math.exp(min(z / aVt, 500.0))
            f  = I - Ipv + I0 * (exp_x - 1.0) + z / Rp
            df = 1.0 + I0 * Rs / aVt * exp_x + Rs / Rp
            if abs(df) < 1e-30:
                break
            dI = f / df
            I -= dI
            if abs(dI) < tol:
                break
        return max(I, 0.0)

    def _calc_pmax(self, Ipv, I0, Rs, Rp, Vt, a, Voc):
        """Sweep module I-V curve and return peak power."""
        Pmax = 0.0
        for i in range(500):
            v = Voc * i / 500.0
            i_val = max(self._newton_raphson(v, Ipv, I0, Rs, Rp, Vt, a), 0.0)
            p = v * i_val
            if p > Pmax:
                Pmax = p
        return Pmax