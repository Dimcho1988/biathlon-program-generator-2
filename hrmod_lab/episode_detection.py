"""Deterministic HR-only correction-lobe and response-episode detection."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .schemas import HRmodConfig


@dataclass(frozen=True, slots=True)
class CorrectionLobe:
    lobe_id: int
    segment_id: int
    start_index: int
    end_index: int
    sign: int
    duration_s: float
    area_bpm_s: float


@dataclass(frozen=True, slots=True)
class DetectedEpisode:
    episode_id: int
    segment_id: int
    start_index: int
    end_index: int
    state: str
    complete: bool
    incomplete_reason: str | None
    lobes: tuple[CorrectionLobe, ...]
    flags: tuple[str, ...]

    @property
    def positive_lobe_count(self) -> int:
        return sum(lobe.sign > 0 for lobe in self.lobes)

    @property
    def negative_lobe_count(self) -> int:
        return sum(lobe.sign < 0 for lobe in self.lobes)


@dataclass(frozen=True, slots=True)
class EpisodeDetectionResult:
    episodes: tuple[DetectedEpisode, ...]
    lobes: tuple[CorrectionLobe, ...]
    thresholded_correction: np.ndarray
    suppressed_lobe_mask: np.ndarray
    episode_ids: np.ndarray
    episode_states: tuple[str, ...]


def _lobe_duration(dt_s: np.ndarray, start: int, end: int) -> float:
    return float(np.sum(np.maximum(dt_s[start : end + 1], 0.0)))


def _make_episode(
    episode_id: int,
    lobes: list[CorrectionLobe],
    *,
    complete: bool,
    state: str,
    reason: str | None,
) -> DetectedEpisode:
    flags: list[str] = []
    if state == "INCOMPLETE_START":
        flags.append("INCOMPLETE_EPISODE_START")
    elif state == "INCOMPLETE_END":
        flags.append("INCOMPLETE_EPISODE_END")
    return DetectedEpisode(
        episode_id=episode_id,
        segment_id=lobes[0].segment_id,
        start_index=lobes[0].start_index,
        end_index=lobes[-1].end_index,
        state=state,
        complete=complete,
        incomplete_reason=reason,
        lobes=tuple(lobes),
        flags=tuple(flags),
    )


def detect_response_episodes(
    *,
    elapsed_s: np.ndarray,
    dt_s: np.ndarray,
    segment_ids: np.ndarray,
    raw_correction: np.ndarray,
    config: HRmodConfig,
) -> EpisodeDetectionResult:
    """Detect mathematical response windows solely from raw HR correction shape."""

    elapsed_s = np.asarray(elapsed_s, dtype=float)
    dt_s = np.asarray(dt_s, dtype=float)
    segment_ids = np.asarray(segment_ids, dtype=int)
    raw_correction = np.asarray(raw_correction, dtype=float)
    count = len(raw_correction)
    if not (len(elapsed_s) == len(dt_s) == len(segment_ids) == count):
        raise ValueError("episode detection inputs must have equal lengths")

    thresholded = np.zeros(count, dtype=float)
    eligible = np.isfinite(raw_correction) & (segment_ids >= 0)
    outside_deadband = eligible & (
        np.abs(raw_correction) > config.correction_deadband_bpm
    )
    thresholded[outside_deadband] = raw_correction[outside_deadband]
    suppressed_mask = np.zeros(count, dtype=bool)

    candidate_lobes: list[CorrectionLobe] = []
    lobe_counter = 1
    for segment_id in sorted({int(value) for value in segment_ids if value >= 0}):
        indices = np.flatnonzero(segment_ids == segment_id)
        position = 0
        while position < len(indices):
            index = int(indices[position])
            value = thresholded[index]
            if value == 0.0:
                position += 1
                continue
            sign = 1 if value > 0.0 else -1
            start_position = position
            position += 1
            while position < len(indices):
                next_index = int(indices[position])
                next_value = thresholded[next_index]
                if next_value == 0.0 or (1 if next_value > 0.0 else -1) != sign:
                    break
                # Segment indices are normally consecutive; retain the guard so
                # an invalid HR row can never be bridged implicitly.
                if next_index != int(indices[position - 1]) + 1:
                    break
                position += 1
            start = int(indices[start_position])
            end = int(indices[position - 1])
            duration = _lobe_duration(dt_s, start, end)
            area = float(np.sum(np.abs(thresholded[start : end + 1]) * dt_s[start : end + 1]))
            lobe = CorrectionLobe(
                lobe_id=lobe_counter,
                segment_id=segment_id,
                start_index=start,
                end_index=end,
                sign=sign,
                duration_s=duration,
                area_bpm_s=area,
            )
            lobe_counter += 1
            if (
                duration + 1e-12 < config.min_lobe_duration_s
                or area + 1e-12 < config.min_lobe_area_bpm_s
            ):
                thresholded[start : end + 1] = 0.0
                suppressed_mask[start : end + 1] = True
            else:
                candidate_lobes.append(lobe)

    episodes: list[DetectedEpisode] = []
    next_episode_id = 1
    lobes_by_segment: dict[int, list[CorrectionLobe]] = {}
    for lobe in candidate_lobes:
        lobes_by_segment.setdefault(lobe.segment_id, []).append(lobe)

    for segment_id in sorted(lobes_by_segment):
        segment_lobes = lobes_by_segment[segment_id]
        segment_indices = np.flatnonzero(segment_ids == segment_id)
        segment_end_index = int(segment_indices[-1])
        active: list[CorrectionLobe] = []

        def close_active(
            *, complete: bool, state: str, reason: str | None
        ) -> None:
            nonlocal active, next_episode_id
            if not active:
                return
            episodes.append(
                _make_episode(
                    next_episode_id,
                    active,
                    complete=complete,
                    state=state,
                    reason=reason,
                )
            )
            next_episode_id += 1
            active = []

        for lobe in segment_lobes:
            if not active:
                if lobe.sign < 0:
                    episodes.append(
                        _make_episode(
                            next_episode_id,
                            [lobe],
                            complete=False,
                            state="INCOMPLETE_START",
                            reason="negative_lobe_without_observed_positive_onset",
                        )
                    )
                    next_episode_id += 1
                else:
                    active = [lobe]
                continue

            previous = active[-1]
            neutral_gap_s = max(
                0.0,
                float(elapsed_s[lobe.start_index] - elapsed_s[previous.end_index]),
            )
            has_negative = any(item.sign < 0 for item in active)
            if neutral_gap_s >= config.episode_neutral_gap_s:
                if has_negative:
                    close_active(complete=True, state="COMPLETE", reason=None)
                else:
                    close_active(
                        complete=False,
                        state="INCOMPLETE_END",
                        reason="neutral_gap_before_negative_phase",
                    )
                if lobe.sign < 0:
                    episodes.append(
                        _make_episode(
                            next_episode_id,
                            [lobe],
                            complete=False,
                            state="INCOMPLETE_START",
                            reason="negative_lobe_without_observed_positive_onset",
                        )
                    )
                    next_episode_id += 1
                else:
                    active = [lobe]
                continue

            proposed_duration = float(
                elapsed_s[lobe.end_index] - elapsed_s[active[0].start_index]
            )
            if proposed_duration > config.max_episode_duration_s:
                close_active(
                    complete=False,
                    state="INCOMPLETE_END",
                    reason="max_episode_duration_exceeded",
                )
                if lobe.sign < 0:
                    episodes.append(
                        _make_episode(
                            next_episode_id,
                            [lobe],
                            complete=False,
                            state="INCOMPLETE_START",
                            reason="negative_lobe_after_episode_duration_limit",
                        )
                    )
                    next_episode_id += 1
                else:
                    active = [lobe]
                continue

            active.append(lobe)
            if lobe.sign < 0:
                signed_area = sum(item.sign * item.area_bpm_s for item in active)
                if abs(signed_area) <= config.episode_balance_tolerance_bpm_s:
                    close_active(complete=True, state="COMPLETE", reason=None)

        if active:
            has_negative = any(item.sign < 0 for item in active)
            trailing_neutral_s = max(
                0.0,
                float(elapsed_s[segment_end_index] - elapsed_s[active[-1].end_index]),
            )
            if has_negative and trailing_neutral_s >= config.episode_neutral_gap_s:
                close_active(complete=True, state="COMPLETE", reason=None)
            else:
                close_active(
                    complete=False,
                    state="INCOMPLETE_END",
                    reason=(
                        "segment_ended_before_balance_or_neutral_gap"
                        if has_negative
                        else "segment_ended_before_negative_phase"
                    ),
                )

    episode_ids = np.full(count, -1, dtype=int)
    states = ["NONE"] * count
    for episode in episodes:
        episode_ids[episode.start_index : episode.end_index + 1] = episode.episode_id
        for index in range(episode.start_index, episode.end_index + 1):
            states[index] = episode.state

    return EpisodeDetectionResult(
        episodes=tuple(episodes),
        lobes=tuple(candidate_lobes),
        thresholded_correction=thresholded,
        suppressed_lobe_mask=suppressed_mask,
        episode_ids=episode_ids,
        episode_states=tuple(states),
    )


__all__ = [
    "CorrectionLobe",
    "DetectedEpisode",
    "EpisodeDetectionResult",
    "detect_response_episodes",
]
