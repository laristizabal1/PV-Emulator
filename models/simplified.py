# models/simplified.py
import math
import numpy as np
from .base import PVModel, ModuleParams, MPPResult

class SimplifiedModel(PVModel):
    """
    Modelo simplificado explícito (ecs. 4–5 del documento, corregido).
    Calcula el MPP directamente desde coeficientes de temperatura.
    No resuelve la ecuación implícita del diodo.

    CORRECCIÓN DE ANCLAJE (validada contra datasheet en STC):
    Las ecuaciones originales del pipeline (notebook / App.jsx computePV())
    anclaban el voltaje a Voc_n y la corriente a Isc_n, devolviendo en STC
    el producto Voc·Isc — un fill factor implícito de 1.0, +26 % sobre la
    Pmp real del panel (FF ≈ 0.79 para el Renogy RNG-100D-SS). Además
    usaban KI [A/°C] como si fuera una fracción relativa (+5 % de corriente
    espurio a dT=20 °C). Se corrige anclando a Vmp_n/Imp_n y usando
    αI = KI/Isc_n [1/°C]. MAE vs referencia pvlib De Soto (mismo datasheet,
    G=100–1000 W/m²): 2.3 % — comparable a los modelos de diodos (1D 3.4 %,
    2D 1.9 % con Rsh(G) De Soto), con desviación máxima a baja G (−5 %).
    """

    def fit(self) -> None:
        self._fitted = True   # sin parámetros que ajustar

    def get_mpp(self, G_poa, T_cell, Ns_arr=1, Np_arr=1,
                V_max_hw=60.0, I_max_hw=170.0) -> MPPResult:
        if G_poa < 5:
            return MPPResult(0.0, 0.0, 0.0)

        p  = self.p
        dT = T_cell - 25.0
        Vt_arr = 0.026 * p.Ns * Ns_arr

        # Ecuación de corriente — ancla Imp_n; KI relativo (αI = KI/Isc_n)
        alpha_I = p.KI / p.Isc_n
        Imp = (p.Imp_n * Np_arr) * (G_poa / 1000.0) * (1.0 + alpha_I * dT)

        # Ecuación de voltaje — ancla Vmp_n con corrección de temperatura
        # (KV del datasheet, V/°C) y término logarítmico de irradiancia
        Vmp = ((p.Vmp_n * Ns_arr)
               + p.KV * dT
               + Vt_arr * math.log(max(G_poa, 1.0) / 1000.0))

        Vmp = float(np.clip(Vmp, 0.0, V_max_hw))
        Imp = float(np.clip(Imp, 0.0, I_max_hw))
        return MPPResult(round(Vmp, 3), round(Imp, 3), round(Vmp * Imp, 1))

    def iv_curve(self, G_poa, T_cell, Ns_arr=1, Np_arr=1,
                 n_pts: int = 200) -> MPPResult:
        """
        Curva I-V explícita — aproximación de una exponencial (C1/C2).

        Coherente con el espíritu del modelo: forma cerrada, sin resolver la
        ecuación implícita del diodo (sin Newton-Raphson). La curva se ancla
        al MPP que calcula get_mpp() y a los ratios Voc/Vmp e Isc/Imp del
        datasheet:

            I(V) = Isc·[1 − C1·(exp(V/(C2·Voc)) − 1)]
            C2   = (Vmp/Voc − 1) / ln(1 − Imp/Isc)
            C1   = (1 − Imp/Isc)·exp(−Vmp/(C2·Voc))

        Habilita las envolventes "cp" y "curve" de run_profile también con
        este modelo (curve_v/curve_i/Voc/Isc vía pipeline.profile.build).
        """
        mpp = self.get_mpp(G_poa, T_cell, Ns_arr, Np_arr)
        if mpp.Pmp <= 0:
            return MPPResult(0.0, 0.0, 0.0)

        p   = self.p
        Voc = mpp.Vmp * (p.Voc_n / p.Vmp_n)   # escala por ratio del datasheet
        Isc = mpp.Imp * (p.Isc_n / p.Imp_n)

        ratio = 1.0 - mpp.Imp / Isc
        if ratio <= 0.0 or Voc <= mpp.Vmp:
            # Curva degenerada (Imp>=Isc o Voc<=Vmp): sin tabla — el caller
            # (pipeline.profile._hourly_curve) degrada a envolvente "direct".
            return MPPResult(mpp.Vmp, mpp.Imp, mpp.Pmp)

        C2 = (mpp.Vmp / Voc - 1.0) / math.log(ratio)
        C1 = ratio * math.exp(-mpp.Vmp / (C2 * Voc))

        V_arr = np.linspace(0.0, Voc, n_pts)
        I_arr = Isc * (1.0 - C1 * (np.exp(V_arr / (C2 * Voc)) - 1.0))
        I_arr = np.clip(I_arr, 0.0, None)
        return MPPResult(mpp.Vmp, mpp.Imp, mpp.Pmp, V_arr, I_arr)