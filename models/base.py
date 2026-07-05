# models/base.py
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
import numpy as np

@dataclass
class ModuleParams:
    """PV module datasheet parameters."""
    Isc_n: float          # A  — short-circuit current at STC
    Voc_n: float          # V  — open-circuit voltage at STC
    Imp_n: float          # A  — current at MPP at STC
    Vmp_n: float          # V  — voltage at MPP at STC
    KI:    float          # A/°C — current temperature coefficient
    KV:    float          # V/°C — voltage temperature coefficient
    Ns:    int            # series cells inside the module
    noct:  float = 45.0   # °C — nominal operating cell temperature

@dataclass
class MPPResult:
    """Maximum power point result."""
    Vmp: float
    Imp: float
    Pmp: float
    V_arr: np.ndarray = field(default=None, repr=False)
    I_arr: np.ndarray = field(default=None, repr=False)

class PVModel(ABC):
    """
    Common interface for all PV electrical models.
    """
    def __init__(self, params: ModuleParams):
        self.p = params
        self._fitted = False

    @abstractmethod
    def fit(self) -> None:
        """
        Fit internal parameters to STC.
        Called ONCE before using get_mpp().
        """

    @abstractmethod
    def get_mpp(self,
                G_poa:    float,
                T_cell:   float,
                Ns_arr:   int   = 1,
                Np_arr:   int   = 1,
                V_max_hw: float = 60.0,
                I_max_hw: float = 170.0) -> MPPResult:
        """
        Compute the maximum power point for the given conditions.
        Must respect the hardware limits V_max_hw and I_max_hw.
        """

    def iv_curve(self,
                 G_poa:  float,
                 T_cell: float,
                 Ns_arr: int = 1,
                 Np_arr: int = 1,
                 n_pts:  int = 200) -> MPPResult:
        """Full I-V curve. Optional — not every model implements it."""
        raise NotImplementedError(f"{type(self).__name__} does not implement iv_curve()")
