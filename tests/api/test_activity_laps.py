from copy import deepcopy
from datetime import datetime, timezone
import gzip
import struct

import fitdecode
import pytest

from apps.api.activity_catalog import extract_activity_metadata
from apps.api.activity_laps import enrich_activity_laps
from apps.api.device_laps import load_device_laps, parse_device_laps
from intervals_inspector.intervals_client import IntervalsAPIError, IntervalsResponse


def original_fit():
    """Two actual FIT lap messages: work, then recovery with timer paused."""
    origin = int((datetime(2026, 11, 21, 8, tzinfo=timezone.utc) - datetime(1989, 12, 31, tzinfo=timezone.utc)).total_seconds())
    fields = [(2, 4, 0x86), (7, 4, 0x86), (8, 4, 0x86), (9, 4, 0x86),
              (13, 2, 0x84), (15, 1, 2), (16, 1, 2), (23, 1, 0)]
    definition = bytes([0x40, 0, 0]) + struct.pack('<H', 19) + bytes([len(fields)]) + b''.join(bytes(f) for f in fields)
    data = definition + b'\x00' + struct.pack('<IIIIHBBB', origin, 6400, 6400, 2200, 3500, 150, 170, 0)
    data += b'\x00' + struct.pack('<IIIIHBBB', origin+7, 10000, 8000, 0, 0, 120, 130, 1)
    header = struct.pack('<BBHI4s', 12, 0x20, 2100, len(data), b'.FIT')
    return header + data + struct.pack('<H', fitdecode.utils.compute_crc(header+data))


DETAIL = {"id": "i123", "start_date": "2026-11-21T08:00:00Z", "elapsed_time": 17,
          "distance": 22, "file_type": "fit", "source": "GARMIN_CONNECT", "icu_sync_date": "2026-11-21"}


def test_original_fit_keeps_decimal_times_pauses_zero_speed_and_recovery():
    laps = parse_device_laps(gzip.compress(original_fit()), DETAIL)
    assert len(laps) == 2
    assert laps[0]["timer_time_s"] == 6.4
    assert laps[0]["end_time_s"] == 6.4
    assert laps[0]["average_speed_kmh"] == pytest.approx(12.6)
    assert laps[1]["start_time_s"] == 7
    assert laps[1]["elapsed_time_s"] == 10
    assert laps[1]["timer_time_s"] == 8
    assert laps[1]["average_speed_kmh"] == 0
    assert laps[1]["intensity"] == "REST"


def test_file_summary_is_reused_until_the_original_changes():
    class Client:
        calls = 0
        def get_activity_file_result(self, activity_id):
            self.calls += 1
            return IntervalsResponse(200, original_fit())
    client = Client()
    first = load_device_laps(client, DETAIL)
    assert first["status"] == "available"
    assert load_device_laps(client, DETAIL, [first]) == first
    assert client.calls == 1
    load_device_laps(client, {**DETAIL, "icu_sync_date": "2026-11-22"}, [first])
    assert client.calls == 2


@pytest.mark.parametrize("status,expected", [(403, "unavailable"), (404, "unavailable"), (429, "retry"), (503, "retry")])
def test_optional_file_failure_keeps_regular_activity_available(status, expected):
    class Client:
        def get_activity_file_result(self, activity_id):
            raise IntervalsAPIError("provider unavailable", status_code=status)
    assert load_device_laps(Client(), DETAIL)["status"] == expected


@pytest.mark.parametrize("data", [gzip.compress(b"original-fit")[:-8],
                                   b"\x1f\x8b\x08\x00\x00\x00\x00\x00\x00\xff\x07"])
def test_malformed_gzip_does_not_abort_activity_refresh(data):
    class Client:
        def get_activity_file_result(self, activity_id):
            return IntervalsResponse(200, data)
    assert load_device_laps(Client(), DETAIL)["status"] == "retry"


def test_icu_intervals_keep_explicit_times_and_do_not_treat_indices_as_seconds():
    detail = {**DETAIL, "type": "Run", "icu_intervals": [
        {"start_index": 100, "end_index": 103, "start_time": 200, "end_time": 206,
         "type": "RECOVERY", "average_speed": 0, "label": "Rest"}]}
    intervals = extract_activity_metadata("act_"+"1"*32, detail)["intervals"]
    assert intervals[0]["start_time_s"] == 200
    assert intervals[0]["average_speed_kmh"] == 0
    assert intervals[0]["source"] == "intervals"


def test_smart_recording_is_integrated_and_split_at_fractional_lap_boundary():
    catalog = {"elapsed_time_s": 4, "intervals": [
        {"source": "device_laps", "status": "available", "laps": [
            {"source": "device", "start_time_s": 0, "end_time_s": 1.5, "average_hr_bpm": 151},
            {"source": "device", "start_time_s": 1.5, "end_time_s": 4},
        ]}]}
    shadow = {"timeseries": [
        {"elapsed_s": 0, "hr_raw_bpm": 100, "hrmod_final_bpm": 110,
         "hr_interval_classification": "smart_recording", "hr_interval_end_s": 4},
        {"elapsed_s": 4, "hr_raw_bpm": 140, "hrmod_final_bpm": 150},
    ], "speed_test_series": [
        {"elapsed_s": i, "dt_s": 1, "speed_raw_kmh": 10, "vflat_b65_kmh": 12}
        for i in range(1, 5)]}
    before = deepcopy((catalog, shadow))
    laps, status = enrich_activity_laps(catalog, shadow)
    assert status == "available"
    assert laps[0]["average_hr_bpm"] == 151  # preserve the original watch summary
    assert laps[0]["comparison_hr_bpm"] == pytest.approx(107.5)
    assert laps[0]["average_hrmod_bpm"] == pytest.approx(117.5)
    assert laps[1]["average_hrmod_bpm"] == pytest.approx(137.5)
    assert sum(lap["hr_comparison_seconds"] for lap in laps) == 4
    assert sum(lap["speed_comparison_seconds"] for lap in laps) == 4
    assert laps[0]["speed_comparison_seconds"] == 1.5
    assert laps[0]["average_vflat_kmh"] == 12
    assert (catalog, shadow) == before


def test_stops_missing_hr_and_excluded_speed_never_become_model_evidence():
    catalog = {"elapsed_time_s": 20, "intervals": [{"start_time_s": 0, "end_time_s": 20}]}
    shadow = {"timeseries": [
        {"elapsed_s": 0, "hr_raw_bpm": 140, "hrmod_final_bpm": 150, "hr_interval_classification": "recording_stop", "hr_interval_end_s": 18},
        {"elapsed_s": 18, "hr_raw_bpm": None, "hrmod_final_bpm": 150},
        {"elapsed_s": 19, "hr_raw_bpm": 140, "hrmod_final_bpm": 150},
    ], "speed_test_series": [
        {"elapsed_s": 19, "dt_s": 1, "speed_raw_kmh": 0, "vflat_b65_kmh": 0},
        {"elapsed_s": 20, "dt_s": 1, "speed_raw_kmh": 10, "vflat_b65_kmh": 99, "exclusion_reason": "VFLAT_SAMPLE_EXCLUDED"},
    ]}
    lap = enrich_activity_laps(catalog, shadow)[0][0]
    assert lap["hr_comparison_seconds"] == 1
    assert lap["speed_comparison_seconds"] == 1
    assert lap["average_vflat_kmh"] == 0
    assert lap["comparison_speed_kmh"] == 0


def test_missing_bounds_or_models_do_not_fabricate_derived_values():
    catalog = {"elapsed_time_s": 10, "intervals": [{"start_index": 0, "end_index": 5,
                                                  "average_hr_bpm": 150, "elapsed_time_s": 10}]}
    lap = enrich_activity_laps(catalog, None)[0][0]
    assert not lap["has_time_bounds"]
    assert lap["average_hr_bpm"] == 150
    assert lap["average_hrmod_bpm"] is None
    assert lap["average_vflat_kmh"] is None


def test_tcx_original_laps_without_trackpoints_do_not_invent_elapsed_bounds():
    data = b'''<TrainingCenterDatabase xmlns="http://www.garmin.com/xmlschemas/TrainingCenterDatabase/v2"><Activities><Activity><Lap StartTime="2026-11-21T08:00:00Z"><TotalTimeSeconds>6.4</TotalTimeSeconds><DistanceMeters>22</DistanceMeters><AverageHeartRateBpm><Value>150</Value></AverageHeartRateBpm></Lap></Activity></Activities></TrainingCenterDatabase>'''
    lap = parse_device_laps(data, DETAIL)[0]
    assert lap["elapsed_time_s"] is None
    assert lap["end_time_s"] is None
    assert lap["timer_time_s"] == 6.4
    assert lap["average_hr_bpm"] == 150


def test_activity_end_clips_a_smart_span_without_changing_its_slope():
    catalog = {"elapsed_time_s": 2, "intervals": [{"start_time_s": 0, "end_time_s": 2}]}
    shadow = {"timeseries": [
        {"elapsed_s": 0, "hr_raw_bpm": 100, "hrmod_final_bpm": 110,
         "hr_interval_classification": "smart_recording", "hr_interval_end_s": 4},
        {"elapsed_s": 4, "hr_raw_bpm": 140, "hrmod_final_bpm": 150},
    ]}
    lap = enrich_activity_laps(catalog, shadow)[0][0]
    assert lap["average_hr_bpm"] == pytest.approx(110)
    assert lap["average_hrmod_bpm"] == pytest.approx(120)
    assert lap["hr_comparison_seconds"] == 2
