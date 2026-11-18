"""Compare scalar and batched HR correction on identical full-resolution data.

Run: python scripts/benchmark_hr_recalculation.py --repeats 3
An authorized private {catalog, immutable, previous} JSON replay can be supplied
with --replay-file. No identities, recordings, credentials or data mutations are
emitted. Times include decoding, calibration and lossless encoding; network and
downstream capacity calculations are excluded. Exact logical equality required.
"""
import argparse
import json
from pathlib import Path
import statistics
import sys
from time import perf_counter
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from apps.api.activity_shadow_pipeline import compute_activity_shadow
from apps.api.hr_recalculation import recalibrate_shadow, _detail
from apps.api.shadow_storage import decode_shadow_payload, encode_shadow_payload
from biathlon.hr_zones import bounds_from_hrmax
from hrmod_lab import wave_detection_v4 as detection
from intervals_inspector.stream_normalizer import NormalizerInput, normalize_stream_intervals


def scalar(t, y, config):
    return tuple(np.asarray(column) for column in zip(*[
        detection._weighted_local_line(t, y, float(center), config) for center in t], strict=True))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--replay-file", type=Path)
    parser.add_argument("--repeats", type=int, default=3)
    args = parser.parse_args()
    if args.repeats < 1:
        parser.error("repeats must be positive")
    settings = SimpleNamespace(hrmax_bpm=187, zone_bounds_bpm=bounds_from_hrmax(187, (45,72,79,89,94,100)))
    if args.replay_file:
        sample = json.loads(args.replay_file.read_text())
        detail = _detail(sample["catalog"])
    else:
        offsets = list(range(7200))
        normalized = normalize_stream_intervals(NormalizerInput(offsets=offsets, metrics={
            "heartrate": [140 + 30 * np.sin(t / 70) for t in offsets],
            "velocity_smooth": [4.] * len(offsets), "gradient": [0.] * len(offsets)}))
        detail = {"type": "Run", "start_date": "2026-11-01T08:00:00Z", "elapsed_time": 7200, "moving_time": 7200}
        immutable, previous = compute_activity_shadow(detail=detail, normalized=normalized,
            zone_bounds_bpm=bounds_from_hrmax(193, (45,72,79,89,94,100)), explicit_hrmax_bpm=193)
        sample = {"immutable": immutable, "previous": encode_shadow_payload(previous)}
    optimized = detection._weighted_local_lines
    timings = {"scalar": [], "batched": []}
    expected = None
    for _ in range(args.repeats):
        for label, implementation in (("scalar", scalar), ("batched", optimized)):
            with patch.object(detection, "_weighted_local_lines", implementation):
                started = perf_counter()
                previous = decode_shadow_payload(sample["previous"])
                result = recalibrate_shadow(sample["immutable"], previous, detail=detail, settings=settings)
                encoded = encode_shadow_payload(result)
                timings[label].append(perf_counter() - started)
            if expected is None:
                expected = result
            assert result == expected, "Logical calibration results changed"
            assert decode_shadow_payload(encoded) == expected, "Storage roundtrip changed"
    medians = {key: round(statistics.median(value), 4) for key, value in timings.items()}
    print(json.dumps({"samples": len(sample["immutable"]["samples"]), "repeats": args.repeats,
                      "seconds": medians, "speedup": round(medians["scalar"] / medians["batched"], 2),
                      "exact_result_match": True}))


if __name__ == "__main__":
    main()
