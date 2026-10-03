"""Pure MusicState update, compression, and continuous guards.

No FastAPI, SQLite, symbolic composer, or playback imports. Never logs harmony
labels or digests at INFO.
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass

from app.adaptive_runtime_music_state_schemas import (
    MUSIC_STATE_CAP_ENERGY,
    MUSIC_STATE_CAP_HARMONY_TRAJECTORY,
    MUSIC_STATE_CAP_MOTIF_USAGE,
    MUSIC_STATE_CAP_ORCHESTRATION,
    MUSIC_STATE_CAP_REPETITION_HISTORY,
    MUSIC_STATE_CAP_TENSION,
    MUSIC_STATE_CAP_THEME_IDS,
    AdaptiveRuntimeMotifUsageV1,
    AdaptiveRuntimeMusicStateV1,
    AdaptiveRuntimeRepetitionEntryV1,
    empty_music_state,
)

logger = logging.getLogger(__name__)

REPETITION_PRESSURE_THRESHOLD = 3
HARMONIC_DEAD_END_TAIL = 4

FALLBACK_REUSE_LOOP = "reuse_loop"
FALLBACK_MOTIF = "motif_variation"
FALLBACK_ACCOMPANIMENT = "accompaniment"


@dataclass(frozen=True)
class ContinuousGuardResult:
    """Deterministic guard outcomes. Never silence transport."""

    guard_flags: tuple[str, ...]
    fallback_order: tuple[str, str, str]
    seed_bump: int
    hard_reject_duplicate: bool


def compress_music_state(state: AdaptiveRuntimeMusicStateV1) -> AdaptiveRuntimeMusicStateV1:
    """Drop oldest ring entries past fixed caps. Deterministic."""
    themes = list(state.active_theme_ids[-MUSIC_STATE_CAP_THEME_IDS:])
    # Themes are a set-like field; keep uniqueness and cap from the left.
    unique_themes: list[str] = []
    for theme in themes:
        if theme not in unique_themes:
            unique_themes.append(theme)
    unique_themes = unique_themes[:MUSIC_STATE_CAP_THEME_IDS]

    motif_usage = list(state.motif_usage[-MUSIC_STATE_CAP_MOTIF_USAGE:])
    harmony = list(state.harmony_trajectory[-MUSIC_STATE_CAP_HARMONY_TRAJECTORY:])
    repetition = list(state.repetition_history[-MUSIC_STATE_CAP_REPETITION_HISTORY:])
    energy = list(state.energy[-MUSIC_STATE_CAP_ENERGY:])
    tension = list(state.tension[-MUSIC_STATE_CAP_TENSION:])
    orchestration = list(state.orchestration_history[-MUSIC_STATE_CAP_ORCHESTRATION:])
    flags = list(dict.fromkeys(state.guard_flags))[:8]

    compressed = AdaptiveRuntimeMusicStateV1(
        active_theme_ids=unique_themes,
        motif_usage=motif_usage,
        harmony_trajectory=harmony,
        repetition_history=repetition,
        energy=energy,
        tension=tension,
        orchestration_history=orchestration,
        runtime_state_id=state.runtime_state_id,
        summary_digest=None,
        virtual_bar=max(1, state.virtual_bar),
        guard_flags=flags,  # type: ignore[arg-type]
    )
    digest = music_state_summary_digest(compressed)
    return compressed.model_copy(update={"summary_digest": digest})


def music_state_summary_digest(state: AdaptiveRuntimeMusicStateV1) -> str:
    """16 hex chars over canonical core fields. Callers must not log this."""
    payload = {
        "active_theme_ids": list(state.active_theme_ids),
        "motif_usage": [
            {
                "motif_id": item.motif_id,
                "use_count": item.use_count,
                "last_virtual_bar": item.last_virtual_bar,
            }
            for item in state.motif_usage
        ],
        "harmony_trajectory": list(state.harmony_trajectory),
        "repetition_history": [
            {"digest16": item.digest16, "count": item.count}
            for item in state.repetition_history
        ],
        "energy": [round(float(value), 6) for value in state.energy],
        "tension": [round(float(value), 6) for value in state.tension],
        "orchestration_history": list(state.orchestration_history),
        "runtime_state_id": state.runtime_state_id,
        "virtual_bar": state.virtual_bar,
        "guard_flags": list(state.guard_flags),
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:16]


def next_virtual_bar(
    *,
    current_virtual_bar: int,
    bar_count: int,
    advanced: bool,
) -> int:
    """Advance the continuous anchor deterministically."""
    base = max(bar_count + 1, max(1, current_virtual_bar))
    if not advanced:
        return base
    return base + 1


def update_music_state(
    previous: AdaptiveRuntimeMusicStateV1 | None,
    *,
    prefix_digest: str | None,
    theme_ids: list[str],
    motif_ids: list[str] | None = None,
    harmony_label: str | None,
    intensity: float,
    tension: float | None = None,
    orchestration_fingerprint: str | None,
    runtime_state_id: str | None,
    virtual_bar: int,
    score_theme_ids: list[str] | None = None,
) -> AdaptiveRuntimeMusicStateV1:
    """Merge one maintain into MusicState rings, then compress."""
    prior = previous if previous is not None else empty_music_state(virtual_bar=virtual_bar)
    themes = list(dict.fromkeys([*prior.active_theme_ids, *[t.strip() for t in theme_ids if t.strip()]]))

    motif_usage = list(prior.motif_usage)
    for motif_id in motif_ids or []:
        cleaned = motif_id.strip()
        if not cleaned:
            continue
        found = False
        updated: list[AdaptiveRuntimeMotifUsageV1] = []
        for entry in motif_usage:
            if entry.motif_id == cleaned:
                updated.append(
                    AdaptiveRuntimeMotifUsageV1(
                        motif_id=cleaned,
                        use_count=entry.use_count + 1,
                        last_virtual_bar=max(1, virtual_bar),
                    )
                )
                found = True
            else:
                updated.append(entry)
        if not found:
            updated.append(
                AdaptiveRuntimeMotifUsageV1(
                    motif_id=cleaned,
                    use_count=1,
                    last_virtual_bar=max(1, virtual_bar),
                )
            )
        motif_usage = updated

    harmony = list(prior.harmony_trajectory)
    if harmony_label is not None:
        label = harmony_label.strip()[:32]
        if label:
            harmony.append(label)

    repetition = list(prior.repetition_history)
    if prefix_digest:
        digest = prefix_digest.strip().lower()[:16]
        if len(digest) == 16:
            matched = False
            rebuilt: list[AdaptiveRuntimeRepetitionEntryV1] = []
            for entry in repetition:
                if entry.digest16 == digest:
                    rebuilt.append(
                        AdaptiveRuntimeRepetitionEntryV1(
                            digest16=digest,
                            count=entry.count + 1,
                        )
                    )
                    matched = True
                else:
                    rebuilt.append(entry)
            if not matched:
                rebuilt.append(AdaptiveRuntimeRepetitionEntryV1(digest16=digest, count=1))
            repetition = rebuilt

    energy = [*prior.energy, max(0.0, min(1.0, float(intensity)))]
    tension_ring = list(prior.tension)
    if tension is not None:
        tension_ring.append(max(0.0, min(1.0, float(tension))))

    orchestration = list(prior.orchestration_history)
    if orchestration_fingerprint:
        token = orchestration_fingerprint.strip().lower()[:16]
        if len(token) == 16:
            orchestration.append(token)

    # Cap to newest before schema validate (extra=forbid models reject oversize).
    draft = AdaptiveRuntimeMusicStateV1(
        active_theme_ids=themes[-MUSIC_STATE_CAP_THEME_IDS:],
        motif_usage=motif_usage[-MUSIC_STATE_CAP_MOTIF_USAGE:],
        harmony_trajectory=harmony[-MUSIC_STATE_CAP_HARMONY_TRAJECTORY:],
        repetition_history=repetition[-MUSIC_STATE_CAP_REPETITION_HISTORY:],
        energy=energy[-MUSIC_STATE_CAP_ENERGY:],
        tension=tension_ring[-MUSIC_STATE_CAP_TENSION:],
        orchestration_history=orchestration[-MUSIC_STATE_CAP_ORCHESTRATION:],
        runtime_state_id=runtime_state_id or prior.runtime_state_id,
        summary_digest=None,
        virtual_bar=max(1, virtual_bar),
        guard_flags=list(prior.guard_flags),
    )
    compressed = compress_music_state(draft)
    guards = evaluate_continuous_guards(
        compressed,
        score_theme_ids=score_theme_ids,
        last_applied_digest=None,
        candidate_digest=None,
    )
    with_flags = compressed.model_copy(update={"guard_flags": list(guards.guard_flags)})
    return compress_music_state(with_flags)


def evaluate_continuous_guards(
    music_state: AdaptiveRuntimeMusicStateV1,
    *,
    score_theme_ids: list[str] | None = None,
    last_applied_digest: str | None = None,
    candidate_digest: str | None = None,
) -> ContinuousGuardResult:
    """Soft reorder / seed bump; hard-reject only on duplicate apply digest."""
    flags: list[str] = []
    seed_bump = 0

    max_rep = 0
    if music_state.repetition_history:
        max_rep = max(entry.count for entry in music_state.repetition_history)
    if max_rep >= REPETITION_PRESSURE_THRESHOLD:
        flags.append("repetition_pressure")
        seed_bump += 1

    score_themes = {item.strip() for item in (score_theme_ids or []) if item.strip()}
    active = set(music_state.active_theme_ids)
    motif_nonempty = bool(music_state.motif_usage)
    if (not active and motif_nonempty) or (
        score_themes and active and active.isdisjoint(score_themes)
    ):
        flags.append("theme_drift")
        seed_bump += 1

    trajectory = music_state.harmony_trajectory
    if len(trajectory) >= HARMONIC_DEAD_END_TAIL:
        tail = trajectory[-HARMONIC_DEAD_END_TAIL:]
        if len(set(tail)) == 1:
            flags.append("harmonic_dead_end")
            seed_bump += 1

    hard_reject = False
    if (
        last_applied_digest
        and candidate_digest
        and last_applied_digest.strip().lower() == candidate_digest.strip().lower()
    ):
        hard_reject = True

    if "repetition_pressure" in flags:
        order = (FALLBACK_MOTIF, FALLBACK_ACCOMPANIMENT, FALLBACK_REUSE_LOOP)
    elif "theme_drift" in flags:
        order = (FALLBACK_MOTIF, FALLBACK_ACCOMPANIMENT, FALLBACK_REUSE_LOOP)
    elif "harmonic_dead_end" in flags:
        order = (FALLBACK_ACCOMPANIMENT, FALLBACK_MOTIF, FALLBACK_REUSE_LOOP)
    else:
        order = (FALLBACK_REUSE_LOOP, FALLBACK_MOTIF, FALLBACK_ACCOMPANIMENT)

    unique_flags = tuple(dict.fromkeys(flags))
    logger.debug(
        "Evaluated continuous MusicState guards",
        extra={
            "guard_flags": list(unique_flags),
            "seed_bump": seed_bump,
            "hard_reject_duplicate": hard_reject,
            "virtual_bar": music_state.virtual_bar,
        },
    )
    return ContinuousGuardResult(
        guard_flags=unique_flags,
        fallback_order=order,
        seed_bump=seed_bump,
        hard_reject_duplicate=hard_reject,
    )
