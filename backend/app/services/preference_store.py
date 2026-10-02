"""SQLite persistence for preference settings, ballots, choices, and the ranker.

This module does not import ``project_store``, ``composer_profile_store``, or
``personal_composer_store``. It does not write ``composition.v2``.
"""

from __future__ import annotations

import json
import logging
import secrets
from datetime import datetime, timezone
from pathlib import Path

from app.db.connection import get_connection, get_project_db_path
from app.preference_schemas import (
    PreferenceChoiceCandidateV1,
    PreferenceChoiceSummaryV1,
    PreferenceChoiceV1,
    PreferenceLearningError,
    PreferencePendingBallotV1,
    PreferenceRankingV1,
    PreferenceRankerV1,
    PreferenceSettingsV1,
)
from app.preference_settings import preference_learning_enabled
from app.services.composition_snapshot_encoding import snapshot_fingerprint_log_prefix
from app.services.preference_ranker import LinearPairwiseRanker, log_preference_ranker_updated, zero_ranker

logger = logging.getLogger(__name__)

CHOICE_LIMIT = 200
_SETTINGS_ID = 1
_RANKER_ID = 1


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _resolve(db_path: Path | str | None) -> Path:
    return Path(db_path) if db_path is not None else get_project_db_path()


def _dump(model) -> str:
    return json.dumps(model.model_dump(mode="json"), separators=(",", ":"), sort_keys=True)


def _disabled(code: str) -> PreferenceLearningError:
    logger.warning("Preference learning gate closed", extra={"code": code})
    message = {
        "preference_learning_disabled": "Preference learning is disabled.",
        "preference_collection_disabled": "Preference collection is off.",
    }[code]
    return PreferenceLearningError(code, message)


def get_settings(*, db_path: Path | str | None = None) -> PreferenceSettingsV1:
    """Return stored switches. A missing row is the default and is not inserted."""
    path = _resolve(db_path)
    logger.debug("preference settings read", extra={"table": "preference_settings"})
    with get_connection(path) as conn:
        row = conn.execute(
            "SELECT body_json FROM preference_settings WHERE id = ?",
            (_SETTINGS_ID,),
        ).fetchone()
    if row is None:
        logger.debug(
            "preference settings default",
            extra={"collection_enabled": False, "ranking_enabled": False},
        )
        return PreferenceSettingsV1()
    return PreferenceSettingsV1.model_validate(json.loads(row["body_json"]))


def put_settings(
    *,
    collection_enabled: bool,
    ranking_enabled: bool,
    db_path: Path | str | None = None,
) -> PreferenceSettingsV1:
    """Write the two user switches. A true value requires the deployment flag."""
    logger.debug(
        "preference settings write start",
        extra={"collection_enabled": collection_enabled, "ranking_enabled": ranking_enabled},
    )
    if (collection_enabled or ranking_enabled) and not preference_learning_enabled():
        raise _disabled("preference_learning_disabled")
    current = get_settings(db_path=db_path)
    updated = PreferenceSettingsV1(
        collection_enabled=collection_enabled,
        ranking_enabled=ranking_enabled,
        updated_at=_utc_now(),
    )
    path = _resolve(db_path)
    with get_connection(path) as conn:
        conn.execute(
            """
            INSERT INTO preference_settings (id, body_json, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                body_json = excluded.body_json,
                updated_at = excluded.updated_at
            """,
            (_SETTINGS_ID, _dump(updated), updated.updated_at),
        )
    logger.info(
        "preference settings stored",
        extra={
            "collection_enabled": updated.collection_enabled,
            "ranking_enabled": updated.ranking_enabled,
            "previous_collection_enabled": current.collection_enabled,
            "previous_ranking_enabled": current.ranking_enabled,
        },
    )
    return updated


def effective_collection(*, db_path: Path | str | None = None) -> bool:
    """True only when the deployment flag and the user collection switch are on."""
    if not preference_learning_enabled():
        return False
    return get_settings(db_path=db_path).collection_enabled


def effective_ranking(*, db_path: Path | str | None = None) -> bool:
    """True only when the deployment flag and the user ranking switch are on."""
    if not preference_learning_enabled():
        return False
    return get_settings(db_path=db_path).ranking_enabled


def stash_pending(
    ballot: PreferencePendingBallotV1,
    *,
    db_path: Path | str | None = None,
) -> PreferencePendingBallotV1:
    """Replace the pending ballot for this surface."""
    path = _resolve(db_path)
    updated_at = _utc_now()
    prefix = snapshot_fingerprint_log_prefix(ballot.context.source_fingerprint)
    logger.debug(
        "preference pending stash start",
        extra={
            "surface": ballot.context.surface,
            "candidate_count": len(ballot.candidates),
            "snapshot_fingerprint_log_prefix": prefix,
        },
    )
    with get_connection(path) as conn:
        conn.execute(
            """
            INSERT INTO preference_pending (surface, body_json, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(surface) DO UPDATE SET
                body_json = excluded.body_json,
                updated_at = excluded.updated_at
            """,
            (ballot.context.surface, _dump(ballot), updated_at),
        )
    logger.info(
        "preference pending stashed",
        extra={
            "surface": ballot.context.surface,
            "candidate_count": len(ballot.candidates),
            "snapshot_fingerprint_log_prefix": prefix,
        },
    )
    return ballot


def get_pending(
    surface: str,
    *,
    db_path: Path | str | None = None,
) -> PreferencePendingBallotV1 | None:
    """Return the pending ballot for ``surface``, or None when that row is absent."""
    path = _resolve(db_path)
    with get_connection(path) as conn:
        row = conn.execute(
            "SELECT body_json FROM preference_pending WHERE surface = ?",
            (surface,),
        ).fetchone()
    if row is None:
        logger.debug("preference pending miss", extra={"surface": surface})
        return None
    logger.debug("preference pending hit", extra={"surface": surface})
    return PreferencePendingBallotV1.model_validate(json.loads(row["body_json"]))


def _load_ranker(conn) -> PreferenceRankerV1 | None:
    row = conn.execute(
        "SELECT body_json FROM preference_ranker WHERE id = ?",
        (_RANKER_ID,),
    ).fetchone()
    if row is None:
        return None
    return PreferenceRankerV1.model_validate(json.loads(row["body_json"]))


def get_ranker(*, db_path: Path | str | None = None) -> PreferenceRankerV1 | None:
    """Return the stored ranker, or None before the first successful choice."""
    path = _resolve(db_path)
    with get_connection(path) as conn:
        return _load_ranker(conn)


def choice_count(*, db_path: Path | str | None = None) -> int:
    path = _resolve(db_path)
    with get_connection(path) as conn:
        row = conn.execute("SELECT COUNT(*) AS n FROM preference_choices").fetchone()
    return int(row["n"])


def record_choice(
    *,
    surface: str,
    chosen_candidate_id: str,
    db_path: Path | str | None = None,
) -> PreferenceChoiceV1:
    """Insert one choice and the updated ranker, then delete that pending ballot.

    Both writes share one connection. A failure rolls back both.
    """
    logger.debug("preference choice record start", extra={"surface": surface})
    if not preference_learning_enabled():
        raise _disabled("preference_learning_disabled")
    settings = get_settings(db_path=db_path)
    if not settings.collection_enabled:
        raise _disabled("preference_collection_disabled")

    path = _resolve(db_path)
    ranker_impl = LinearPairwiseRanker()
    with get_connection(path) as conn:
        pending_row = conn.execute(
            "SELECT body_json FROM preference_pending WHERE surface = ?",
            (surface,),
        ).fetchone()
        if pending_row is None:
            logger.warning(
                "Preference ballot missing",
                extra={"code": "preference_ballot_missing", "surface": surface},
            )
            raise PreferenceLearningError(
                "preference_ballot_missing",
                "No pending ballot is stored for this surface.",
            )
        pending = PreferencePendingBallotV1.model_validate(json.loads(pending_row["body_json"]))
        known = {item.candidate_id for item in pending.candidates}
        if chosen_candidate_id not in known:
            logger.warning(
                "Preference ballot missing",
                extra={"code": "preference_ballot_missing", "surface": surface},
            )
            raise PreferenceLearningError(
                "preference_ballot_missing",
                "That candidate is not on the pending ballot.",
            )
        count_row = conn.execute("SELECT COUNT(*) AS n FROM preference_choices").fetchone()
        if int(count_row["n"]) >= CHOICE_LIMIT:
            logger.warning("Preference choice limit", extra={"code": "preference_choice_limit"})
            raise PreferenceLearningError(
                "preference_choice_limit",
                "The preference choice log is full.",
            )
        choice = PreferenceChoiceV1(
            id=f"pref_{secrets.token_hex(8)}",
            context=pending.context,
            candidates=[
                PreferenceChoiceCandidateV1(
                    candidate_id=item.candidate_id,
                    candidate_fingerprint=item.candidate_fingerprint,
                    original_index=item.original_index,
                    feature_vector=list(item.feature_vector),
                    chosen=item.candidate_id == chosen_candidate_id,
                )
                for item in pending.candidates
            ],
            chosen_candidate_id=chosen_candidate_id,
            created_at=_utc_now(),
        )
        model = _load_ranker(conn) or zero_ranker()
        updated_ranker = ranker_impl.update(model, choice)
        conn.execute(
            """
            INSERT INTO preference_choices (id, surface, created_at, body_json)
            VALUES (?, ?, ?, ?)
            """,
            (choice.id, choice.context.surface, choice.created_at, _dump(choice)),
        )
        conn.execute(
            """
            INSERT INTO preference_ranker (id, body_json, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                body_json = excluded.body_json,
                updated_at = excluded.updated_at
            """,
            (_RANKER_ID, _dump(updated_ranker), updated_ranker.updated_at),
        )
        conn.execute("DELETE FROM preference_pending WHERE surface = ?", (surface,))
    log_preference_ranker_updated(pair_count=updated_ranker.pair_count, surface=choice.context.surface)
    logger.info(
        "preference choice recorded",
        extra={
            "surface": choice.context.surface,
            "choice_id": choice.id,
            "candidate_count": len(choice.candidates),
            "snapshot_fingerprint_log_prefix": snapshot_fingerprint_log_prefix(
                choice.context.source_fingerprint
            ),
        },
    )
    return choice


def list_choices(
    *,
    limit: int = 20,
    db_path: Path | str | None = None,
) -> list[PreferenceChoiceSummaryV1]:
    """Return summaries, newest first. Feature vectors stay on the detail row."""
    bounded = max(1, min(int(limit), 50))
    path = _resolve(db_path)
    logger.debug("preference choices list", extra={"limit": bounded})
    with get_connection(path) as conn:
        rows = conn.execute(
            """
            SELECT body_json FROM preference_choices
            ORDER BY created_at DESC, id DESC
            LIMIT ?
            """,
            (bounded,),
        ).fetchall()
    summaries: list[PreferenceChoiceSummaryV1] = []
    for row in rows:
        choice = PreferenceChoiceV1.model_validate(json.loads(row["body_json"]))
        summaries.append(
            PreferenceChoiceSummaryV1(
                id=choice.id,
                surface=choice.context.surface,
                operation=choice.context.operation,
                chosen_candidate_id=choice.chosen_candidate_id,
                candidate_count=len(choice.candidates),
                created_at=choice.created_at,
                source_fingerprint_prefix=choice.context.source_fingerprint[:12],
            )
        )
    logger.debug("preference choices listed", extra={"count": len(summaries)})
    return summaries


def get_choice(choice_id: str, *, db_path: Path | str | None = None) -> PreferenceChoiceV1:
    """Return one stored choice, including feature vectors and no composition."""
    path = _resolve(db_path)
    logger.debug("preference choice read", extra={"choice_id": choice_id})
    with get_connection(path) as conn:
        row = conn.execute(
            "SELECT body_json FROM preference_choices WHERE id = ?",
            (choice_id,),
        ).fetchone()
    if row is None:
        logger.info(
            "preference choice missing",
            extra={"choice_id": choice_id, "code": "preference_not_found"},
        )
        raise PreferenceLearningError("preference_not_found", "Preference choice was not found.")
    return PreferenceChoiceV1.model_validate(json.loads(row["body_json"]))


def reset_preference_data(*, db_path: Path | str | None = None) -> int:
    """Delete choices, pending ballots, and the ranker. The settings row stays."""
    path = _resolve(db_path)
    logger.debug("preference reset start", extra={"table": "preference_choices"})
    with get_connection(path) as conn:
        count_row = conn.execute("SELECT COUNT(*) AS n FROM preference_choices").fetchone()
        deleted = int(count_row["n"])
        conn.execute("DELETE FROM preference_choices")
        conn.execute("DELETE FROM preference_pending")
        conn.execute("DELETE FROM preference_ranker")
    logger.info(
        "preference data reset",
        extra={
            "candidate_count": deleted,
            "choice_id": None,
        },
    )
    return deleted


def score_pending(
    *,
    surface: str,
    candidate_ids: list[str],
    db_path: Path | str | None = None,
) -> PreferenceRankingV1:
    """Order ``candidate_ids`` from the pending features when ranking is effective."""
    logger.debug(
        "preference rank start",
        extra={"surface": surface, "candidate_count": len(candidate_ids)},
    )
    inactive = PreferenceRankingV1(
        ranking_applied=False,
        ordered_candidate_ids=list(candidate_ids),
        scores=[0.0] * len(candidate_ids),
    )
    if not effective_ranking(db_path=db_path):
        logger.info(
            "preference rank skipped",
            extra={"surface": surface, "candidate_count": len(candidate_ids), "ranking_applied": False},
        )
        return inactive
    model = get_ranker(db_path=db_path)
    if model is None or model.pair_count == 0:
        logger.info(
            "preference rank cold",
            extra={"surface": surface, "candidate_count": len(candidate_ids), "ranking_applied": False},
        )
        return inactive
    pending = get_pending(surface, db_path=db_path)
    if pending is None:
        raise PreferenceLearningError(
            "preference_ballot_missing",
            "No pending ballot is stored for this surface.",
        )
    by_id = {item.candidate_id: item for item in pending.candidates}
    items = []
    for candidate_id in candidate_ids:
        row = by_id.get(candidate_id)
        if row is None:
            raise PreferenceLearningError(
                "preference_ballot_missing",
                "That candidate is not on the pending ballot.",
            )
        items.append(row)
    ranking = LinearPairwiseRanker().rank(items, model)
    logger.info(
        "preference rank complete",
        extra={
            "surface": surface,
            "candidate_count": len(candidate_ids),
            "ranking_applied": ranking.ranking_applied,
        },
    )
    return ranking
