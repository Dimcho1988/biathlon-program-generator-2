"""Cycling flat-equivalent speed from a steady mechanical power balance.

Reference total mass, rolling resistance and drag are assumptions. No measured
watts are required. No B65 terrain factors, skiing inertia, or running table.
"""
from dataclasses import asdict, dataclass
import numpy as np
import pandas as pd
from .core import _rolling_median

MODEL_VERSION = "vflat_cycling_mechanical_v1"
CONFIG_VERSION = "vflat_cycling_80kg_crr005_cda032_v1"


@dataclass(frozen=True)
class CyclingConfig:
    total_mass_kg: float = 80.
    crr: float = .005
    cda_m2: float = .32
    air_density_kg_m3: float = 1.225
    gravity_m_s2: float = 9.81
    min_speed_kmh: float = 5.
    min_grade_pct: float = -3.
    max_grade_pct: float = 30.
    post_descent_s: int = 20
    output_smoothing_s: int = 21

    def __post_init__(self):
        for value in (self.total_mass_kg,self.crr,self.cda_m2,self.air_density_kg_m3,self.gravity_m_s2):
            if isinstance(value,bool) or not np.isfinite(value) or value <= 0:
                raise ValueError("Cycling parameters must be finite and positive")

    def to_dict(self):
        return {**asdict(self), "parameter_basis":"REFERENCE_NOT_INDIVIDUALLY_MEASURED",
                "wind_mps":0., "power_basis":"STEADY_MECHANICAL_ESTIMATE"}


def equivalent_speed(speed_mps, grade_pct, config=None):
    selected = config or CyclingConfig()
    v, grade = np.broadcast_arrays(np.asarray(speed_mps,dtype=float), np.asarray(grade_pct,dtype=float))
    theta = np.arctan(grade/100.)
    drag = .5*selected.air_density_kg_m3*selected.cda_m2
    weight = selected.total_mass_kg*selected.gravity_m_s2
    power = (weight*(np.sin(theta)+selected.crr*np.cos(theta))+drag*v*v)*v
    supported = np.isfinite(v)&(v>=0)&np.isfinite(grade)&(power>=0)
    # The cubic is strictly increasing on nonnegative speed. Bracketing scales
    # to the supplied power, avoiding a hidden maximum equivalent-speed cap.
    lo=np.zeros_like(v);hi=np.cbrt(np.maximum(power,0)/drag)+1
    for _ in range(45):
        mid=(lo+hi)/2
        lower=drag*mid**3+weight*selected.crr*mid < power
        lo=np.where(lower,mid,lo);hi=np.where(lower,hi,mid)
    return np.where(supported,(lo+hi)/2*3.6,np.nan)


def apply_cycling_grade(timeseries, config=None):
    selected = config or CyclingConfig()
    required={"grade_pct","speed_mps","block","turn_flag"}
    if not required.issubset(timeseries):
        raise ValueError("Missing prepared cycling columns")
    out=timeseries.copy(deep=True)
    grade=out.grade_pct.to_numpy(dtype=float);v=out.speed_mps.to_numpy(dtype=float)
    speed=equivalent_speed(v,grade,selected)
    supported=(np.isfinite(speed)&(grade>=selected.min_grade_pct)&(grade<=selected.max_grade_pct)
               &(out.block.to_numpy()>=0)&(v*3.6>=selected.min_speed_kmh)&~out.turn_flag.to_numpy(dtype=bool))
    # Cadence zero, where actually recorded, is evidence of coasting. Missing
    # cadence remains unknown; it is not fabricated or inferred as pedalling.
    if "cadence_rpm" in out:
        cadence=out.cadence_rpm.to_numpy(dtype=float)
        supported &= ~np.isfinite(cadence) | (cadence>0)
    for _,ix in out.groupby("block",sort=False).indices.items():
        recent=pd.Series(grade[ix]<selected.min_grade_pct).rolling(selected.post_descent_s+1,min_periods=1).max().to_numpy(dtype=bool)
        supported[ix] &= ~recent
    final=_rolling_median(pd.Series(np.where(supported,speed,np.nan),index=out.index),out.block,selected.output_smoothing_s)
    final=final.where(supported)
    for field in ("grade_actual_pct","grade_effective_pct","grade_stationary_pct"):
        out[field]=grade
    out["speed_raw_kmh"]=v*3.6
    out["vflat_b65_kmh"]=final  # Legacy transport name; versions specify cycling.
    out["vflat_before_terrain_kmh"]=final
    out["vflat_delta_kmh"]=final-v*3.6
    out["valid"]=supported & np.isfinite(final)
    out["vflat_model_version"]=MODEL_VERSION;out["vflat_config_version"]=CONFIG_VERSION
    out.attrs["terrain_correction"]={"factor":1.,"applied":False,"reason":"CYCLING_MECHANICAL_BALANCE"}
    return out
