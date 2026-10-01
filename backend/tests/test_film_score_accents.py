"""Accent repair for aligned film-score sync cues."""

from __future__ import annotations

from app.composition_schemas import CompositionV2
from app.film_score_schemas import FilmCueSnapshot, FilmDensityRegion, FilmHitAlignment, FilmScorePlanV1, FilmSyncOrigin, FilmTempoStrategy
from app.services.composition_timeline import compile_timeline
from app.services.film_score_accents import apply_film_score_accents
from app.services.video_spotting import verify_cue_landings
from app.video_scoring_schemas import HitPointV1


def _plan(*, alignments: list[FilmHitAlignment], regions: list[FilmDensityRegion] | None = None) -> FilmScorePlanV1:
    return FilmScorePlanV1(
        project_id="proj_film",
        source_fingerprint="source-fingerprint",
        scoring_document_revision=1,
        target_duration_seconds=180.0,
        music_start_seconds=0.0,
        music_end_seconds=180.0,
        sync_origin=FilmSyncOrigin(video_origin_seconds=0.0, musical_origin_tick=0),
        time_signature="4/4",
        key="C major",
        root_tempo=120,
        tempo_min=96,
        tempo_max=132,
        sections=[
            {
                "id": "sec_01",
                "label": "Section 1",
                "start_bar": 1,
                "bar_count": 8,
                "density": "moderate",
                "tempo_bpm": 120,
                "start_video_seconds": 0.0,
                "end_video_seconds": 16.0,
            }
        ],
        tempo_strategy=FilmTempoStrategy(changes=[]),
        hit_alignments=alignments,
        density_regions=regions or [],
    )


def _composition(*, start_tick: int, duration_ticks: int) -> CompositionV2:
    return CompositionV2.model_validate(
        {
            "schema_version": "composition.v2",
            "tempo": 120,
            "key": "C major",
            "time_signature": "4/4",
            "ticks_per_quarter": 480,
            "duration_ticks": 90 * 1920,
            "bar_count": 90,
            "sections": [
                {
                    "type": "verse",
                    "start_bar": 1,
                    "bar_count": 90,
                    "start_tick": 0,
                    "duration_ticks": 90 * 1920,
                }
            ],
            "tracks": [
                {
                    "id": "melody-1",
                    "name": "Melody",
                    "instrument": "acoustic_grand_piano",
                    "role": "melody",
                    "midi_program": 0,
                    "channel": 1,
                    "events": [
                        {
                            "type": "note",
                            "pitch": "E4",
                            "start_tick": start_tick,
                            "duration_ticks": duration_ticks,
                            "velocity": 70,
                        }
                    ],
                }
            ],
            "tempo_changes": [],
        }
    )


def _cue(seconds: float = 10.0) -> FilmCueSnapshot:
    return FilmCueSnapshot(
        id="hit_0000000a",
        kind="hit_point",
        importance="critical",
        video_seconds=seconds,
        tolerance_frames=0,
    )


def _landing(composition, cue: FilmCueSnapshot):
    timeline = compile_timeline(composition)
    hit = HitPointV1(
        id=cue.id,
        kind=cue.kind,
        label="Door",
        video_seconds=cue.video_seconds,
        musical_tick=0,
        tolerance_frames=0,
        importance=cue.importance,
    )
    return verify_cue_landings(
        [hit],
        composition,
        timeline=timeline,
        duration_seconds=180.0,
        frame_rate_numerator=24,
        frame_rate_denominator=1,
        timecode_mode="non_drop",
        start_timecode="00:00:00:00",
        video_origin_seconds=0.0,
        musical_origin_tick=0,
    )[0]


def test_vector_a_accent_lands_the_ten_second_hit() -> None:
    cue = _cue(10.0)
    composition = _composition(start_tick=0, duration_ticks=480)
    plan = _plan(
        alignments=[
            FilmHitAlignment(
                cue_id=cue.id,
                kind="hit_point",
                importance="critical",
                status="aligned",
                target_bar=6,
                delta_seconds=0.0,
                tempo_change_added=False,
            )
        ]
    )
    updated = apply_film_score_accents(
        composition,
        plan,
        [cue],
        frame_rate_numerator=24,
        frame_rate_denominator=1,
        duration_seconds=180.0,
    )
    assert _landing(updated, cue).status == "landed"
    assert updated.tempo == 120
    assert updated.tempo_changes == []


def test_sustain_covering_the_frame_is_missed_until_an_attack() -> None:
    cue = _cue(10.0)
    sustain = _composition(start_tick=4800, duration_ticks=9600)
    assert _landing(sustain, cue).status == "missed"
    plan = _plan(
        alignments=[
            FilmHitAlignment(
                cue_id=cue.id,
                kind="hit_point",
                importance="critical",
                status="aligned",
                target_bar=6,
                delta_seconds=0.0,
                tempo_change_added=False,
            )
        ]
    )
    updated = apply_film_score_accents(
        sustain,
        plan,
        [cue],
        frame_rate_numerator=24,
        frame_rate_denominator=1,
        duration_seconds=180.0,
    )
    assert _landing(updated, cue).status == "landed"


def test_unsatisfiable_and_sparse_bars_gain_no_note() -> None:
    cue = _cue(10.0)
    composition = _composition(start_tick=0, duration_ticks=480)
    before = len(composition.tracks[0].events)
    unsatisfiable = _plan(
        alignments=[
            FilmHitAlignment(
                cue_id=cue.id,
                kind="hit_point",
                importance="critical",
                status="unsatisfiable",
                target_bar=6,
                delta_seconds=0.1,
                tempo_change_added=False,
            )
        ]
    )
    skipped = apply_film_score_accents(
        composition,
        unsatisfiable,
        [cue],
        frame_rate_numerator=24,
        frame_rate_denominator=1,
        duration_seconds=180.0,
    )
    assert len(skipped.tracks[0].events) == before
    sparse = _plan(
        alignments=[
            FilmHitAlignment(
                cue_id=cue.id,
                kind="hit_point",
                importance="critical",
                status="aligned",
                target_bar=6,
                delta_seconds=0.0,
                tempo_change_added=False,
            )
        ],
        regions=[FilmDensityRegion(cue_id="hit_0000000d", start_bar=6, end_bar=6, density="sparse")],
    )
    still = apply_film_score_accents(
        composition,
        sparse,
        [cue],
        frame_rate_numerator=24,
        frame_rate_denominator=1,
        duration_seconds=180.0,
    )
    assert len(still.tracks[0].events) == before
