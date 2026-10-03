"""Session context enrichment from companion status."""

from __future__ import annotations

from app.services.ardour_exchange_context import build_exchange_session_context
from app.services.ardour_companion_service import shutdown_ardour_companion


def test_context_when_companion_disabled() -> None:
    shutdown_ardour_companion()
    context = build_exchange_session_context()
    assert context.schema_version == "ardour.exchange.session_context.v1"
    assert context.connection_state in {"disabled", "disconnected"}
    # Context never invents note material.
    assert not hasattr(context, "events")
