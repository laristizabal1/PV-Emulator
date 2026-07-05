"""
Two-Diode Photovoltaic Model (Simplified)

Implementation following:
    Ishaque, K., Salam, Z. & Taheri, H. (2011).
    "Simple, fast and accurate two-diode model for photovoltaic modules."
    Solar Energy Materials and Solar Cells, 95(2), 586-594.

Adaptation: same automatic Ns_eff detection as the single-diode model.

All equation references (ec. X) refer to the above paper.
"""

import math
import numpy as np
from .base import PVModel, ModuleParams, MPPResult

# ── Physical constants ──────────────────────────────────────────────
k     = 1.380649e-23
q     = 1.602176634e-19
T_STC = 298.15
G_STC = 1000.0

_VOC_PER_CELL_THRESHOLD = 0.50


def _effective_ns_2d(Ns, Voc_n, Isc_n, Vmp_n, Imp_n, p_f):
    """Effective Ns for the two-diode thermal voltage (see single_diode)."""
    if Ns <= 0:
        return 1
    if Voc_n / Ns >= _VOC_PER_CELL_THRESHOLD:
        return Ns

    Pmax_e = Vmp_n * Imp_n
    margin = max(Pmax_e * 0.01, 5.0)
    target = Pmax_e + margin

    lo, hi = max(Ns // 5, 1), Ns
    best = Ns // 2

    while lo <= hi:
        mid = (lo + hi) // 2
        VT = mid * k * T_STC / q
        if VT <= 0:
            hi = mid - 1; continue
        VT2 = (p_f - 1.0) * VT

        x1 = min(Voc_n / VT, 500.0)
        x2 = min(Voc_n / VT2, 500.0)
        denom_io = math.exp(x1) + math.exp(x2) - 2.0
        if denom_io < 1e-30:
            hi = mid - 1; continue
        Io = Isc_n / denom_io

        Pmax = 0.0
        for i in range(400):
            V = Voc_n * i / 400.0
            e1 = math.exp(min(V / VT, 500.0))
            e2 = math.exp(min(V / VT2, 500.0))
            I = Isc_n - Io * (e1 + e2 - 2.0)
            if I > 0:
                P = V * I
                if P > Pmax:
                    Pmax = P

        if Pmax >= target:
            best = mid; lo = mid + 1
        else:
            hi = mid - 1

    return best


class TwoDiodeModel(PVModel):
    """Simplified two-diode PV model (Ishaque et al. 2011).

    Works with any PV module technology.

    Parameters
    ----------
    params : ModuleParams
    p : float, optional
        Free parameter ≥ 2.2. Controls a2 = p − 1 (default 2.2).
    """

    def __init__(self, params: ModuleParams, p: float = 2.2):
        super().__init__(params)
        if p < 2.2:
            p = 2.2
        self.p_factor = p
        self._Ns_eff = _effective_ns_2d(
            params.Ns, params.Voc_n, params.Isc_n,
            params.Vmp_n, params.Imp_n, p)
        self.Rs: float = 0.0
        self.Rp: float = 0.0
        self.Ipv_stc: float = 0.0
        self.Io_stc: float = 0.0
        self.Vt_stc: float = 0.0

    def fit(self) -> None:
        """Adjust Rs and Rp at STC — Pmp-matching algorithm (Fig. 6).

        LIMITACIÓN DOCUMENTADA — Rs = 0 en módulos pequeños de alto FF:
        igual que en el modelo de un diodo (ver single_diode.fit), el barrido
        de igualación de Pmp puede terminar en Rs=0 para módulos de pocas
        celdas y alto fill factor (Renogy RNG-100D-SS: Rs=0, Rp=91.8 Ω;
        Vmp queda +0.87 V sobre el datasheet). Limitación del método
        publicado (Ishaque 2011), no de la implementación. Con la corrección
        Rsh(G) de De Soto, el MAE de Pmp vs referencia pvlib es 1.9 % — el
        menor de los tres modelos del proyecto.
        """
        if self._fitted:
            return

        par = self.p
        p_f = self.p_factor
        Pmax_e = par.Vmp_n * par.Imp_n

        self.Vt_stc = self._Ns_eff * k * T_STC / q
        VT = self.Vt_stc
        VT2 = (p_f - 1.0) * VT

        Ipv = par.Isc_n

        # ec. 6 — equal saturation currents
        x1 = min(par.Voc_n / VT, 500.0)
        x2 = min(par.Voc_n / VT2, 500.0)
        denom_io = math.exp(x1) + math.exp(x2) - 2.0
        if denom_io < 1e-30:
            denom_io = 1e-30
        Io = par.Isc_n / denom_io

        # ec. 9 — initial Rp
        denom_rp = par.Isc_n - par.Imp_n
        Rp = (par.Vmp_n / denom_rp - (par.Voc_n - par.Vmp_n) / par.Imp_n
               if denom_rp > 0 else 1000.0)
        if Rp <= 0:
            Rp = 1000.0

        Rs = 0.0
        step = 0.001
        max_Rs = 3.0
        best_Rs, best_Rp, best_err = 0.0, Rp, float('inf')

        while Rs < max_Rs:
            z_mpp = par.Vmp_n + par.Imp_n * Rs
            e1 = math.exp(min(z_mpp / VT, 500.0))
            e2 = math.exp(min(z_mpp / VT2, 500.0))
            denom = Ipv - Io * (e1 + e2 - 2.0) - Pmax_e / par.Vmp_n
            if abs(denom) > 1e-15:
                Rp_new = z_mpp / denom
                if Rp_new > 0:
                    Rp = Rp_new

            I_at_Vmp = self._newton_raphson_2d(
                par.Vmp_n, Ipv, Io, Rs, Rp, VT, p_f)
            Pmp_calc = par.Vmp_n * max(I_at_Vmp, 0.0)

            err = abs(Pmp_calc - Pmax_e)
            if err < best_err:
                best_err, best_Rs, best_Rp = err, Rs, Rp

            if Pmp_calc >= Pmax_e:
                break
            Rs += step

        self.Rs, self.Rp = best_Rs, best_Rp
        self.Ipv_stc, self.Io_stc = Ipv, Io
        self._fitted = True

    def get_mpp(self, G_poa: float, T_cell: float,
                Ns_arr: int = 1, Np_arr: int = 1,
                V_max_hw: float = 60.0, I_max_hw: float = 170.0) -> MPPResult:
        """Return the MPP of the array for given G and T."""
        if G_poa < 5:
            return MPPResult(0.0, 0.0, 0.0)

        Ipv, Io, Rs, Rp, VT, p_f, Voc_est = self._scale_to_conditions(
            G_poa, T_cell, Ns_arr, Np_arr)

        n_pts = 500
        V_arr = np.linspace(0.0, Voc_est * 1.05, n_pts)
        I_arr = np.array([
            self._newton_raphson_2d(v, Ipv, Io, Rs, Rp, VT, p_f)
            for v in V_arr])
        I_arr = np.clip(I_arr, 0.0, None)

        P_arr = V_arr * I_arr
        idx = int(np.argmax(P_arr))
        Vmp = float(np.clip(V_arr[idx], 0.0, V_max_hw))
        Imp = float(np.clip(I_arr[idx], 0.0, I_max_hw))

        return MPPResult(round(Vmp, 3), round(Imp, 3), round(Vmp * Imp, 1))

    def iv_curve(self, G_poa: float, T_cell: float,
                 Ns_arr: int = 1, Np_arr: int = 1,
                 n_pts: int = 200) -> MPPResult:
        """Return the full I-V curve and MPP."""
        if G_poa < 5:
            return MPPResult(0.0, 0.0, 0.0)

        Ipv, Io, Rs, Rp, VT, p_f, Voc_est = self._scale_to_conditions(
            G_poa, T_cell, Ns_arr, Np_arr)

        V_arr = np.linspace(0.0, Voc_est * 1.05, n_pts)
        I_arr = np.array([
            self._newton_raphson_2d(v, Ipv, Io, Rs, Rp, VT, p_f)
            for v in V_arr])
        I_arr = np.clip(I_arr, 0.0, None)

        P_arr = V_arr * I_arr
        idx = int(np.argmax(P_arr))
        Vmp = float(V_arr[idx])
        Imp = float(I_arr[idx])
        return MPPResult(round(Vmp, 3), round(Imp, 3), round(Vmp * Imp, 1),
                         V_arr, I_arr)

    # ================================================================
    def _scale_to_conditions(self, G_poa, T_cell, Ns_arr, Np_arr):
        par, p_f = self.p, self.p_factor
        T = T_cell + 273.15
        dT = T_cell - 25.0

        VT_mod = self._Ns_eff * k * T / q
        VT2 = (p_f - 1.0) * VT_mod

        Ipv = (par.Isc_n + par.KI * dT) * (G_poa / G_STC)

        Voc_real = par.Voc_n + par.KV * dT
        x1 = min(Voc_real / VT_mod, 500.0) if VT_mod > 0 else 500.0
        x2 = min(Voc_real / VT2, 500.0)   if VT2 > 0   else 500.0
        denom_io = math.exp(x1) + math.exp(x2) - 2.0
        if denom_io < 1e-30:
            denom_io = 1e-30
        Io = (par.Isc_n + par.KI * dT) / denom_io

        # Resistencia shunt dependiente de irradiancia — De Soto et al. (2006):
        # Rsh = Rsh_ref·(G_ref/G). Ishaque (2011) usa Rp constante, lo que
        # subestima Voc/Pmp a baja irradiancia (ver nota equivalente en
        # single_diode._scale_to_conditions). A G=1000 el factor es 1 y el
        # anclaje STC del fit() no cambia.
        Rp_G = self.Rp * (G_STC / max(G_poa, 1.0))

        Ipv_arr = Ipv * Np_arr
        Io_arr  = Io  * Np_arr
        Rs_arr  = self.Rs * Ns_arr / max(Np_arr, 1)
        Rp_arr  = Rp_G    * Ns_arr / max(Np_arr, 1)
        VT_arr  = VT_mod  * Ns_arr
        Voc_est = max(Voc_real, 1.0) * Ns_arr
        return Ipv_arr, Io_arr, Rs_arr, Rp_arr, VT_arr, p_f, Voc_est

    @staticmethod
    def _newton_raphson_2d(V, Ipv, Io, Rs, Rp, VT, p_f,
                           tol=1e-9, max_iter=50):
        """Solve the implicit two-diode equation for I at V."""
        I = (Ipv - V / Rp) if Rp > 0 else Ipv
        I = max(I, 0.0)
        VT2 = (p_f - 1.0) * VT
        if VT <= 0 or VT2 <= 0:
            return max(I, 0.0)
        for _ in range(max_iter):
            z = V + I * Rs
            exp1 = math.exp(min(z / VT, 500.0))
            exp2 = math.exp(min(z / VT2, 500.0))
            f  = I - Ipv + Io * (exp1 - 1.0) + Io * (exp2 - 1.0) + z / Rp
            df = 1.0 + Io * Rs / VT * exp1 + Io * Rs / VT2 * exp2 + Rs / Rp
            if abs(df) < 1e-30:
                break
            dI = f / df
            I -= dI
            if abs(dI) < tol:
                break
        return max(I, 0.0)