"""
models/
=======
Equivalent electrical models of the PV panel.

    base          — ABC PVModel + dataclasses ModuleParams, MPPResult
    panel_factory — panel_from_datasheet(): converts datasheet -> ModuleParams
                    with auto-detection of the coefficient convention (%/C or A/C)
    simplified    — SimplifiedModel: explicit closed-form MPP (no diode network)
    single_diode  — SingleDiodeModel: single-diode model with Rs/Rsh
    two_diode     — TwoDiodeModel: two-diode model (higher accuracy at low G)
    thermal       — NOCT and Faiman cell-temperature models (orthogonal to the
                    electrical models above: they supply T_cell, not the I-V curve)

The electrical models (simplified / single_diode / two_diode) and the thermal
models combine — they do not compete. Any of them implements the PVModel
interface (fit / get_mpp / iv_curve), so the pipeline is model-agnostic.
(pvlib stays a dependency for POA transposition and the Faiman thermal model,
but is no longer offered as an electrical curve model.)

Standard usage:
    from models.panel_factory import panel_from_datasheet
    from models.single_diode   import SingleDiodeModel

    # Coefficients in A/C and V/C (absolute)
    params = panel_from_datasheet(
        Isc=13.5, Voc=49.8, Imp=13.35, Vmp=41.2,
        KI=0.00675, KV=-0.14442, Ns=144
    )

    model = SingleDiodeModel(params)
    model.fit()
    mpp = model.get_mpp(G_poa=800, T_cell=35)
"""
from models.base import PVModel, ModuleParams, MPPResult
from models.simplified import SimplifiedModel
from models.single_diode import SingleDiodeModel
from models.two_diode import TwoDiodeModel

__all__ = [
    "PVModel", "ModuleParams", "MPPResult",
    "SimplifiedModel", "SingleDiodeModel", "TwoDiodeModel",
]