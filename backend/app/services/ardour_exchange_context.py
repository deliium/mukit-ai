"""Build ``ardour.exchange.session_context.v1`` from companion status.

OSC context enriches live transport/selection. Manifest remains authoritative
for selected-region bar spans. Never writes note events.
"""

from __future__ import annotations

import logging

from app.ardour_companion_settings import load_ardour_companion_settings
from app.ardour_exchange_schemas import ArdourExchangeSessionContextV1
from app.services.ardour_companion_service import get_ardour_companion_status

logger = logging.getLogger(__name__)


def build_exchange_session_context() -> ArdourExchangeSessionContextV1:
    """Snapshot companion-observed fields for exchange UI / ingest enrichment."""
    companion_settings = load_ardour_companion_settings()
    status = get_ardour_companion_status(settings=companion_settings)

    warnings: list[str] = []
    sample_rate = getattr(status, "sample_rate", None)
    # Companion status may not yet expose sample_rate; read from held transport when present.
    if sample_rate is None:
        try:
            from app.services import ardour_companion_service as companion

            session = getattr(companion, "_SESSION", None)
            if session is not None:
                sample_rate = session.transport.state.sample_rate
        except Exception:
            sample_rate = None

    if sample_rate is None and status.connection_state in {"connected", "awaiting_feedback", "stale"}:
        warnings.append("sample_rate_unknown")
        logger.warning(
            "Ardour exchange context sample_rate unknown",
            extra={"code": "ardour_sample_rate_unknown"},
        )

    selected_name = None
    if status.selected_ssid is not None:
        for strip in status.strips:
            if strip.ssid == status.selected_ssid:
                selected_name = strip.name
                break

    stale = bool(status.stale) if hasattr(status, "stale") else status.connection_state == "stale"
    # Prefer explicit stale flag from status document when present.
    if getattr(status, "connection_state", None) == "stale":
        stale = True

    context = ArdourExchangeSessionContextV1(
        connection_state=status.connection_state,  # type: ignore[arg-type]
        locate_samples=status.locate_samples,
        transport_playing=status.transport_playing,
        selected_ssid=status.selected_ssid,
        selected_strip_name=selected_name,
        sample_rate=sample_rate if isinstance(sample_rate, int) else None,
        tempo_bpm=None,
        stale=stale,
        warnings=warnings,
    )
    logger.debug(
        "Ardour exchange session context",
        extra={
            "connection_state": context.connection_state,
            "has_locate": context.locate_samples is not None,
            "has_sample_rate": context.sample_rate is not None,
            "has_selected": context.selected_ssid is not None,
        },
    )
    return context
