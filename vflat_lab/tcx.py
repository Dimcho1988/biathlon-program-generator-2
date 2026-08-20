"""TCX parsing and conservative 1 Hz signal preparation for Vflat Lab."""

from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO
import math
from pathlib import Path
from typing import BinaryIO
import xml.etree.ElementTree as ET

import numpy as np
import pandas as pd
from scipy.signal import savgol_filter

from .core import VFlatConfig, odd_window


@dataclass(frozen=True)
class ParsedTCX:
    filename: str
    metadata: dict[str, object]
    trackpoints: pd.DataFrame


def _child_text(node: ET.Element | None, name: str) -> str | None:
    if node is None:
        return None
    child = node.find(f"{{*}}{name}")
    return child.text if child is not None else None


def _float_or_nan(value: str | None) -> float:
    try:
        return float(value) if value is not None else np.nan
    except (TypeError, ValueError):
        return np.nan


def parse_tcx(source: bytes | bytearray | BinaryIO | str | Path, filename: str | None = None) -> ParsedTCX:
    """Parse Garmin-style TCX without treating smart-recorded points as 1 Hz."""

    if isinstance(source, (str, Path)):
        path = Path(source)
        data = path.read_bytes()
        filename = filename or path.name
    elif isinstance(source, (bytes, bytearray)):
        data = bytes(source)
        filename = filename or "activity.tcx"
    else:
        data = source.read()
        filename = filename or getattr(source, "name", "activity.tcx")

    root = ET.parse(BytesIO(data)).getroot()
    activity = root.find(".//{*}Activity")
    if activity is None:
        raise ValueError("TCX файлът не съдържа Activity.")
    activity_id = _child_text(activity, "Id") or Path(filename).stem
    creator = activity.find("{*}Creator")
    device = _child_text(creator, "Name") or "Неизвестно устройство"
    unit_id = _child_text(creator, "UnitId") or "unknown"

    rows: list[dict[str, object]] = []
    laps = activity.findall("{*}Lap")
    for lap_index, lap in enumerate(laps):
        for point in lap.findall(".//{*}Trackpoint"):
            timestamp = _child_text(point, "Time")
            if not timestamp:
                continue
            position = point.find("{*}Position")
            heart = point.find("{*}HeartRateBpm")
            speed_node = point.find(".//{*}TPX/{*}Speed")
            rows.append(
                {
                    "time": pd.to_datetime(timestamp, utc=True),
                    "distance_m": _float_or_nan(_child_text(point, "DistanceMeters")),
                    "altitude_m": _float_or_nan(_child_text(point, "AltitudeMeters")),
                    "hr_bpm": _float_or_nan(_child_text(heart, "Value")),
                    "tpx_speed_mps": _float_or_nan(speed_node.text if speed_node is not None else None),
                    "lat": _float_or_nan(_child_text(position, "LatitudeDegrees")),
                    "lon": _float_or_nan(_child_text(position, "LongitudeDegrees")),
                    "lap": lap_index,
                }
            )
    frame = pd.DataFrame(rows)
    if frame.empty:
        raise ValueError("TCX файлът не съдържа Trackpoint записи с време.")
    frame = frame.sort_values("time")
    frame = frame.groupby("time", as_index=False).agg(
        distance_m=("distance_m", "max"),
        altitude_m=("altitude_m", "mean"),
        hr_bpm=("hr_bpm", "mean"),
        tpx_speed_mps=("tpx_speed_mps", "mean"),
        lat=("lat", "mean"),
        lon=("lon", "mean"),
        lap=("lap", "max"),
    )
    elapsed_s = (frame.time.iloc[-1] - frame.time.iloc[0]).total_seconds()
    distance = frame.distance_m.dropna()
    distance_km = float(distance.max() - distance.min()) / 1000.0 if not distance.empty else np.nan
    metadata = {
        "filename": filename,
        "activity_id": activity_id,
        "device": device,
        "unit_id": unit_id,
        "trackpoints": int(len(frame)),
        "laps": int(len(laps)),
        "start": frame.time.iloc[0].isoformat(),
        "elapsed_min": elapsed_s / 60.0,
        "distance_km": distance_km,
        "median_recording_interval_s": float(frame.time.diff().dt.total_seconds().dropna().median()),
    }
    return ParsedTCX(filename=filename, metadata=metadata, trackpoints=frame)


def _haversine_step_m(lat: np.ndarray, lon: np.ndarray) -> np.ndarray:
    radians_lat = np.radians(lat)
    radians_lon = np.radians(lon)
    dlat = np.diff(radians_lat)
    dlon = np.diff(radians_lon)
    a = np.sin(dlat / 2) ** 2 + np.cos(radians_lat[:-1]) * np.cos(radians_lat[1:]) * np.sin(dlon / 2) ** 2
    step = 2 * 6_371_000.0 * np.arcsin(np.sqrt(np.clip(a, 0.0, 1.0)))
    return np.r_[0.0, np.where(np.isfinite(step), step, 0.0)]


def _ensure_distance(frame: pd.DataFrame) -> pd.Series:
    distance = frame.distance_m.astype(float).copy()
    if distance.notna().mean() >= 0.80 and distance.nunique(dropna=True) >= 3:
        return distance.interpolate(limit_direction="both")
    if frame.lat.notna().mean() < 0.80 or frame.lon.notna().mean() < 0.80:
        raise ValueError("Липсва достатъчна дистанция или GPS позиция за изчисляване на скоростта.")
    step = _haversine_step_m(frame.lat.to_numpy(float), frame.lon.to_numpy(float))
    return pd.Series(np.cumsum(step), index=frame.index)


def _spatial_grade(distance: np.ndarray, altitude: np.ndarray, smoothing_m: int) -> np.ndarray:
    ok = np.isfinite(distance) & np.isfinite(altitude)
    if ok.sum() < 7:
        return np.full(len(distance), np.nan)
    unique = pd.DataFrame({"d": distance[ok], "a": altitude[ok]}).groupby("d", as_index=False).a.median()
    if unique.d.iloc[-1] - unique.d.iloc[0] < 20:
        return np.full(len(distance), np.nan)
    grid = np.arange(math.floor(unique.d.iloc[0]), math.ceil(unique.d.iloc[-1]) + 1.0, 1.0)
    altitude_grid = np.interp(grid, unique.d, unique.a)
    window = odd_window(int(smoothing_m), len(grid), minimum=7)
    smooth_altitude = savgol_filter(altitude_grid, window, 2, mode="interp") if window >= 5 else altitude_grid
    grade_grid = np.gradient(smooth_altitude, grid) * 100.0
    return np.interp(distance, grid, grade_grid)


def _turn_flag(lat: np.ndarray, lon: np.ndarray, threshold_deg: float) -> np.ndarray:
    if len(lat) < 8 or not np.isfinite(lat).any() or not np.isfinite(lon).any():
        return np.zeros(len(lat), dtype=bool)
    lat_r = np.radians(pd.Series(lat).interpolate(limit_direction="both").to_numpy())
    lon_r = np.radians(pd.Series(lon).interpolate(limit_direction="both").to_numpy())
    delta_lon = np.diff(lon_r)
    y = np.sin(delta_lon) * np.cos(lat_r[1:])
    x = np.cos(lat_r[:-1]) * np.sin(lat_r[1:]) - np.sin(lat_r[:-1]) * np.cos(lat_r[1:]) * np.cos(delta_lon)
    heading = np.unwrap(np.arctan2(y, x))
    smooth = pd.Series(heading).rolling(3, center=True, min_periods=1).median().to_numpy()
    change = np.zeros(len(lat))
    if len(smooth) >= 7:
        change[3:-3] = np.abs(smooth[5:] - smooth[:-5])
    return change > np.radians(threshold_deg)


def _resample_block(frame: pd.DataFrame, block_id: int, config: VFlatConfig, activity_start: pd.Timestamp) -> pd.DataFrame:
    part = frame.sort_values("time").copy()
    source_t = (part.time - part.time.iloc[0]).dt.total_seconds().to_numpy(float)
    end = int(math.floor(source_t[-1]))
    seconds = np.arange(0, end + 1, dtype=float)
    output = pd.DataFrame({"sec_in_block": seconds})
    for column in ("distance_m", "altitude_m", "hr_bpm", "lat", "lon", "lap"):
        values = part[column].astype(float).interpolate(limit_direction="both").to_numpy()
        output[column] = np.interp(seconds, source_t, values)
    output["lap"] = output.lap.round().astype(int)
    output["time"] = part.time.iloc[0] + pd.to_timedelta(seconds, unit="s")
    output["elapsed_s"] = (output.time - activity_start).dt.total_seconds()
    output["block"] = block_id

    relative_distance = output.distance_m.to_numpy(float) - float(output.distance_m.iloc[0])
    output["distance_in_block_m"] = relative_distance
    output["grade_pct"] = _spatial_grade(
        relative_distance,
        output.altitude_m.to_numpy(float),
        config.altitude_smoothing_m,
    )
    window = odd_window(config.speed_smoothing_s, len(output), minimum=7)
    if window >= 5:
        output["speed_mps"] = savgol_filter(relative_distance, window, 2, deriv=1, delta=1.0, mode="interp")
        output["accel_mps2"] = savgol_filter(relative_distance, window, 2, deriv=2, delta=1.0, mode="interp")
    else:
        output["speed_mps"] = np.gradient(relative_distance)
        output["accel_mps2"] = np.gradient(output.speed_mps)
    output["speed_mps"] = output.speed_mps.clip(lower=0.0)
    output["turn_flag"] = _turn_flag(output.lat.to_numpy(), output.lon.to_numpy(), config.turn_threshold_deg)
    edge = min(8, max(2, len(output) // 10))
    output.loc[output.index[:edge], "turn_flag"] = True
    output.loc[output.index[-edge:], "turn_flag"] = True
    return output


def prepare_activity(parsed: ParsedTCX, config: VFlatConfig) -> pd.DataFrame:
    """Split smart recording gaps and resample only inside continuous blocks."""

    raw = parsed.trackpoints.copy().sort_values("time")
    raw["distance_m"] = _ensure_distance(raw)
    delta_t = raw.time.diff().dt.total_seconds()
    delta_d = raw.distance_m.diff()
    new_block = delta_t.isna() | (delta_t > config.max_gap_s) | (delta_t <= 0) | (delta_d < -5)
    raw["source_block"] = new_block.cumsum()
    parts: list[pd.DataFrame] = []
    for block_id, (_, block) in enumerate(raw.groupby("source_block", sort=True), start=1):
        duration = (block.time.iloc[-1] - block.time.iloc[0]).total_seconds()
        if len(block) < 5 or duration < 15:
            continue
        parts.append(_resample_block(block, block_id, config, raw.time.iloc[0]))
    if not parts:
        raise ValueError("Няма непрекъснат участък с поне 15 секунди валидни данни.")
    output = pd.concat(parts, ignore_index=True)
    output["filename"] = parsed.filename
    return output
